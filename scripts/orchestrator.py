#!/usr/bin/env python3
"""SwarmEvolve orchestrator CLI (M7).

One command to compile two AI source files, run a single match, and emit a
structured ``results.json`` describing the outcome. Designed so downstream
milestones (M8 sandbox, M9 fitness, M10 evolution, M12 tournament) can layer
on top without reworking argument shapes.

Sub-commands
------------
``run``         Compile both AIs + run one match. This is the only command
                fully implemented in M7.
``evaluate``    Stub (M9).
``evolve``      Stub (M10).
``tournament``  Stub (M12).

Exit codes
----------
* ``0`` — match pipeline finished (regardless of who won; check
  ``results.match.outcome``).
* ``2`` — invalid input (missing AI file, bad CLI).
* ``3`` — compilation failed. ``results.json`` still written with
  ``status="compile_failed"`` and captured compiler stderr.
* ``4`` — engine crashed (rc outside {0,1,2}) or exceeded ``--timeout``.
  ``results.json`` written with ``status="engine_crashed"`` or
  ``"timeout"``.
* ``10`` — internal error (unexpected exception).

The orchestrator never writes outside the ``--out-dir`` (default:
``./data/runs/<timestamp>/``). Even the scratch build directory lives under
the out-dir so ``--out-dir`` is the one handle a caller needs to clean up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = REPO_ROOT / "src" / "engine.cpp"
BASELINES = REPO_ROOT / "src" / "baselines"
PLACEHOLDER = "TEAM_NS_PLACEHOLDER"

RESULTS_SCHEMA_VERSION = 1

# Exit codes --------------------------------------------------------------

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_COMPILE_FAILED = 3
EXIT_RUN_FAILED = 4
EXIT_INTERNAL = 10


# ------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """One JSON object per log record; stable field ordering."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if record.args and isinstance(record.args, dict):
            payload.update(record.args)  # type: ignore[arg-type]
        for key in ("extra_fields",):
            val = getattr(record, key, None)
            if isinstance(val, dict):
                payload.update(val)
        return json.dumps(payload, sort_keys=True)


def _configure_logging(verbosity: int) -> logging.Logger:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("swarmevolve.orchestrator")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    return logger


# ------------------------------------------------------------------------
# Compiler / platform discovery
# ------------------------------------------------------------------------

def detect_compiler() -> str | None:
    """Return a path to a working C++17 compiler, or None.

    Mirrors ``tests/_build_helper.py::find_compiler`` so orchestrator runs
    and test integration stay in sync. Preference order:
    ``$CXX`` → Homebrew LLVM → ``nvc++`` on Linux → ``g++`` → ``clang++``.
    """
    env = os.environ.get("CXX")
    if env and shutil.which(env):
        return env
    candidates = [
        "/opt/homebrew/opt/llvm/bin/clang++",
        "/usr/local/opt/llvm/bin/clang++",
    ]
    if sys.platform == "linux":
        candidates.append("nvc++")
        candidates.append("g++")
        candidates.append("clang++")
    else:
        candidates.append("clang++")
        candidates.append("g++")
    for cand in candidates:
        if shutil.which(cand) or Path(cand).is_file():
            return cand
    return None


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2.0,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------------
# Source rendering / compilation
# ------------------------------------------------------------------------

def render_ai_source(src_path: Path, namespace: str, dest: Path) -> Path:
    """Copy ``src_path`` into ``dest`` with ``TEAM_NS_PLACEHOLDER`` → ``namespace``.

    Also rewrites ``#include "../foo.h"`` → ``#include "foo.h"`` so the
    ``-Isrc`` flag resolves the shared headers. Source files that don't
    contain the placeholder (e.g. hand-written AI files already wrapping
    their own namespace) are copied verbatim.
    """
    text = src_path.read_text()
    if PLACEHOLDER in text:
        text = text.replace(PLACEHOLDER, namespace)
    text = text.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    text = text.replace('#include "../types.h"', '#include "types.h"')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    return dest


@dataclass
class CompileResult:
    compiler: str
    flags: list[str]
    return_code: int
    wall_ms: int
    stdout: str
    stderr: str
    binary: Path | None


def compile_matchup(
    out_dir: Path,
    team_a_src: Path,
    team_b_src: Path,
    compiler: str,
    extra_flags: list[str] | None = None,
) -> CompileResult:
    """Render + compile a single-match binary under ``out_dir/build/``.

    The produced binary lives at ``out_dir/build/swarmevolve`` when the
    compile succeeds. On failure ``binary`` is ``None``.
    """
    build = out_dir / "build"
    a_dir = build / "src" / "a"
    b_dir = build / "src" / "b"
    render_ai_source(team_a_src, "TeamA", a_dir / "ai.cpp")
    render_ai_source(team_b_src, "TeamB", b_dir / "ai.cpp")

    binary = build / "swarmevolve"
    flags = [
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wshadow",
        "-Wpedantic",
        "-Werror",
        # `#pragma acc routine seq` is legal under OpenACC (nvc++) and
        # the spec mandates non-OpenACC compilers silently ignore it.
        # Apple clang does; g++ raises an "unknown pragma" warning which
        # -Werror promotes to a failure. Mirrors M8/M9 compile paths.
        "-Wno-unknown-pragmas",
        f"-I{REPO_ROOT / 'src'}",
    ]
    if extra_flags:
        flags.extend(extra_flags)
    cmd = [
        compiler,
        *flags,
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o",
        str(binary),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    wall_ms = int((time.monotonic() - t0) * 1000)
    return CompileResult(
        compiler=compiler,
        flags=flags,
        return_code=proc.returncode,
        wall_ms=wall_ms,
        stdout=proc.stdout,
        stderr=proc.stderr,
        binary=binary if proc.returncode == 0 else None,
    )


# ------------------------------------------------------------------------
# Match execution
# ------------------------------------------------------------------------

@dataclass
class MatchResult:
    return_code: int
    outcome: str
    ticks: int
    a_alive: int
    b_alive: int
    wall_ms: int
    stderr_tail: str


_OUTCOME_BY_RC = {0: "A_WIN", 1: "B_WIN", 2: "DRAW"}


def run_match(
    binary: Path,
    seed: int,
    *,
    record: Path | None = None,
    max_ticks: int | None = None,
    drones_a: int | None = None,
    drones_b: int | None = None,
    timeout: float = 10.0,
) -> MatchResult | None:
    """Execute the engine once; return ``None`` on timeout or crash.

    The orchestrator interprets engine return codes 0/1/2 as the valid
    outcome set; anything else is treated as a crash.
    """
    args = ["--seed", str(seed)]
    if record is not None:
        args += ["--record", str(record)]
    if max_ticks is not None:
        args += ["--max-ticks", str(max_ticks)]
    if drones_a is not None:
        args += ["--drones-a", str(drones_a)]
    if drones_b is not None:
        args += ["--drones-b", str(drones_b)]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(binary), *args],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return MatchResult(
            return_code=-1,
            outcome="TIMEOUT",
            ticks=0,
            a_alive=0,
            b_alive=0,
            wall_ms=int(timeout * 1000),
            stderr_tail=(exc.stderr or b"").decode(errors="replace")[-4096:],
        )
    wall_ms = int((time.monotonic() - t0) * 1000)

    if proc.returncode not in _OUTCOME_BY_RC:
        return MatchResult(
            return_code=proc.returncode,
            outcome="CRASH",
            ticks=0,
            a_alive=0,
            b_alive=0,
            wall_ms=wall_ms,
            stderr_tail=proc.stderr[-4096:],
        )

    lines = [line for line in proc.stdout.strip().splitlines() if line]
    if not lines:
        return MatchResult(
            return_code=proc.returncode,
            outcome="CRASH",
            ticks=0,
            a_alive=0,
            b_alive=0,
            wall_ms=wall_ms,
            stderr_tail="no-stdout\n" + proc.stderr[-4096:],
        )
    last = lines[-1]
    try:
        fields = dict(tok.split("=", 1) for tok in last.split())
        outcome_tag = fields["outcome"]
        ticks = int(fields["ticks"])
        a_alive = int(fields["a_alive"])
        b_alive = int(fields["b_alive"])
    except (KeyError, ValueError):
        return MatchResult(
            return_code=proc.returncode,
            outcome="CRASH",
            ticks=0,
            a_alive=0,
            b_alive=0,
            wall_ms=wall_ms,
            stderr_tail=f"bad-summary={last!r}\n" + proc.stderr[-4096:],
        )
    expected = _OUTCOME_BY_RC[proc.returncode]
    # If the engine's summary outcome disagrees with the rc, prefer the rc
    # (since the exit code is what the pipeline branches on).
    outcome = expected if outcome_tag == expected else expected
    return MatchResult(
        return_code=proc.returncode,
        outcome=outcome,
        ticks=ticks,
        a_alive=a_alive,
        b_alive=b_alive,
        wall_ms=wall_ms,
        stderr_tail=proc.stderr[-4096:],
    )


# ------------------------------------------------------------------------
# results.json assembly
# ------------------------------------------------------------------------

def build_results(
    *,
    status: str,
    team_a_src: Path,
    team_b_src: Path,
    seed: int,
    max_ticks: int,
    compile_result: CompileResult | None,
    match_result: MatchResult | None,
    trace_path: Path | None,
    video_path: Path | None,
    binary_path: Path | None,
) -> dict[str, Any]:
    def _team(path: Path) -> dict[str, Any]:
        return {
            "source_path": str(path.resolve()),
            "source_sha256": _sha256_file(path) if path.is_file() else "0" * 64,
            "injected": False,
        }

    compile_payload: dict[str, Any]
    if compile_result is None:
        compile_payload = {
            "compiler": "",
            "flags": [],
            "return_code": -1,
            "wall_ms": 0,
            "stdout": "",
            "stderr": "",
        }
    else:
        compile_payload = {
            "compiler": compile_result.compiler,
            "flags": compile_result.flags,
            "return_code": compile_result.return_code,
            "wall_ms": compile_result.wall_ms,
            "stdout": compile_result.stdout,
            "stderr": compile_result.stderr,
        }

    match_payload: dict[str, Any] | None
    if match_result is None or match_result.outcome in ("TIMEOUT", "CRASH"):
        match_payload = None if match_result is None else {
            "return_code": match_result.return_code,
            "outcome": "DRAW",  # placeholder; status carries the real tag
            "ticks": match_result.ticks,
            "a_alive": match_result.a_alive,
            "b_alive": match_result.b_alive,
            "wall_ms": match_result.wall_ms,
            "stderr_tail": match_result.stderr_tail,
        }
        # For CRASH/TIMEOUT we set match=null so downstream consumers always
        # read `status` first.
        if match_result is not None and match_result.outcome in ("TIMEOUT", "CRASH"):
            match_payload = None
    else:
        match_payload = {
            "return_code": match_result.return_code,
            "outcome": match_result.outcome,
            "ticks": match_result.ticks,
            "a_alive": match_result.a_alive,
            "b_alive": match_result.b_alive,
            "wall_ms": match_result.wall_ms,
            "stderr_tail": match_result.stderr_tail,
        }

    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "status": status,
        "team_a": _team(team_a_src),
        "team_b": _team(team_b_src),
        "seed": seed,
        "max_ticks": max_ticks,
        "compile": compile_payload,
        "match": match_payload,
        "artifacts": {
            "trace_path": str(trace_path.resolve()) if trace_path else None,
            "video_path": str(video_path.resolve()) if video_path else None,
            "binary_path": str(binary_path.resolve()) if binary_path else None,
        },
        "host": {
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "python": platform.python_version(),
            "git_sha": _git_sha(),
        },
    }


def _write_results(out_dir: Path, payload: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "results.json"
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return dest


# ------------------------------------------------------------------------
# Sub-command: run
# ------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace, logger: logging.Logger) -> int:
    team_a = Path(args.team_a).resolve()
    team_b = Path(args.team_b).resolve()
    if not team_a.is_file():
        logger.error("missing-team-a-source", extra={"extra_fields": {"path": str(team_a)}})
        return EXIT_INVALID_INPUT
    if not team_b.is_file():
        logger.error("missing-team-b-source", extra={"extra_fields": {"path": str(team_b)}})
        return EXIT_INVALID_INPUT

    compiler = args.compiler or detect_compiler()
    if compiler is None:
        logger.error("no-compiler-found")
        return EXIT_INVALID_INPUT

    if args.out_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = REPO_ROOT / "data" / "runs" / stamp
    else:
        out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("out-dir", extra={"extra_fields": {"path": str(out_dir)}})

    # Compile
    logger.info("compile-start", extra={"extra_fields": {"compiler": compiler}})
    compile_result = compile_matchup(out_dir, team_a, team_b, compiler)
    logger.info("compile-done", extra={"extra_fields": {
        "rc": compile_result.return_code, "wall_ms": compile_result.wall_ms,
    }})

    if compile_result.binary is None:
        payload = build_results(
            status="compile_failed",
            team_a_src=team_a,
            team_b_src=team_b,
            seed=args.seed,
            max_ticks=args.max_ticks,
            compile_result=compile_result,
            match_result=None,
            trace_path=None,
            video_path=None,
            binary_path=None,
        )
        _write_results(out_dir, payload)
        return EXIT_COMPILE_FAILED

    # Run
    trace_path: Path | None = None
    if args.record:
        trace_path = out_dir / "trace.jsonl"
    logger.info("match-start", extra={"extra_fields": {"seed": args.seed}})
    match_result = run_match(
        compile_result.binary,
        seed=args.seed,
        record=trace_path,
        max_ticks=args.max_ticks,
        drones_a=args.drones_a,
        drones_b=args.drones_b,
        timeout=args.timeout,
    )
    logger.info("match-done", extra={"extra_fields": {
        "outcome": match_result.outcome if match_result else None,
        "ticks": match_result.ticks if match_result else None,
    }})

    if match_result is None or match_result.outcome in ("TIMEOUT", "CRASH"):
        status = "timeout" if match_result and match_result.outcome == "TIMEOUT" else "engine_crashed"
        payload = build_results(
            status=status,
            team_a_src=team_a,
            team_b_src=team_b,
            seed=args.seed,
            max_ticks=args.max_ticks,
            compile_result=compile_result,
            match_result=match_result,
            trace_path=trace_path if trace_path and trace_path.is_file() else None,
            video_path=None,
            binary_path=compile_result.binary,
        )
        _write_results(out_dir, payload)
        return EXIT_RUN_FAILED

    # Optional video
    video_path: Path | None = None
    if args.video and trace_path is not None and trace_path.is_file():
        video_path = out_dir / "match.mp4"
        try:
            _render_video(trace_path, video_path, logger)
        except Exception as exc:  # pragma: no cover - video is optional
            logger.warning("video-render-failed",
                           extra={"extra_fields": {"error": str(exc)}})
            video_path = None

    payload = build_results(
        status="ok",
        team_a_src=team_a,
        team_b_src=team_b,
        seed=args.seed,
        max_ticks=args.max_ticks,
        compile_result=compile_result,
        match_result=match_result,
        trace_path=trace_path if trace_path and trace_path.is_file() else None,
        video_path=video_path,
        binary_path=compile_result.binary,
    )
    _write_results(out_dir, payload)
    return EXIT_OK


def _render_video(trace_path: Path, video_path: Path, logger: logging.Logger) -> None:
    """Invoke scripts/visualizer.py via subprocess to keep cv2 optional."""
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "visualizer.py"),
           str(trace_path), str(video_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"visualizer rc={proc.returncode} stderr={proc.stderr[-512:]}")
    logger.info("video-rendered",
                extra={"extra_fields": {"path": str(video_path)}})


# ------------------------------------------------------------------------
# Stub sub-commands (M9, M10, M12)
# ------------------------------------------------------------------------

def cmd_evaluate(args: argparse.Namespace, logger: logging.Logger) -> int:
    """(M9) Run ``--n-matches`` seeded matches and emit fitness.json + events.jsonl.

    Unlike ``run``, this sub-command never emits a trace by default —
    fitness evaluation is about aggregate stats across many matches,
    not per-match introspection. ``--record-traces`` re-enables them
    but is off by default to keep disk usage bounded.
    """
    # Local imports so fitness.py / experiment_log.py remain optional
    # for callers that only use `run`.
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import fitness as _fitness  # noqa: PLC0415
    import experiment_log as _explog  # noqa: PLC0415

    team_a = Path(args.team_a).resolve()
    team_b = Path(args.team_b).resolve()
    if not team_a.is_file():
        logger.error("missing-team-a-source",
                     extra={"extra_fields": {"path": str(team_a)}})
        return EXIT_INVALID_INPUT
    if not team_b.is_file():
        logger.error("missing-team-b-source",
                     extra={"extra_fields": {"path": str(team_b)}})
        return EXIT_INVALID_INPUT

    if args.out_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = REPO_ROOT / "data" / "experiments" / f"eval_{stamp}"
    else:
        out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    compiler = args.compiler or detect_compiler()
    if compiler is None:
        logger.error("no-compiler-found")
        return EXIT_INVALID_INPUT

    cfg = {
        "n_matches": args.n_matches,
        "seed_base": args.seed_base,
        "workers": args.workers,
        "max_ticks": args.max_ticks,
        "timeout": args.timeout,
        "compiler": compiler,
    }

    try:
        with _explog.ExperimentLog(out_dir) as log:
            log.write_start(
                experiment_type="fitness",
                team_a_src=team_a,
                team_b_src=team_b,
                config=cfg,
            )
            logger.info("evaluate-start", extra={"extra_fields": cfg})
            try:
                result = _fitness.evaluate_fitness(
                    team_a, team_b,
                    n_matches=args.n_matches,
                    seed_base=args.seed_base,
                    workers=args.workers,
                    max_ticks=args.max_ticks,
                    timeout=args.timeout,
                    compiler=compiler,
                    scratch_root=out_dir / "build",
                )
            except _fitness.CompileError as exc:
                log.write("compile_failed", error=str(exc))
                logger.error("compile-failed",
                             extra={"extra_fields": {"error": str(exc)[:512]}})
                return EXIT_COMPILE_FAILED
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                log.write("evaluate_failed", error=str(exc))
                logger.error("evaluate-failed",
                             extra={"extra_fields": {"error": str(exc)}})
                return EXIT_INVALID_INPUT

            # Record every match individually so replay can walk the log.
            for match in result.per_match:
                log.write("match_result", **match)
            log.write(
                "fitness_summary",
                wins_a=result.wins_a, wins_b=result.wins_b,
                draws=result.draws, invalid=result.invalid,
                mean=result.mean, stdev=result.stdev,
                ci_low=result.ci_low, ci_high=result.ci_high,
                wall_seconds=result.wall_seconds,
            )

        # Write fitness.json OUTSIDE the log ctx so the log is closed
        # before we touch the sibling file; avoids interleaving.
        fitness_path = out_dir / "fitness.json"
        fitness_path.write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
        )
        logger.info("evaluate-done",
                    extra={"extra_fields": {
                        "wins_a": result.wins_a, "wins_b": result.wins_b,
                        "draws": result.draws, "mean": result.mean,
                        "fitness_path": str(fitness_path),
                    }})

        # Guard: if more than half the matches crashed/timeout'd, surface
        # that as a pipeline error instead of silently returning 0.
        if result.n_matches > 0 and result.invalid * 2 > result.n_matches:
            logger.error("too-many-invalid-matches",
                         extra={"extra_fields": {
                             "invalid": result.invalid,
                             "n_matches": result.n_matches,
                         }})
            return EXIT_RUN_FAILED
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("evaluate-internal-error",
                         extra={"extra_fields": {"error": str(exc)}})
        return EXIT_INTERNAL
    return EXIT_OK


def cmd_replay(args: argparse.Namespace, logger: logging.Logger) -> int:
    """(M9) Re-run the exact matches captured in a previous ``events.jsonl``.

    The replay must reproduce the original ``fitness_summary`` bit-for-
    bit (mean, stdev, wins, draws). Any divergence exits 4.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import fitness as _fitness  # noqa: PLC0415
    import experiment_log as _explog  # noqa: PLC0415

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        logger.error("missing-run-dir",
                     extra={"extra_fields": {"path": str(run_dir)}})
        return EXIT_INVALID_INPUT

    try:
        events = _explog.ExperimentLog.read(run_dir)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("bad-events-log",
                     extra={"extra_fields": {"error": str(exc)}})
        return EXIT_INVALID_INPUT

    start_ev = next((e for e in events if e.get("type") == "experiment_start"), None)
    summary_ev = next((e for e in events if e.get("type") == "fitness_summary"), None)
    if start_ev is None or summary_ev is None:
        logger.error("events-missing-start-or-summary")
        return EXIT_INVALID_INPUT
    if start_ev.get("experiment_type") != "fitness":
        logger.error("not-a-fitness-run",
                     extra={"extra_fields": {
                         "experiment_type": start_ev.get("experiment_type"),
                     }})
        return EXIT_INVALID_INPUT

    env = start_ev.get("environment", {})
    team_a = Path(env.get("team_a", {}).get("path", ""))
    team_b = Path(env.get("team_b", {}).get("path", ""))
    cfg = start_ev.get("config", {})
    if not team_a.is_file() or not team_b.is_file():
        logger.error("source-file-missing-for-replay",
                     extra={"extra_fields": {
                         "team_a": str(team_a), "team_b": str(team_b),
                     }})
        return EXIT_INVALID_INPUT

    # Compiler choice on replay: the recorded compiler pins what the
    # *original* run used, but on a different host that binary may be
    # absent (Linux g++ on macOS laptop) or broken (macOS's /usr/bin/g++
    # is a clang-C wrapper that can't find libc++). We prefer the
    # locally-auto-detected compiler and record any substitution in the
    # log. The determinism contract is over *inputs* (sources, seeds,
    # configs); cross-compiler divergence in engine floats would surface
    # as a score mismatch in the field-by-field comparison below — which
    # is exactly what we want this check to catch.
    recorded_compiler = cfg.get("compiler")
    local_compiler = detect_compiler()
    resolved_compiler = local_compiler or recorded_compiler
    if recorded_compiler and resolved_compiler != recorded_compiler:
        logger.info("replay-compiler-substitution",
                    extra={"extra_fields": {
                        "recorded": recorded_compiler,
                        "resolved": resolved_compiler,
                    }})

    logger.info("replay-start",
                extra={"extra_fields": {"run_dir": str(run_dir),
                                         "n_matches": cfg.get("n_matches")}})
    result = _fitness.evaluate_fitness(
        team_a, team_b,
        n_matches=cfg.get("n_matches", 1),
        seed_base=cfg.get("seed_base", 0),
        workers=cfg.get("workers"),
        max_ticks=cfg.get("max_ticks", 1000),
        timeout=cfg.get("timeout", 10.0),
        compiler=resolved_compiler,
        scratch_root=run_dir / "replay_build",
    )

    # Compare the reproducible subset of fields; ci/wall_seconds are
    # deterministic *given* the scores, so we check those too.
    for key in ("wins_a", "wins_b", "draws", "invalid", "mean", "stdev",
                "ci_low", "ci_high"):
        original = summary_ev.get(key)
        replayed = getattr(result, key)
        if original != replayed:
            logger.error("replay-divergence",
                         extra={"extra_fields": {
                             "field": key, "original": original,
                             "replayed": replayed,
                         }})
            return EXIT_RUN_FAILED

    logger.info("replay-ok",
                extra={"extra_fields": {"run_dir": str(run_dir)}})
    return EXIT_OK


def cmd_evolve(args: argparse.Namespace, logger: logging.Logger) -> int:
    """Thin wrapper over ``scripts/evolve.main()``.

    We re-export the CLI flags verbatim so the orchestrator is an
    alternative spelling, not a parallel interface. ``evolve.py``
    owns the argparse surface area; we just forward ``argv``.
    """
    # Import here so `orchestrator evaluate` / `run` / `replay` don't
    # pay the matplotlib / anthropic import cost on every invocation.
    import evolve as _evolve  # type: ignore[import-not-found]

    # Reconstruct the list of argv tokens from the namespace produced
    # by our sub-parser so evolve.main parses them under its own
    # argparse. Only non-None values are forwarded so defaults stay
    # consistent between the two entry points.
    forwarded: list[str] = []
    if args.verbose:
        forwarded += ["-" + ("v" * args.verbose)]
    pairs = (
        ("--opponent", args.opponent),
        ("--as-team", args.as_team),
        ("--generations", args.generations),
        ("--n-matches", args.n_matches),
        ("--workers", args.workers),
        ("--client", args.client),
        ("--mock-response-dir", args.mock_response_dir),
        ("--model", args.model),
        ("--seed", args.seed),
        ("--accept-margin", args.accept_margin),
        ("--max-compile-failures", args.max_compile_failures),
        ("--checkpoint-every", args.checkpoint_every),
        ("--out-dir", args.out_dir),
        ("--resume", args.resume),
        ("--seed-ai", args.seed_ai),
        ("--prompt", args.prompt),
    )
    for flag, value in pairs:
        if value is None:
            continue
        forwarded += [flag, str(value)]
    logger.info("evolve-start",
                extra={"extra_fields": {"argv": forwarded}})
    return int(_evolve.main(forwarded))


def cmd_tournament(args: argparse.Namespace, logger: logging.Logger) -> int:
    logger.error("not-implemented",
                 extra={"extra_fields": {"command": "tournament", "milestone": "M12"}})
    return EXIT_INVALID_INPUT


# ------------------------------------------------------------------------
# CLI entry
# ------------------------------------------------------------------------

def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def _non_negative_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {ivalue}")
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="SwarmEvolve orchestrator CLI (M7).",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="increase verbosity; -v=info, -vv=debug")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="compile two AIs and run one match")
    run.add_argument("--team-a", required=True,
                     help="path to Team A AI source file")
    run.add_argument("--team-b", required=True,
                     help="path to Team B AI source file")
    run.add_argument("--seed", type=_non_negative_int, default=0)
    run.add_argument("--max-ticks", type=_positive_int, default=1000)
    run.add_argument("--drones-a", type=_positive_int, default=10)
    run.add_argument("--drones-b", type=_positive_int, default=10)
    run.add_argument("--compiler", default=None,
                     help="override C++ compiler; default: $CXX or auto-detect")
    run.add_argument("--out-dir", default=None,
                     help="where to write results.json + build/ + trace.jsonl")
    run.add_argument("--record", action=argparse.BooleanOptionalAction, default=True,
                     help="write JSONL trace (default: on)")
    run.add_argument("--video", action=argparse.BooleanOptionalAction, default=False,
                     help="render MP4 via visualizer (default: off)")
    run.add_argument("--timeout", type=float, default=10.0,
                     help="engine wall-clock timeout in seconds")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser(
        "evaluate",
        help="run N matches, compute fitness, and write events.jsonl + fitness.json",
    )
    evaluate.add_argument("--team-a", required=True,
                          help="path to Team A AI source file")
    evaluate.add_argument("--team-b", required=True,
                          help="path to Team B AI source file")
    evaluate.add_argument("--n-matches", type=_positive_int, default=20,
                          help="number of matches to run (default: 20)")
    evaluate.add_argument("--seed-base", type=_non_negative_int, default=0,
                          help="first seed; seeds are [seed_base, seed_base+n_matches)")
    evaluate.add_argument("--workers", type=_positive_int, default=None,
                          help="worker processes; default: min(cpu_count, n_matches)")
    evaluate.add_argument("--max-ticks", type=_positive_int, default=1000,
                          help="per-match tick cap (default: 1000)")
    evaluate.add_argument("--timeout", type=float, default=10.0,
                          help="per-match wall-clock timeout in seconds (default: 10)")
    evaluate.add_argument("--compiler", default=None,
                          help="override C++ compiler; default: $CXX or auto-detect")
    evaluate.add_argument("--out-dir", default=None,
                          help="where to write fitness.json + events.jsonl")
    evaluate.set_defaults(func=cmd_evaluate)

    replay = sub.add_parser(
        "replay",
        help="re-run a previous fitness experiment and verify byte-identical summary",
    )
    replay.add_argument("run_dir",
                        help="path to a previous experiment directory (contains events.jsonl)")
    replay.set_defaults(func=cmd_replay)

    evolve = sub.add_parser(
        "evolve",
        help="run closed-loop LLM evolution against a frozen opponent",
    )
    evolve.add_argument("--opponent", default=None,
                        help="path to frozen opponent AI source (required for fresh run)")
    evolve.add_argument("--as-team", choices=("A", "B"), default="A")
    evolve.add_argument("--generations", type=_positive_int, default=50)
    evolve.add_argument("--n-matches", type=_positive_int, default=20)
    evolve.add_argument("--workers", type=_positive_int, default=None)
    evolve.add_argument("--client", choices=("anthropic", "mock"), default="anthropic")
    evolve.add_argument("--mock-response-dir", default=None,
                        help="directory of *.md responses when --client=mock")
    evolve.add_argument("--model", default=None,
                        help="override LLM model id (default: $ANTHROPIC_MODEL)")
    evolve.add_argument("--seed", type=int, default=None,
                        help="root seed for per-generation seed derivation")
    evolve.add_argument("--accept-margin", type=float, default=0.0,
                        help="challenger must beat champion mean by > margin")
    evolve.add_argument("--max-compile-failures", type=_positive_int, default=5)
    evolve.add_argument("--checkpoint-every", type=_positive_int, default=10)
    evolve.add_argument("--out-dir", default=None,
                        help="run directory (default: data/experiments/<ts>)")
    evolve.add_argument("--resume", default=None,
                        help="resume an existing run directory")
    evolve.add_argument("--seed-ai", default=None,
                        help="initial champion (default: --opponent)")
    evolve.add_argument("--prompt", default=None,
                        help="prompt template path (default: prompts/evolve_ai.md)")
    evolve.set_defaults(func=cmd_evolve)

    tournament = sub.add_parser("tournament", help="(M12) round-robin tournament")
    tournament.set_defaults(func=cmd_tournament)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = _configure_logging(args.verbose)
    try:
        return int(args.func(args, logger))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("internal-error",
                         extra={"extra_fields": {"error": str(exc)}})
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
