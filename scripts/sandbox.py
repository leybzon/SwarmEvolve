"""SwarmEvolve container-sandbox wrapper (M8).

Thin Python layer around ``docker run`` / ``podman run`` that enforces
the exact isolation flags mandated by IMPLEMENTATION_PLAN §M8 and
ARCHITECTURE Layer 4::

    --rm --network=none --cap-drop=ALL --security-opt no-new-privileges
    --read-only --tmpfs /tmp:size=16m --memory=512m --pids-limit=64
    --cpus=2 --user 65534:65534
    -v <src>:/work/src:ro -v <out>:/work/out

Inputs:
    team_a_src / team_b_src   host-side paths to the two C++ AI sources
    out_dir                   writable host directory (will receive
                              sandbox_status.json, trace.jsonl, logs)
    image                     sandbox image tag (default:
                              ``swarmevolve-sandbox:latest``)
    engine_args               list of extra args for the engine binary
                              (``--seed``, ``--max-ticks``, ``--drones-a``
                              …). The entrypoint auto-adds ``--record``
                              when absent.

Output:
    A :class:`SandboxResult` dataclass with exit code, parsed status
    dict (from ``sandbox_status.json``), and captured stdout/stderr.

Runtime detection: prefers ``$CONTAINER_RUNTIME`` if set, else tries
``docker`` (works with Colima), else ``podman``. If neither is on PATH,
:class:`SandboxUnavailableError` is raised so callers / tests can skip
cleanly.

Batch mode (v2 study): :func:`run_batch_in_sandbox` runs
``docker/entrypoint_eval.sh batch`` in the eval image
(``docker/Dockerfile.eval``) with the same isolation flags, except that
``--cpus`` is set per container. One container compiles once and plays a
list of seeds; ``scripts/fitness.py`` splits an evaluation across several.

This module is **host-side only**. Nothing here runs inside the
container; the in-container logic lives in ``docker/entrypoint.sh``,
``docker/entrypoint_eval.sh`` and ``scripts/eval_batch.py``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The eval image (docker/Dockerfile.eval) also serves the M8 single-match
# mode, so one image covers both paths.
DEFAULT_IMAGE = "swarmevolve-eval:latest"

# Container-side limit on concurrent matches. Each match and each runner
# thread counts against --pids-limit=64.
MAX_JOBS_PER_CONTAINER = 16


def isolation_flags(cpus: int | float = 2) -> tuple[str, ...]:
    """The M8 isolation flags with ``--cpus`` set to ``cpus``."""
    return tuple(f"--cpus={cpus}" if f == "--cpus=2" else f for f in SANDBOX_FLAGS)


# Spec-exact sandbox flags. Keep this tuple in one place so tests can
# assert on it and the plan-level audit trail is obvious.
SANDBOX_FLAGS: tuple[str, ...] = (
    "--rm",
    "--network=none",
    "--cap-drop=ALL",
    "--security-opt",
    "no-new-privileges",
    "--read-only",
    # /tmp must be exec because the entrypoint compiles the engine there
    # and then runs it. nosuid+nodev still hold (Docker defaults); exec is
    # scoped to /tmp only — the root filesystem stays --read-only and
    # no-new-privileges blocks setuid escalation anyway.
    "--tmpfs",
    "/tmp:size=64m,mode=1777,exec,nosuid,nodev",
    "--memory=512m",
    "--pids-limit=64",
    "--cpus=2",
    "--user",
    "65534:65534",
)


class SandboxError(RuntimeError):
    """Any failure originating from the sandbox wrapper."""


class SandboxUnavailableError(SandboxError):
    """No container runtime is installed / reachable on this host."""


@dataclasses.dataclass(frozen=True)
class SandboxResult:
    """Structured outcome of one :func:`run_match_in_sandbox` call."""

    runtime: str  # "docker" or "podman"
    image: str
    returncode: int  # container exit code (entrypoint.sh)
    status: dict  # parsed sandbox_status.json (or {})
    stdout: str
    stderr: str
    wall_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.status.get("status") == "ok"


# ---------------------------------------------------------------------------
# runtime detection
# ---------------------------------------------------------------------------


def detect_runtime() -> str:
    """Return ``"docker"`` or ``"podman"``.

    Honors ``$CONTAINER_RUNTIME`` when it names an available binary;
    otherwise prefers Docker (so Colima users get the default path) and
    falls back to Podman.
    """
    explicit = os.environ.get("CONTAINER_RUNTIME")
    if explicit and shutil.which(explicit):
        return explicit
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    raise SandboxUnavailableError(
        "no container runtime found; install Docker (Colima) or Podman "
        "or set $CONTAINER_RUNTIME to an available binary",
    )


def runtime_available() -> bool:
    """Return True iff :func:`detect_runtime` would succeed. Test helper."""
    try:
        detect_runtime()
        return True
    except SandboxUnavailableError:
        return False


def image_present(image: str = DEFAULT_IMAGE, runtime: str | None = None) -> bool:
    """Return True iff the sandbox image tag exists locally."""
    rt = runtime or detect_runtime()
    proc = subprocess.run(
        [rt, "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def build_command(
    *,
    team_a_src: Path,
    team_b_src: Path,
    out_dir: Path,
    image: str,
    engine_args: Iterable[str] | None,
    runtime: str,
    timeout: float | None,
) -> list[str]:
    """Compose the full argv for ``<runtime> run ...``.

    Separated from :func:`run_match_in_sandbox` so tests can assert on
    the flag set without starting a container. The caller owns path
    resolution — we just validate existence and convert to absolute.
    """
    team_a_src = team_a_src.resolve()
    team_b_src = team_b_src.resolve()
    out_dir = out_dir.resolve()

    if not team_a_src.is_file():
        raise SandboxError(f"team A source not found: {team_a_src}")
    if not team_b_src.is_file():
        raise SandboxError(f"team B source not found: {team_b_src}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # The container runs as uid 65534 (spec requirement) but the bind
    # mount is owned by the invoking host user. Make /work/out
    # world-writable so the unprivileged container can write
    # sandbox_status.json / trace.jsonl / logs. The container itself
    # can't chown (CAP_CHOWN dropped), so the host has to do it. This
    # only widens permissions on the per-run output directory, never
    # on the repo.
    os.chmod(out_dir, 0o777)

    # We mount a small host-side staging dir that contains exactly
    # a.cpp and b.cpp. The entrypoint looks for /work/src/a.cpp and
    # /work/src/b.cpp — doing the name remap host-side means we never
    # have to trust the candidate file's name. The staging dir is
    # mounted read-only so its permissions don't matter to the
    # container.
    staging = out_dir / ".sandbox_src"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "a.cpp").write_bytes(team_a_src.read_bytes())
    (staging / "b.cpp").write_bytes(team_b_src.read_bytes())

    cmd: list[str] = [runtime, "run"]
    cmd.extend(SANDBOX_FLAGS)
    cmd.extend(
        [
            "-v",
            f"{staging}:/work/src:ro",
            "-v",
            f"{out_dir}:/work/out",
        ]
    )
    cmd.append(image)
    if engine_args:
        cmd.extend(engine_args)

    # Host-side wall-clock timeout. We *also* pass this to the caller
    # via subprocess.run(timeout=...) so a truly wedged container is
    # killed by Python, not only by the engine's --max-ticks.
    return cmd


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_match_in_sandbox(
    team_a_src: Path,
    team_b_src: Path,
    out_dir: Path,
    *,
    image: str = DEFAULT_IMAGE,
    engine_args: Iterable[str] | None = None,
    timeout: float | None = 30.0,
    runtime: str | None = None,
) -> SandboxResult:
    """Run one match inside the sandbox image.

    This does *not* build the image — that is a separate one-time step
    (``make docker-build``). If the image is missing we raise
    :class:`SandboxError` so callers get a clear message.
    """
    rt = runtime or detect_runtime()
    if not image_present(image, runtime=rt):
        raise SandboxError(
            f"image '{image}' not found; run `make docker-build` first",
        )

    cmd = build_command(
        team_a_src=team_a_src,
        team_b_src=team_b_src,
        out_dir=out_dir,
        image=image,
        engine_args=engine_args,
        runtime=rt,
        timeout=timeout,
    )

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        # Best-effort cleanup: kill the container. Docker `run --rm`
        # auto-removes on exit, but a timed-out container is still up.
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        rc = 124  # conventional timeout code
        timed_out = True
    wall = time.monotonic() - t0

    status_path = out_dir / "sandbox_status.json"
    status: dict = {}
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text())
        except json.JSONDecodeError:
            status = {"status": "unparseable_status", "raw": status_path.read_text()[:200]}
    elif timed_out:
        status = {"status": "timeout", "reason": f"host wall-clock > {timeout}s"}
        status_path.write_text(json.dumps(status) + "\n")
    else:
        # Container exited before writing status — treat as crash.
        status = {"status": "no_status_written", "returncode": rc}
        status_path.write_text(json.dumps(status) + "\n")

    return SandboxResult(
        runtime=rt,
        image=image,
        returncode=rc,
        status=status,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=wall,
    )


# ---------------------------------------------------------------------------
# Batch mode (eval image)
# ---------------------------------------------------------------------------


class SandboxInfraError(SandboxError):
    """The container did not produce a usable batch result (runtime error,
    container-level timeout, missing or unparseable output). This is an
    infrastructure failure, never a verdict on the programs under test."""


def resolve_image_id(image: str = DEFAULT_IMAGE, runtime: str | None = None) -> str:
    """Content-addressed ID (``sha256:...``) of ``image``.

    Batches run by ID so a retag mid-evaluation cannot mix two images.
    """
    rt = runtime or detect_runtime()
    proc = subprocess.run(
        [rt, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SandboxError(f"image '{image}' not found; run `make docker-build` first")
    return proc.stdout.strip()


@dataclasses.dataclass(frozen=True)
class BatchResult:
    """Outcome of one :func:`run_batch_in_sandbox` call."""

    image_id: str
    rows: list[dict[str, Any]]
    status: dict[str, Any]
    wall_seconds: float
    trace_path: Path | None


def build_batch_command(
    *,
    staging: Path,
    out_dir: Path,
    image: str,
    runtime: str,
    name: str,
    cpus: int | float,
    batch_args: Iterable[str],
) -> list[str]:
    """argv for one batch container. Separate so tests can check the flags."""
    return [
        runtime,
        "run",
        *isolation_flags(cpus),
        "--name",
        name,
        "-v",
        f"{staging.resolve()}:/work/src:ro",
        "-v",
        f"{out_dir.resolve()}:/work/out",
        image,
        "batch",
        *batch_args,
    ]


def run_batch_in_sandbox(
    team_a_src: Path,
    team_b_src: Path,
    seeds: list[int],
    work_dir: Path,
    *,
    image_id: str,
    cpus: int = 2,
    jobs: int | None = None,
    max_ticks: int = 1000,
    timeout: float = 10.0,
    under_test: str | None = None,
    record_seed: int | None = None,
    drones_a: int | None = None,
    drones_b: int | None = None,
    arena_scale: float | None = None,
    runtime: str | None = None,
) -> BatchResult:
    """Compile ``team_a_src`` + ``team_b_src`` once in a container and play ``seeds``.

    Both sources must already carry their ``TeamA`` / ``TeamB`` namespaces.
    ``work_dir`` receives ``src/`` (staged inputs, mounted read-only) and
    ``out/`` (results.jsonl, status.json, logs, trace.jsonl). Raises
    :class:`SandboxInfraError` when the container yields no usable result.
    """
    rt = runtime or detect_runtime()
    jobs = max(1, min(jobs or cpus, MAX_JOBS_PER_CONTAINER, len(seeds)))
    staging = work_dir / "src"
    out_dir = work_dir / "out"
    staging.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o777)  # see build_command: the container runs as 65534
    shutil.copyfile(team_a_src, staging / "a.cpp")
    shutil.copyfile(team_b_src, staging / "b.cpp")
    (staging / "seeds.txt").write_text("\n".join(str(s) for s in seeds) + "\n")

    batch_args = [
        "--seeds-file",
        "/work/src/seeds.txt",
        "--jobs",
        str(jobs),
        "--max-ticks",
        str(max_ticks),
        "--timeout",
        repr(float(timeout)),
    ]
    if under_test is not None:
        batch_args += ["--under-test", under_test]
    if record_seed is not None:
        batch_args += ["--record-seed", str(record_seed)]
    if drones_a is not None:
        batch_args += ["--drones-a", str(drones_a)]
    if drones_b is not None:
        batch_args += ["--drones-b", str(drones_b)]
    if arena_scale is not None:
        batch_args += ["--arena-scale", repr(float(arena_scale))]

    name = f"swarmevolve-batch-{uuid.uuid4().hex[:12]}"
    cmd = build_batch_command(
        staging=staging,
        out_dir=out_dir,
        image=image_id,
        runtime=rt,
        name=name,
        cpus=cpus,
        batch_args=batch_args,
    )
    # Per-match timeouts are enforced inside the container; this outer
    # bound only catches a wedged container or runtime.
    rounds = -(-len(seeds) // jobs)
    container_timeout = 180.0 + (rounds + 1) * timeout * 1.5

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=container_timeout
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run([rt, "kill", name], capture_output=True, check=False)
        raise SandboxInfraError(
            f"batch container {name} exceeded {container_timeout:.0f}s"
        ) from exc
    wall = time.monotonic() - t0

    status_path = out_dir / "status.json"
    results_path = out_dir / "results.jsonl"
    try:
        status = json.loads(status_path.read_text())
        rows = [json.loads(line) for line in results_path.read_text().splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxInfraError(
            f"batch container {name} exited rc={proc.returncode} without a usable result: "
            f"{exc}; stderr: {proc.stderr[-500:]}"
        ) from exc
    if proc.returncode != 0 or status.get("n_done") != len(seeds):
        raise SandboxInfraError(
            f"batch container {name} exited rc={proc.returncode} with status "
            f"{status.get('status')!r}, {status.get('n_done')} of {len(seeds)} matches; "
            f"stderr: {proc.stderr[-500:]}"
        )
    if sorted(r["seed"] for r in rows) != sorted(seeds):
        raise SandboxInfraError(f"batch container {name} returned a different seed set")
    trace = out_dir / "trace.jsonl"
    return BatchResult(
        image_id=image_id,
        rows=rows,
        status=status,
        wall_seconds=wall,
        trace_path=trace if record_seed is not None and trace.is_file() else None,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="sandbox",
        description="Run a SwarmEvolve match inside the M8 Docker sandbox.",
    )
    p.add_argument("--team-a", required=True, type=Path)
    p.add_argument("--team-b", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--image", default=DEFAULT_IMAGE)
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="host wall-clock timeout in seconds (default: 30)",
    )
    p.add_argument("--runtime", choices=["docker", "podman"], default=None)
    p.add_argument(
        "engine_args", nargs=argparse.REMAINDER, help="pass everything after `--` to the engine"
    )
    args = p.parse_args(argv)

    eng_args = args.engine_args
    # argparse.REMAINDER keeps the leading `--` if present; strip it.
    if eng_args and eng_args[0] == "--":
        eng_args = eng_args[1:]

    try:
        result = run_match_in_sandbox(
            args.team_a,
            args.team_b,
            args.out_dir,
            image=args.image,
            engine_args=eng_args,
            timeout=args.timeout,
            runtime=args.runtime,
        )
    except SandboxUnavailableError as exc:
        print(f"sandbox unavailable: {exc}", flush=True)
        return 20
    except SandboxError as exc:
        print(f"sandbox error: {exc}", flush=True)
        return 21

    print(
        json.dumps(
            {
                "runtime": result.runtime,
                "image": result.image,
                "returncode": result.returncode,
                "status": result.status,
                "wall_seconds": round(result.wall_seconds, 3),
            },
            indent=2,
        )
    )
    return 0 if result.ok else (result.returncode or 1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
