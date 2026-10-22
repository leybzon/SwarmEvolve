#!/usr/bin/env python3
"""Fitness evaluator (M9).

Runs N seeded matches between two AI sources and returns an aggregate
:class:`FitnessResult` with win/loss/draw counts, per-match scores,
mean, stdev, and a bootstrap 95% confidence interval on the mean.

Design notes
------------
* **Matches run in the canonical eval container** (frozen-snapshot
  execution change 2, see legacy/m25/CHANGES.md). M25 compiled and ran
  both programs on the host. The snapshot hands the seeds to the shared
  container runner (``scripts/fitness.py``, ``backend="sandbox"``) and
  takes back only each match's outcome; scores, aggregates and the
  error policy below are still computed here, as in M25. The host path
  is gone so LLM-written code cannot run on the host.
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

import hashlib
import json
import logging
import math
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPTS = REPO_ROOT / "scripts"

# M25 mapped the engine's return code 0/1/2 to these outcomes and scores;
# the container reports the outcome itself. CRASH and TIMEOUT score 0.
_SCORE_BY_OUTCOME = {"A_WIN": 1.0, "B_WIN": -1.0, "DRAW": 0.0}
_INVALID_OUTCOMES = ("TIMEOUT", "CRASH")
_BUILD_FAILURES = ("GUARD_FAIL", "COMPILE_FAIL")

_LOG = logging.getLogger("swarmevolve.legacy_m25.fitness")


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
    backend: str = "sandbox"
    platform: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation. Used by ``fitness.json``."""
        d = asdict(self)
        # asdict already copies; we just pin ordering keys deterministically.
        return d


# ---------------------------------------------------------------------------
# Match execution: the canonical eval container
# ---------------------------------------------------------------------------


class CompileError(RuntimeError):
    """A source failed to take loop guards or to compile in the container."""


class InfraError(RuntimeError):
    """The container produced no usable result; says nothing about the code."""


def _canonical_fitness() -> Any:
    """The shared container runner, ``scripts/fitness.py``."""
    if str(CANONICAL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(CANONICAL_SCRIPTS))
    import fitness as canonical  # the top-level scripts/fitness.py, not this module

    return canonical


def _play_in_container(
    team_a: Path,
    team_b: Path,
    *,
    n_matches: int,
    seed_base: int,
    workers: int | None,
    max_ticks: int,
    timeout: float,
    scratch_root: Path | str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
    """Play seeds ``[seed_base, seed_base + n_matches)`` in the eval container.

    Returns (per-match rows, platform fingerprint, workers, compiler).
    Raises :class:`CompileError` on a guard or compile failure and
    :class:`InfraError` on anything else that stops the batch.
    """
    canonical = _canonical_fitness()
    try:
        res = canonical.evaluate_fitness(
            team_a,
            team_b,
            n_matches=n_matches,
            seed_base=seed_base,
            workers=workers,
            max_ticks=max_ticks,
            timeout=timeout,
            scratch_root=scratch_root,
            under_test=None,
            backend="sandbox",
            raise_on_build_failure=True,
        )
    except canonical.CompileError as exc:
        raise CompileError(str(exc)) from exc
    except Exception as exc:
        raise InfraError(f"container evaluation failed: {type(exc).__name__}: {exc}") from exc
    return list(res.per_match), dict(res.platform), int(res.workers), str(res.compiler)


def _legacy_row(row: dict[str, Any]) -> dict[str, Any]:
    """A container row in M25's per-match shape, scored by M25's rule."""
    outcome = row["outcome"]
    if outcome not in _SCORE_BY_OUTCOME and outcome not in _INVALID_OUTCOMES:
        raise InfraError(f"unexpected match outcome {outcome!r} for seed {row.get('seed')}")
    return {
        "seed": row["seed"],
        "outcome": outcome,
        "score": _SCORE_BY_OUTCOME.get(outcome, 0.0),
        "ticks": row.get("ticks", 0),
        "wall_ms": row.get("wall_ms", 0),
        "a_alive": row.get("a_alive", 0),
        "b_alive": row.get("b_alive", 0),
        "return_code": row.get("return_code", -1),
    }


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
    lo_idx = math.floor((alpha / 2) * iterations)
    hi_idx = math.ceil((1 - alpha / 2) * iterations) - 1
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
        CPUs for the container runner. ``None`` → ``os.cpu_count()``,
        capped at ``n_matches``.
    max_ticks, timeout
        Tick cap and per-match wall-clock limit, as in M25 (1000, 10 s).
    scratch_root
        Parent directory for the container runner's work directory.
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
        If either source fails guard injection or compilation. (We don't
        swallow this: a compile failure is a pipeline-level bug, not a
        per-match failure.)
    InfraError
        If the container produced no usable result.
    """
    team_a = Path(team_a_src).resolve()
    team_b = Path(team_b_src).resolve()
    if not team_a.is_file():
        raise FileNotFoundError(f"team A source not found: {team_a}")
    if not team_b.is_file():
        raise FileNotFoundError(f"team B source not found: {team_b}")
    if n_matches < 1:
        raise ValueError(f"n_matches must be >= 1, got {n_matches}")

    t0 = time.monotonic()

    rows, platform, max_workers, cxx = _play_in_container(
        team_a,
        team_b,
        n_matches=n_matches,
        seed_base=seed_base,
        workers=workers,
        max_ticks=max_ticks,
        timeout=timeout,
        scratch_root=scratch_root,
    )
    results = [_legacy_row(r) for r in rows]
    _LOG.info(
        "legacy-fitness seed_base=%d n=%d backend=sandbox platform=%s",
        seed_base,
        n_matches,
        platform.get("label", "unknown"),
    )

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
            scores,
            iterations=bootstrap_iterations,
            ci_seed=ci_seed,
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
        backend="sandbox",
        platform=platform,
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
            args.team_a,
            args.team_b,
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


__all__ = ["CompileError", "FitnessResult", "InfraError", "evaluate_fitness"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
