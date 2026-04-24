#!/usr/bin/env python3
"""Fitness evaluator (M9).

Runs N seeded matches between two AI sources and returns an aggregate
:class:`FitnessResult` with win/loss/draw counts, per-match scores,
mean, stdev, and a bootstrap 95% confidence interval on the mean.

Design notes
------------
* **Compile once per worker, run many matches.** Each worker process
  renders both AIs, invokes the host compiler once, then reuses the
  resulting binary across its subset of seeds. This keeps the cost
  near the engine's ~20 ms/tick rather than the ~500 ms/compile.
* **Host-side by default; sandbox opt-in.** The spec (§M9) eventually
  routes matches through ``scripts.sandbox``; for M9 we keep the host
  path as default so CI + local iteration stay fast. ``--sandbox``
  opt-in calls :func:`sandbox.run_match_in_sandbox` per match.
* **Deterministic reductions.** Seed sequences are contiguous
  ``[seed_base, seed_base + n_matches)`` so two runs with the same
  ``(team_a, team_b, seed_base, n_matches)`` produce byte-identical
  per-match scores and therefore identical aggregates. The bootstrap
  CI uses a second, orthogonal seed (``ci_seed``) derived from
  ``seed_base`` so it, too, reproduces.
* **Score convention**: +1 for A_WIN, -1 for B_WIN, 0 for DRAW. A
  match that crashes / times out is recorded as ``score=0`` and
  counted in ``invalid`` rather than ``draws``, so the fitness mean
  reflects strategy quality rather than engine stability.

Public API
----------
>>> from fitness import evaluate_fitness
>>> result = evaluate_fitness("src/baselines/pursuit_v1.cpp",
...                           "src/baselines/cluster_v1.cpp",
...                           n_matches=20, seed_base=0)
>>> result.mean  # doctest: +SKIP
0.1

:class:`FitnessResult` is frozen + serialisable to JSON via
:func:`FitnessResult.to_dict`, which is what the orchestrator writes
to ``fitness.json``.
"""

from __future__ import annotations

import concurrent.futures as _futures
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = REPO_ROOT / "src" / "engine.cpp"
PLACEHOLDER = "TEAM_NS_PLACEHOLDER"

_OUTCOME_BY_RC = {0: "A_WIN", 1: "B_WIN", 2: "DRAW"}
_SCORE_BY_RC = {0: 1.0, 1: -1.0, 2: 0.0}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitnessResult:
    """Aggregate fitness over ``n_matches`` matches.

    Field semantics
    ---------------
    ``mean``      mean of per-match score (+1 A, -1 B, 0 draw/invalid).
    ``stdev``     population stdev of per-match score (0 for n<2).
    ``ci_low``,   95% bootstrap CI endpoints. ``None`` only when
    ``ci_high``   ``n_matches < 2`` (CI undefined on a single sample).
    ``wins_a/b``  counts of A_WIN / B_WIN outcomes.
    ``draws``     count of DRAW outcomes.
    ``invalid``   count of matches that crashed / timed out (these
                  contribute ``score=0`` to the mean).
    ``per_match`` list of per-match dicts: ``{seed, outcome, score,
                  ticks, wall_ms, a_alive, b_alive}``.
    """

    team_a_path: str
    team_b_path: str
    n_matches: int
    seed_base: int
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    invalid: int = 0
    mean: float = 0.0
    stdev: float = 0.0
    ci_low: float | None = None
    ci_high: float | None = None
    wall_seconds: float = 0.0
    workers: int = 1
    compiler: str = ""
    per_match: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation. Used by ``fitness.json``."""
        d = asdict(self)
        # asdict already copies; we just pin ordering keys deterministically.
        return d


# ---------------------------------------------------------------------------
# Compiler discovery (mirrors orchestrator.detect_compiler but local-only so
# that workers can import this module without pulling in the orchestrator)
# ---------------------------------------------------------------------------


def _find_compiler() -> str | None:
    env = os.environ.get("CXX")
    if env and shutil.which(env):
        return env
    candidates = [
        "/opt/homebrew/opt/llvm/bin/clang++",
        "/usr/local/opt/llvm/bin/clang++",
    ]
    if sys.platform == "linux":
        candidates += ["nvc++", "g++", "clang++"]
    else:
        candidates += ["clang++", "g++"]
    for cand in candidates:
        if shutil.which(cand) or Path(cand).is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# Per-worker: render sources, compile once, run a batch of seeds
# ---------------------------------------------------------------------------


def _render(src_path: Path, namespace: str, dest: Path) -> None:
    text = src_path.read_text()
    if PLACEHOLDER in text:
        text = text.replace(PLACEHOLDER, namespace)
    text = text.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    text = text.replace('#include "../types.h"', '#include "types.h"')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def _compile(team_a_src: Path, team_b_src: Path, work_dir: Path, compiler: str) -> Path:
    a_dir = work_dir / "src" / "a"
    b_dir = work_dir / "src" / "b"
    _render(team_a_src, "TeamA", a_dir / "ai.cpp")
    _render(team_b_src, "TeamB", b_dir / "ai.cpp")
    binary = work_dir / "swarmevolve"
    # Compile-flag policy (see docs/adr/0001-compile-flag-policy.md):
    #   * Keep -Werror on everything except *style* warnings. Correctness
    #     failures (shadowing, pedantic violations, sign-compare, ABI
    #     mismatches, uninitialized reads) stay hard errors.
    #   * Explicitly suppress unused-{variable,parameter,function,
    #     but-set-variable,const-variable}: both SOTA LLMs we evaluated
    #     (Claude Sonnet 4.5, Claude Opus 4.7) reliably scaffold
    #     bookkeeping variables in their first draft; rejecting those as
    #     compile failures rather than as suboptimal tactics made the
    #     evolutionary loop unable to accept *any* candidate, which
    #     defeats RQ2/RQ3 before they can even run. Rejecting unused
    #     scaffolding at the compiler level is a style judgement, not a
    #     correctness one — so we let it through here and rely on the
    #     LLM-driven retry loop + prompt guidance instead.
    cmd = [
        compiler,
        "-std=c++17", "-O2",
        "-Wall", "-Wextra", "-Wshadow", "-Wpedantic", "-Werror",
        "-Wno-unknown-pragmas",
        "-Wno-unused-variable", "-Wno-unused-parameter",
        "-Wno-unused-function", "-Wno-unused-but-set-variable",
        "-Wno-unused-const-variable",
        f"-I{REPO_ROOT / 'src'}",
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o", str(binary),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CompileError(
            f"compile failed rc={proc.returncode}\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )
    return binary


class CompileError(RuntimeError):
    """Raised when the one-time per-worker compile fails."""


def _run_one_match(binary: Path, seed: int, max_ticks: int, timeout: float) -> dict[str, Any]:
    """Execute a single match; return a dict of match stats."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(binary), "--seed", str(seed), "--max-ticks", str(max_ticks)],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "seed": seed, "outcome": "TIMEOUT", "score": 0.0,
            "ticks": 0, "wall_ms": int(timeout * 1000),
            "a_alive": 0, "b_alive": 0, "return_code": -1,
        }
    wall_ms = int((time.monotonic() - t0) * 1000)

    rc = proc.returncode
    if rc not in _OUTCOME_BY_RC:
        return {
            "seed": seed, "outcome": "CRASH", "score": 0.0,
            "ticks": 0, "wall_ms": wall_ms,
            "a_alive": 0, "b_alive": 0, "return_code": rc,
        }

    ticks = 0
    a_alive = 0
    b_alive = 0
    lines = [line for line in proc.stdout.strip().splitlines() if line]
    if lines:
        try:
            fields = dict(tok.split("=", 1) for tok in lines[-1].split())
            ticks = int(fields.get("ticks", 0))
            a_alive = int(fields.get("a_alive", 0))
            b_alive = int(fields.get("b_alive", 0))
        except ValueError:
            pass
    return {
        "seed": seed,
        "outcome": _OUTCOME_BY_RC[rc],
        "score": _SCORE_BY_RC[rc],
        "ticks": ticks,
        "wall_ms": wall_ms,
        "a_alive": a_alive,
        "b_alive": b_alive,
        "return_code": rc,
    }


def _worker_batch(
    team_a_src: str,
    team_b_src: str,
    seeds: list[int],
    compiler: str,
    max_ticks: int,
    timeout: float,
    work_dir: str,
) -> list[dict[str, Any]]:
    """Top-level so ProcessPoolExecutor can pickle it.

    Each worker gets a non-overlapping slice of seeds. It compiles once
    into its private ``work_dir`` and then runs all of them.
    """
    wd = Path(work_dir)
    wd.mkdir(parents=True, exist_ok=True)
    binary = _compile(Path(team_a_src), Path(team_b_src), wd, compiler)
    results: list[dict[str, Any]] = []
    for seed in seeds:
        results.append(_run_one_match(binary, seed, max_ticks, timeout))
    return results


# ---------------------------------------------------------------------------
# Bootstrap confidence interval (stdlib only)
# ---------------------------------------------------------------------------


def _bootstrap_ci(
    scores: list[float],
    *,
    iterations: int = 2000,
    ci_seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile-bootstrap two-sided ``1 - alpha`` CI for the mean.

    Uses a fresh :class:`random.Random` (not the global) so callers
    can parallelise without worrying about shared state, and so the
    result is reproducible given ``ci_seed``.
    """
    if len(scores) < 2:
        raise ValueError("bootstrap CI needs at least 2 samples")
    rng = random.Random(ci_seed)
    n = len(scores)
    means: list[float] = []
    for _ in range(iterations):
        # sample with replacement
        resample_sum = 0.0
        for _j in range(n):
            resample_sum += scores[rng.randrange(n)]
        means.append(resample_sum / n)
    means.sort()
    lo_idx = int(math.floor((alpha / 2) * iterations))
    hi_idx = int(math.ceil((1 - alpha / 2) * iterations)) - 1
    hi_idx = max(0, min(iterations - 1, hi_idx))
    return means[lo_idx], means[hi_idx]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_fitness(
    team_a_src: Path | str,
    team_b_src: Path | str,
    *,
    n_matches: int = 100,
    seed_base: int = 0,
    workers: int | None = None,
    max_ticks: int = 1000,
    timeout: float = 10.0,
    compiler: str | None = None,
    scratch_root: Path | str | None = None,
    bootstrap_iterations: int = 2000,
) -> FitnessResult:
    """Evaluate N seeded matches between ``team_a_src`` and ``team_b_src``.

    Parameters
    ----------
    team_a_src, team_b_src
        Paths to the two AI source files. Either frozen baselines (with
        ``TEAM_NS_PLACEHOLDER``) or already-wrapped namespaces.
    n_matches
        Number of matches to run. Seeds are ``[seed_base, seed_base +
        n_matches)``.
    seed_base
        First seed. Passing the same ``(srcs, seed_base, n_matches)``
        reproduces byte-identical per-match results.
    workers
        Number of worker processes. ``None`` → ``min(os.cpu_count(),
        n_matches)``. Matches are evenly partitioned across workers;
        each worker compiles once.
    max_ticks, timeout
        Forwarded to the engine / match wrapper.
    compiler
        Override path to the C++ compiler. ``None`` → auto-detect.
    scratch_root
        Parent directory for per-worker build dirs. Defaults to a
        system temp dir. Callers pass an explicit path when they want
        the build artifacts preserved (e.g. the orchestrator's
        ``fitness_run/`` subdir).
    bootstrap_iterations
        Iterations for the bootstrap CI. Default 2000 (~instant).

    Returns
    -------
    FitnessResult
        Populated aggregate + per-match list.

    Raises
    ------
    FileNotFoundError
        If either source file is missing.
    CompileError
        If the one-time compile fails in any worker. (We don't swallow
        this: a compile failure is a pipeline-level bug, not a
        per-match failure.)
    RuntimeError
        If no C++ compiler can be located.
    """
    team_a = Path(team_a_src).resolve()
    team_b = Path(team_b_src).resolve()
    if not team_a.is_file():
        raise FileNotFoundError(f"team A source not found: {team_a}")
    if not team_b.is_file():
        raise FileNotFoundError(f"team B source not found: {team_b}")
    if n_matches < 1:
        raise ValueError(f"n_matches must be >= 1, got {n_matches}")

    cxx = compiler or _find_compiler()
    if cxx is None:
        raise RuntimeError(
            "no C++ compiler found; set $CXX or install clang++/g++",
        )

    if scratch_root is None:
        scratch_root_path = Path(
            os.environ.get("SWARM_FITNESS_SCRATCH")
            or (Path.home() / ".pytest-sandbox" / "fitness")
        )
    else:
        scratch_root_path = Path(scratch_root)
    scratch_root_path.mkdir(parents=True, exist_ok=True)

    max_workers = max(1, min(workers or (os.cpu_count() or 1), n_matches))
    seeds = [seed_base + i for i in range(n_matches)]
    # Round-robin seed assignment → balanced work even when n_matches
    # doesn't divide workers evenly. Deterministic wrt. (workers, n_matches).
    partitions: list[list[int]] = [[] for _ in range(max_workers)]
    for idx, seed in enumerate(seeds):
        partitions[idx % max_workers].append(seed)

    t0 = time.monotonic()

    results: list[dict[str, Any]] = []
    if max_workers == 1:
        # Keep the single-worker path synchronous so exceptions surface
        # with their original traceback (easier debugging + avoids the
        # ProcessPoolExecutor pickle boundary entirely).
        wd = scratch_root_path / f"w0_{os.getpid()}"
        results = _worker_batch(
            str(team_a), str(team_b), partitions[0], cxx,
            max_ticks, timeout, str(wd),
        )
    else:
        with _futures.ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _worker_batch,
                    str(team_a), str(team_b), part, cxx,
                    max_ticks, timeout,
                    str(scratch_root_path / f"w{i}_{os.getpid()}"),
                )
                for i, part in enumerate(partitions)
                if part  # skip empty partitions when workers > n_matches
            ]
            for fut in _futures.as_completed(futures):
                results.extend(fut.result())

    # Sort by seed so the per-match list is deterministic regardless of
    # worker completion order.
    results.sort(key=lambda r: r["seed"])

    wins_a = sum(1 for r in results if r["outcome"] == "A_WIN")
    wins_b = sum(1 for r in results if r["outcome"] == "B_WIN")
    draws = sum(1 for r in results if r["outcome"] == "DRAW")
    invalid = sum(1 for r in results if r["outcome"] in ("TIMEOUT", "CRASH"))
    scores = [r["score"] for r in results]
    mean = statistics.fmean(scores) if scores else 0.0
    stdev = statistics.pstdev(scores) if len(scores) >= 2 else 0.0

    ci_low: float | None = None
    ci_high: float | None = None
    if len(scores) >= 2:
        # ci_seed derived from seed_base so re-runs are reproducible
        # without adding a new knob.
        ci_seed = int(
            hashlib.sha256(f"ci:{seed_base}:{n_matches}".encode()).hexdigest()[:8],
            16,
        )
        ci_low, ci_high = _bootstrap_ci(
            scores, iterations=bootstrap_iterations, ci_seed=ci_seed,
        )

    wall = time.monotonic() - t0
    return FitnessResult(
        team_a_path=str(team_a),
        team_b_path=str(team_b),
        n_matches=n_matches,
        seed_base=seed_base,
        wins_a=wins_a,
        wins_b=wins_b,
        draws=draws,
        invalid=invalid,
        mean=mean,
        stdev=stdev,
        ci_low=ci_low,
        ci_high=ci_high,
        wall_seconds=round(wall, 3),
        workers=max_workers,
        compiler=cxx,
        per_match=results,
    )


# ---------------------------------------------------------------------------
# CLI (mostly for smoke testing; orchestrator.py is the documented entry)
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="fitness", description=__doc__.splitlines()[0])
    p.add_argument("--team-a", required=True, type=Path)
    p.add_argument("--team-b", required=True, type=Path)
    p.add_argument("--n-matches", type=int, default=20)
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--max-ticks", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)
    try:
        result = evaluate_fitness(
            args.team_a, args.team_b,
            n_matches=args.n_matches,
            seed_base=args.seed_base,
            workers=args.workers,
            max_ticks=args.max_ticks,
            timeout=args.timeout,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"fitness error: {exc}", file=sys.stderr)
        return 2
    except CompileError as exc:
        print(f"compile failed: {exc}", file=sys.stderr)
        return 3

    # Print a compact summary; the full dict is a lot.
    summary = {k: v for k, v in result.to_dict().items() if k != "per_match"}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


__all__ = ["FitnessResult", "CompileError", "evaluate_fitness"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
