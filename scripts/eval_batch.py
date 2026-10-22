#!/usr/bin/env python3
"""Batch match runner: inject guards, compile once, play many seeds.

One code path for both fitness backends (``scripts/fitness.py``). In the
eval container (``docker/Dockerfile.eval``) the entrypoint runs this file
as ``batch``; on the host, ``fitness.py`` imports it and calls
:func:`run_batch` directly. Either way the same steps run:

1. Copy the engine sources and both AI sources into a private work tree.
2. Inject loop guards into each AI source (``inject_guards.py --stdout``).
3. Compile engine, A and B as separate translation units with the
   evaluation flags (``compile_flags.eval_flags``), then link.
4. Play every seed, ``jobs`` matches at a time, each with a wall-clock
   timeout. Each match runs in its own session and the whole process
   group is killed afterwards, so stray children cannot outlive it.
5. Write ``results.jsonl`` (one line per match, sorted by seed) and
   ``status.json`` to the output directory.

Match outcomes: ``A_WIN``, ``B_WIN``, ``DRAW`` (engine exit 0/1/2) and the
four invalid categories ``GUARD_FAIL``, ``COMPILE_FAIL``, ``CRASH`` and
``TIMEOUT``. A guard or compile failure produces one line per seed with
``failed_side`` naming the source that failed. Invalid matches score as a
loss for ``under_test`` when it is set, else 0.

Results are kept in memory and written once, after every match process
group has been killed, so a match cannot leave behind a forged
``results.jsonl``.

Stdlib only, and compatible with the eval image's Python 3.10.
"""

from __future__ import annotations

import argparse
import concurrent.futures as _futures
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import compile_flags  # noqa: E402

ENGINE_FILES = ("engine.cpp", "engine.h", "types.h", "ai_abi.h")
HARNESS_FILES = ("inject_guards.py", "compile_flags.py", "eval_batch.py")

OUTCOME_BY_RC = {0: "A_WIN", 1: "B_WIN", 2: "DRAW"}
SCORE_BY_RC = {0: 1.0, 1: -1.0, 2: 0.0}
INVALID_OUTCOMES = ("GUARD_FAIL", "COMPILE_FAIL", "CRASH", "TIMEOUT")
ALL_OUTCOMES = ("A_WIN", "B_WIN", "DRAW", *INVALID_OUTCOMES)

# Bytes of match stdout kept for parsing the summary line.
_STDOUT_TAIL = 4096


def invalid_score(under_test: str | None) -> float:
    """Score of an invalid match: a loss for the side under test, else 0."""
    if under_test == "A":
        return -1.0
    if under_test == "B":
        return 1.0
    return 0.0


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def harness_hashes(engine_dir: Path, harness_dir: Path) -> dict[str, str]:
    """SHA-256 of every file that decides how a match is built and played."""
    hashes = {name: sha256_file(engine_dir / name) for name in ENGINE_FILES}
    hashes.update({name: sha256_file(harness_dir / name) for name in HARNESS_FILES})
    return hashes


def compiler_version(compiler: str) -> str:
    """First line of ``<compiler> --version``, or '' when it cannot run."""
    try:
        proc = subprocess.run(
            [compiler, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = (proc.stdout or proc.stderr).splitlines()
    return lines[0].strip() if lines else ""


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _row(seed: int, outcome: str, score: float, rc: int, wall_ms: int, **extra: Any) -> dict:
    row = {
        "seed": seed,
        "outcome": outcome,
        "score": score,
        "ticks": 0,
        "a_alive": 0,
        "b_alive": 0,
        "return_code": rc,
        "wall_ms": wall_ms,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class BuildFailure(Exception):
    def __init__(self, outcome: str, side: str, rc: int, log: str):
        super().__init__(f"{outcome} side={side} rc={rc}")
        self.outcome = outcome
        self.side = side
        self.rc = rc
        self.log = log


def _inject(injector: Path, src: Path, dest: Path, log_path: Path, side: str) -> None:
    with dest.open("w") as out, log_path.open("w") as log:
        proc = subprocess.run(
            [sys.executable, str(injector), "--stdout", str(src)],
            stdout=out,
            stderr=log,
            check=False,
        )
    if proc.returncode != 0:
        raise BuildFailure("GUARD_FAIL", side, proc.returncode, log_path.read_text()[-4000:])


def build(
    *,
    team_a: Path,
    team_b: Path,
    engine_dir: Path,
    injector: Path,
    work: Path,
    out: Path,
    compiler: str,
    flags: list[str],
) -> Path:
    """Inject guards and compile; return the match binary or raise BuildFailure."""
    src = work / "src"
    for sub in ("a", "b"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    for name in ENGINE_FILES:
        shutil.copyfile(engine_dir / name, src / name)
    shutil.copyfile(team_a, src / "a" / "ai.orig.cpp")
    shutil.copyfile(team_b, src / "b" / "ai.orig.cpp")

    for side, sub in (("A", "a"), ("B", "b")):
        _inject(
            injector,
            src / sub / "ai.orig.cpp",
            src / sub / "ai.cpp",
            out / f"inject_{sub}.log",
            side,
        )

    units = (
        ("engine", src / "engine.cpp"),
        ("A", src / "a" / "ai.cpp"),
        ("B", src / "b" / "ai.cpp"),
    )
    procs = []
    for side, unit in units:
        obj = work / f"{side}.o"
        procs.append(
            (
                side,
                obj,
                subprocess.Popen(
                    [compiler, *flags, f"-I{src}", "-c", str(unit), "-o", str(obj)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                ),
            )
        )
    logs: list[str] = []
    failed: list[tuple[str, int]] = []
    for side, _obj, proc in procs:
        text, _ = proc.communicate()
        logs.append(f"== {side} rc={proc.returncode}\n{text}")
        if proc.returncode != 0:
            failed.append((side, proc.returncode))
    binary = work / "swarmevolve"
    if not failed:
        link = subprocess.run(
            [compiler, *flags, *(str(obj) for _s, obj, _p in procs), "-o", str(binary)],
            capture_output=True,
            text=True,
            check=False,
        )
        logs.append(f"== link rc={link.returncode}\n{link.stdout}{link.stderr}")
        if link.returncode != 0:
            failed.append(("link", link.returncode))
    log = "\n".join(logs)
    (out / "compile.log").write_text(log)
    if failed:
        sides = [s for s, _rc in failed]
        side = "both" if set(sides) == {"A", "B"} else sides[0]
        raise BuildFailure("COMPILE_FAIL", side, failed[0][1], log[-4000:])
    return binary


# ---------------------------------------------------------------------------
# Play
# ---------------------------------------------------------------------------


def _parse_summary(stdout_tail: str) -> tuple[int, int, int]:
    """(ticks, a_alive, b_alive) from the engine's last stdout line."""
    lines = [line for line in stdout_tail.strip().splitlines() if line]
    if not lines:
        return 0, 0, 0
    try:
        fields = dict(tok.split("=", 1) for tok in lines[-1].split())
        return (
            int(fields.get("ticks", 0)),
            int(fields.get("a_alive", 0)),
            int(fields.get("b_alive", 0)),
        )
    except ValueError:
        return 0, 0, 0


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def play_one(
    binary: Path,
    seed: int,
    *,
    max_ticks: int,
    timeout: float,
    under_test: str | None,
    stdout_path: Path,
    engine_args: list[str],
    record_path: Path | None = None,
) -> dict[str, Any]:
    cmd = [str(binary), "--seed", str(seed), "--max-ticks", str(max_ticks), *engine_args]
    if record_path is not None:
        cmd += ["--record", str(record_path), "--record-actions"]
    t0 = time.monotonic()
    with stdout_path.open("w+b") as out:
        try:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True
            )
        except OSError as exc:
            # e.g. EAGAIN under the pids limit after a fork bomb.
            return _row(seed, "CRASH", invalid_score(under_test), -1, 0, error=str(exc)[:200])
        try:
            rc = proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        _kill_group(proc.pid)
        if timed_out:
            proc.wait()
            return _row(seed, "TIMEOUT", invalid_score(under_test), -1, int(timeout * 1000))
        wall_ms = int((time.monotonic() - t0) * 1000)
        if rc not in OUTCOME_BY_RC:
            return _row(seed, "CRASH", invalid_score(under_test), rc, wall_ms)
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - _STDOUT_TAIL))
        tail = out.read().decode("utf-8", errors="replace")
    ticks, a_alive, b_alive = _parse_summary(tail)
    row = _row(seed, OUTCOME_BY_RC[rc], SCORE_BY_RC[rc], rc, wall_ms)
    row.update(ticks=ticks, a_alive=a_alive, b_alive=b_alive)
    return row


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


def run_batch(
    *,
    team_a: Path,
    team_b: Path,
    seeds: list[int],
    out: Path,
    work: Path,
    engine_dir: Path,
    harness_dir: Path,
    compiler: str = "g++",
    max_ticks: int = 1000,
    timeout: float = 10.0,
    jobs: int = 1,
    under_test: str | None = None,
    record_seed: int | None = None,
    drones_a: int | None = None,
    drones_b: int | None = None,
    arena_scale: float | None = None,
) -> dict[str, Any]:
    """Run one batch; write ``results.jsonl`` and ``status.json`` to ``out``.

    Returns the final status dict. Rows are returned through the file only,
    so callers on both backends read the same bytes.
    """
    t0 = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    flags = compile_flags.eval_flags(compiler)
    status: dict[str, Any] = {
        "status": "running",
        "n_seeds": len(seeds),
        "compiler": compiler,
        "compiler_version": compiler_version(compiler),
        "flags": flags,
        "cpu_model": cpu_model(),
        "kernel": os.uname().release,
        "harness_sha256": harness_hashes(engine_dir, harness_dir),
        "jobs": jobs,
        "max_ticks": max_ticks,
        "timeout": timeout,
        "under_test": under_test,
    }
    _write_json(out / "status.json", status)

    engine_args: list[str] = []
    if drones_a is not None:
        engine_args += ["--drones-a", str(drones_a)]
    if drones_b is not None:
        engine_args += ["--drones-b", str(drones_b)]
    if arena_scale is not None:
        engine_args += ["--arena-scale", repr(float(arena_scale))]
    status["engine_args"] = engine_args

    rows: list[dict[str, Any]]
    try:
        binary = build(
            team_a=team_a,
            team_b=team_b,
            engine_dir=engine_dir,
            injector=harness_dir / "inject_guards.py",
            work=work,
            out=out,
            compiler=compiler,
            flags=flags,
        )
    except BuildFailure as exc:
        status.update(
            status="guard_failed" if exc.outcome == "GUARD_FAIL" else "compile_failed",
            failed_side=exc.side,
            build_rc=exc.rc,
        )
        score = invalid_score(under_test)
        rows = [_row(s, exc.outcome, score, exc.rc, 0, failed_side=exc.side) for s in seeds]
    else:
        status["compile_seconds"] = round(time.monotonic() - t0, 3)
        _write_json(out / "status.json", status)
        slots = max(1, min(jobs, len(seeds)))
        free = list(range(slots))

        def task(seed: int) -> dict[str, Any]:
            slot = free.pop()
            try:
                return play_one(
                    binary,
                    seed,
                    max_ticks=max_ticks,
                    timeout=timeout,
                    under_test=under_test,
                    stdout_path=work / f"stdout_{slot}.txt",
                    engine_args=engine_args,
                    record_path=out / "trace.jsonl" if seed == record_seed else None,
                )
            finally:
                free.append(slot)

        t_play = time.monotonic()
        if slots == 1:
            rows = [task(s) for s in seeds]
        else:
            with _futures.ThreadPoolExecutor(max_workers=slots) as pool:
                rows = list(pool.map(task, seeds))
        status["play_seconds"] = round(time.monotonic() - t_play, 3)
        status["status"] = "ok"

    rows.sort(key=lambda r: r["seed"])
    with (out / "results.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    status["counts"] = {o: sum(1 for r in rows if r["outcome"] == o) for o in ALL_OUTCOMES}
    status["n_done"] = len(rows)
    status["wall_seconds"] = round(time.monotonic() - t0, 3)
    _write_json(out / "status.json", status)
    return status


def platform_info(engine_dir: Path, harness_dir: Path, compiler: str) -> dict[str, Any]:
    """What this environment would stamp on a batch, without playing one."""
    info: dict[str, Any] = {
        "compiler": compiler,
        "compiler_version": compiler_version(compiler),
        "flags": compile_flags.eval_flags(compiler),
        "cpu_model": cpu_model(),
        "kernel": os.uname().release,
        "harness_sha256": harness_hashes(engine_dir, harness_dir),
    }
    build_info = harness_dir / "BUILD_INFO.json"
    if build_info.is_file():
        info["build_info"] = json.loads(build_info.read_text())
    return info


def read_results(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines() if line]


def _parse_seeds(text: str) -> list[int]:
    return [int(tok) for tok in text.replace(",", " ").split()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compile two AIs once and play a list of seeds.")
    p.add_argument("--info", action="store_true", help="print the platform info as JSON and exit")
    p.add_argument("--src", type=Path, help="directory holding a.cpp and b.cpp")
    p.add_argument("--out", type=Path)
    p.add_argument("--work", type=Path, help="scratch directory (tmpfs)")
    p.add_argument("--engine-dir", type=Path, default=HERE)
    p.add_argument("--harness-dir", type=Path, default=HERE)
    p.add_argument("--seeds-file", type=Path, help="seeds, whitespace or comma separated")
    p.add_argument("--seeds", help="seeds, comma separated")
    p.add_argument("--compiler", default="g++")
    p.add_argument("--max-ticks", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=10.0, help="per-match wall-clock seconds")
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--under-test", choices=["A", "B"], default=None)
    p.add_argument("--record-seed", type=int, default=None)
    p.add_argument("--drones-a", type=int, default=None)
    p.add_argument("--drones-b", type=int, default=None)
    p.add_argument("--arena-scale", type=float, default=None)
    args = p.parse_args(argv)

    if args.info:
        info = platform_info(args.engine_dir, args.harness_dir, args.compiler)
        print(json.dumps(info, sort_keys=True))
        return 0
    if args.src is None or args.out is None or args.work is None:
        p.error("--src, --out and --work are required for a batch")
    args.out.mkdir(parents=True, exist_ok=True)

    def invalid(reason: str) -> int:
        _write_json(args.out / "status.json", {"status": "invalid_input", "reason": reason})
        print(f"eval_batch: {reason}", file=sys.stderr)
        return 2

    if args.seeds_file is not None:
        seeds = _parse_seeds(args.seeds_file.read_text())
    elif args.seeds is not None:
        seeds = _parse_seeds(args.seeds)
    else:
        return invalid("no seeds given")
    if not seeds or len(set(seeds)) != len(seeds) or min(seeds) < 0:
        return invalid("seeds must be a non-empty list of distinct non-negative integers")
    for name in ("a.cpp", "b.cpp"):
        if not (args.src / name).is_file():
            return invalid(f"missing {args.src / name}")

    run_batch(
        team_a=args.src / "a.cpp",
        team_b=args.src / "b.cpp",
        seeds=seeds,
        out=args.out,
        work=args.work,
        engine_dir=args.engine_dir,
        harness_dir=args.harness_dir,
        compiler=args.compiler,
        max_ticks=args.max_ticks,
        timeout=args.timeout,
        jobs=args.jobs,
        under_test=args.under_test,
        record_seed=args.record_seed,
        drones_a=args.drones_a,
        drones_b=args.drones_b,
        arena_scale=args.arena_scale,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
