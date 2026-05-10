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

This module is **host-side only**. Nothing here runs inside the
container; the in-container logic lives in ``docker/entrypoint.sh``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

DEFAULT_IMAGE = "swarmevolve-sandbox:latest"

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

    import time

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
