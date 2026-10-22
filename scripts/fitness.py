#!/usr/bin/env python3
"""Fitness evaluator (M9).

Runs N seeded matches between two AI sources and returns an aggregate
:class:`FitnessResult` with win/loss/draw counts, per-match scores,
mean, stdev, and a bootstrap 95% confidence interval on the mean.

Design notes
------------
* **One batch runner, two backends.** ``scripts/eval_batch.py`` injects
  loop guards, compiles once with ``compile_flags.eval_flags`` and plays
  a list of seeds. ``backend="sandbox"`` (the default) runs it in the
  eval container (``docker/Dockerfile.eval``) and splits the seeds across
  parallel containers of ``container_cpus`` CPUs each; ``backend="host"``
  runs it in-process. Both produce the same per-match rows.
* **Sandbox by default.** LLM-written code is compiled and played only in
  the container. ``backend=None`` resolves to ``$SWARMEVOLVE_FITNESS_BACKEND``
  or ``"sandbox"``; the test suite sets the variable to ``host`` for its
  trusted fixtures. Every result carries ``backend`` and a ``platform``
  fingerprint (``scripts/platform_fingerprint.py``).
* **Deterministic reductions.** Seed sequences are contiguous
  ``[seed_base, seed_base + n_matches)`` so two runs with the same
  ``(team_a, team_b, seed_base, n_matches)`` produce byte-identical
  per-match rows (apart from ``wall_ms``) and therefore identical
  aggregates. The bootstrap CI uses a second, orthogonal seed
  (``ci_seed``) derived from ``seed_base`` so it, too, reproduces.
* **Score convention**: +1 for A_WIN, -1 for B_WIN, 0 for DRAW. A match
  in one of the invalid categories (GUARD_FAIL, COMPILE_FAIL, CRASH,
  TIMEOUT) is counted in ``invalid`` rather than ``draws``. When the
  caller names the program under test (``under_test="A"`` or ``"B"``),
  an invalid match scores as a loss for that side; otherwise (symmetric
  uses such as tournaments) it scores 0. Evolution loops always pass
  ``under_test`` so a crashing candidate can never score as well as a
  draw.
* **Build failures.** A guard or compile failure fills every seed with
  GUARD_FAIL / COMPILE_FAIL rows. By default :func:`evaluate_fitness`
  then raises :class:`CompileError` (:class:`GuardError` for the guard
  case) with the full result attached as ``exc.result``; pass
  ``raise_on_build_failure=False`` to get the result back instead.

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
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_batch  # noqa: E402
import platform_fingerprint  # noqa: E402

ENGINE_DIR = REPO_ROOT / "src"
HARNESS_DIR = REPO_ROOT / "scripts"
PLACEHOLDER = "TEAM_NS_PLACEHOLDER"

BACKENDS = ("sandbox", "host")
BACKEND_ENV = "SWARMEVOLVE_FITNESS_BACKEND"
# CPUs (and concurrent matches) per eval container; see
# reports/setup/EVAL_TIMING.md for the measurements behind the default.
DEFAULT_CONTAINER_CPUS = 4

INVALID_OUTCOMES = eval_batch.INVALID_OUTCOMES
BUILD_FAILURES = ("GUARD_FAIL", "COMPILE_FAIL")


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
    ``invalid``   count of matches in an invalid category
                  (GUARD_FAIL, COMPILE_FAIL, CRASH, TIMEOUT). They
                  score as a loss for ``under_test`` when it is set,
                  else 0.
    ``under_test`` "A", "B" or ``None``: the side whose program is
                  being evaluated.
    ``per_match`` list of per-match dicts: ``{seed, outcome, score,
                  ticks, wall_ms, a_alive, b_alive, return_code}``, plus
                  ``failed_side`` on GUARD_FAIL / COMPILE_FAIL rows.
    ``backend``   "sandbox" or "host".
    ``platform``  fingerprint of where the matches were compiled and
                  played (``platform_fingerprint.from_status``), with
                  its ``label`` against ``prereg/PLATFORM.json``.
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
    under_test: str | None = None
    backend: str = "host"
    platform: dict[str, Any] = field(default_factory=dict)

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
# Rendering, errors, backend selection
# ---------------------------------------------------------------------------


def _render(src_path: Path, namespace: str, dest: Path) -> None:
    text = src_path.read_text()
    if PLACEHOLDER in text:
        text = text.replace(PLACEHOLDER, namespace)
    text = text.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    text = text.replace('#include "../types.h"', '#include "types.h"')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


class CompileError(RuntimeError):
    """A source failed to compile (or, as :class:`GuardError`, to take loop
    guards). ``result`` holds the evaluation with every seed marked invalid."""

    def __init__(self, message: str, result: FitnessResult | None = None):
        super().__init__(message)
        self.result = result


class GuardError(CompileError):
    """Loop-guard injection refused a source."""


class StaleImageError(RuntimeError):
    """The eval image's baked harness differs from the repo; rebuild it."""


def _invalid_score(under_test: str | None) -> float:
    """Score of an invalid match: a loss for the side under test."""
    return eval_batch.invalid_score(under_test)


def resolve_backend(backend: str | None) -> str:
    """``backend``, else ``$SWARMEVOLVE_FITNESS_BACKEND``, else ``"sandbox"``."""
    chosen = backend or os.environ.get(BACKEND_ENV) or "sandbox"
    if chosen not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}, got {chosen!r}")
    return chosen


def split_seeds(seeds: list[int], parts: int) -> list[list[int]]:
    """Round-robin partition, deterministic in (seeds, parts); no empty parts."""
    parts = max(1, min(parts, len(seeds)))
    out: list[list[int]] = [[] for _ in range(parts)]
    for idx, seed in enumerate(seeds):
        out[idx % parts].append(seed)
    return out


# ---------------------------------------------------------------------------
# Backends: each returns (rows, statuses, extra platform fields)
# ---------------------------------------------------------------------------


def _run_host(
    a_src: Path,
    b_src: Path,
    seeds: list[int],
    eval_dir: Path,
    *,
    compiler: str,
    workers: int,
    batch_kwargs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path | None]:
    out = eval_dir / "out"
    status = eval_batch.run_batch(
        team_a=a_src,
        team_b=b_src,
        seeds=seeds,
        out=out,
        work=eval_dir / "work",
        engine_dir=ENGINE_DIR,
        harness_dir=HARNESS_DIR,
        compiler=compiler,
        jobs=workers,
        **batch_kwargs,
    )
    trace = out / "trace.jsonl"
    return eval_batch.read_results(out), [status], trace if trace.is_file() else None


def _run_sandbox(
    a_src: Path,
    b_src: Path,
    seeds: list[int],
    eval_dir: Path,
    *,
    image_id: str,
    workers: int,
    container_cpus: int,
    batch_kwargs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path | None]:
    import sandbox

    cpus = max(1, min(container_cpus, workers))
    parts = split_seeds(seeds, -(-workers // cpus))

    def one(i: int, part: list[int]) -> sandbox.BatchResult:
        record_seed = batch_kwargs["record_seed"]
        return sandbox.run_batch_in_sandbox(
            a_src,
            b_src,
            part,
            eval_dir / f"c{i}",
            image_id=image_id,
            cpus=cpus,
            jobs=cpus,
            max_ticks=batch_kwargs["max_ticks"],
            timeout=batch_kwargs["timeout"],
            under_test=batch_kwargs["under_test"],
            record_seed=record_seed if record_seed in part else None,
            drones_a=batch_kwargs["drones_a"],
            drones_b=batch_kwargs["drones_b"],
            arena_scale=batch_kwargs["arena_scale"],
        )

    with _futures.ThreadPoolExecutor(max_workers=len(parts)) as pool:
        batches = list(pool.map(one, range(len(parts)), parts))
    expected = eval_batch.harness_hashes(ENGINE_DIR, HARNESS_DIR)
    for b in batches:
        if b.status.get("harness_sha256") != expected:
            raise StaleImageError(
                f"eval image {image_id} was built from different harness files than the "
                "repo; rebuild it with `make docker-build`"
            )
    rows = [r for b in batches for r in b.rows]
    trace = next((b.trace_path for b in batches if b.trace_path is not None), None)
    return rows, [b.status for b in batches], trace


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
    compiler: str | None = None,
    scratch_root: Path | str | None = None,
    bootstrap_iterations: int = 2000,
    under_test: str | None = None,
    record_trace_for_match_idx: int | None = None,
    trace_output_path: Path | str | None = None,
    backend: str | None = None,
    image: str | None = None,
    container_cpus: int = DEFAULT_CONTAINER_CPUS,
    drones_a: int | None = None,
    drones_b: int | None = None,
    arena_scale: float | None = None,
    raise_on_build_failure: bool = True,
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
        CPUs to use. ``None`` → ``os.cpu_count()``, capped at
        ``n_matches``. Host: concurrent matches after one compile.
        Sandbox: ``ceil(workers / container_cpus)`` containers, each
        compiling once and playing ``container_cpus`` matches at a time.
    max_ticks, timeout
        Forwarded to the engine / per-match wall-clock limit (seconds).
    compiler
        Host backend only: override path to the C++ compiler. ``None`` →
        auto-detect. The sandbox always uses the image's g++.
    scratch_root
        Parent directory for the per-evaluation work directory. Defaults
        to ``$SWARM_FITNESS_SCRATCH`` or ``~/.pytest-sandbox/fitness``
        (under ``$HOME`` so Colima can bind-mount it). The work directory
        is removed afterwards.
    bootstrap_iterations
        Iterations for the bootstrap CI. Default 2000 (~instant).
    under_test
        ``"A"`` or ``"B"``: the side whose program is being evaluated.
        Invalid matches score as a loss for that side. ``None``
        (symmetric comparison) scores them 0.
    record_trace_for_match_idx, trace_output_path
        When both are set, match number ``record_trace_for_match_idx``
        (seed ``seed_base + idx``) also writes a schema-v2 trace to
        ``trace_output_path``. Recording does not change the outcome.
    backend
        ``"sandbox"`` or ``"host"``; ``None`` → :func:`resolve_backend`.
    image
        Sandbox only: eval image tag; resolved to its ID once per call.
    container_cpus
        Sandbox only: ``--cpus`` and concurrent matches per container.
    drones_a, drones_b, arena_scale
        Team sizes and arena multiplier; ``None`` keeps the engine default.
    raise_on_build_failure
        Raise :class:`CompileError` / :class:`GuardError` (with
        ``exc.result``) on a build failure instead of returning.

    Raises
    ------
    FileNotFoundError
        If either source file is missing.
    CompileError
        On a guard or compile failure, unless ``raise_on_build_failure``
        is false.
    RuntimeError
        If no C++ compiler can be located (host backend).
    sandbox.SandboxError
        Sandbox backend: no runtime, missing image, or a container that
        produced no usable result (an infrastructure failure).
    StaleImageError
        Sandbox backend: the image was built from other harness files.
    """
    team_a = Path(team_a_src).resolve()
    team_b = Path(team_b_src).resolve()
    if not team_a.is_file():
        raise FileNotFoundError(f"team A source not found: {team_a}")
    if not team_b.is_file():
        raise FileNotFoundError(f"team B source not found: {team_b}")
    if n_matches < 1:
        raise ValueError(f"n_matches must be >= 1, got {n_matches}")
    if under_test not in (None, "A", "B"):
        raise ValueError(f"under_test must be 'A', 'B' or None, got {under_test!r}")
    chosen = resolve_backend(backend)
    record_seed: int | None = None
    if record_trace_for_match_idx is not None and trace_output_path is not None:
        if not 0 <= record_trace_for_match_idx < n_matches:
            raise ValueError(
                f"record_trace_for_match_idx must be in [0, {n_matches}), "
                f"got {record_trace_for_match_idx}"
            )
        record_seed = seed_base + record_trace_for_match_idx

    if scratch_root is None:
        scratch_root_path = Path(
            os.environ.get("SWARM_FITNESS_SCRATCH") or (Path.home() / ".pytest-sandbox" / "fitness")
        )
    else:
        scratch_root_path = Path(scratch_root)
    scratch_root_path.mkdir(parents=True, exist_ok=True)

    max_workers = max(1, min(workers or (os.cpu_count() or 1), n_matches))
    seeds = [seed_base + i for i in range(n_matches)]
    batch_kwargs: dict[str, Any] = {
        "max_ticks": max_ticks,
        "timeout": timeout,
        "under_test": under_test,
        "record_seed": record_seed,
        "drones_a": drones_a,
        "drones_b": drones_b,
        "arena_scale": arena_scale,
    }

    t0 = time.monotonic()
    eval_dir = Path(tempfile.mkdtemp(prefix=f"eval_{chosen}_", dir=scratch_root_path))
    try:
        a_src = eval_dir / "a.cpp"
        b_src = eval_dir / "b.cpp"
        _render(team_a, "TeamA", a_src)
        _render(team_b, "TeamB", b_src)
        if chosen == "host":
            cxx = compiler or _find_compiler()
            if cxx is None:
                raise RuntimeError("no C++ compiler found; set $CXX or install clang++/g++")
            results, statuses, trace = _run_host(
                a_src,
                b_src,
                seeds,
                eval_dir,
                compiler=cxx,
                workers=max_workers,
                batch_kwargs=batch_kwargs,
            )
            image_tag = image_id = None
        else:
            import sandbox

            image_tag = image or sandbox.DEFAULT_IMAGE
            image_id = sandbox.resolve_image_id(image_tag)
            results, statuses, trace = _run_sandbox(
                a_src,
                b_src,
                seeds,
                eval_dir,
                image_id=image_id,
                workers=max_workers,
                container_cpus=container_cpus,
                batch_kwargs=batch_kwargs,
            )
            cxx = statuses[0].get("compiler", "g++")
        if trace is not None and trace_output_path is not None:
            dest = Path(trace_output_path).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(trace, dest)
        build_log = ""
        for name in ("compile.log", "inject_a.log", "inject_b.log"):
            for log in sorted(eval_dir.glob(f"**/out/{name}")):
                build_log += f"--- {name}\n{log.read_text()[-2000:]}\n"
                break
    finally:
        shutil.rmtree(eval_dir, ignore_errors=True)

    fingerprints = [
        platform_fingerprint.from_status(st, backend=chosen, image=image_tag, image_id=image_id)
        for st in statuses
    ]
    if any(fp != fingerprints[0] for fp in fingerprints):
        raise RuntimeError("batches of one evaluation reported different platforms")
    platform = dict(fingerprints[0])
    platform["label"] = platform_fingerprint.platform_label(platform)

    results.sort(key=lambda r: r["seed"])

    wins_a = sum(1 for r in results if r["outcome"] == "A_WIN")
    wins_b = sum(1 for r in results if r["outcome"] == "B_WIN")
    draws = sum(1 for r in results if r["outcome"] == "DRAW")
    invalid = sum(1 for r in results if r["outcome"] in INVALID_OUTCOMES)
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
    result = FitnessResult(
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
        under_test=under_test,
        backend=chosen,
        platform=platform,
    )
    failure = next((r for r in results if r["outcome"] in BUILD_FAILURES), None)
    if failure is not None and raise_on_build_failure:
        error = GuardError if failure["outcome"] == "GUARD_FAIL" else CompileError
        raise error(
            f"{failure['outcome'].lower().replace('_', ' ')}: side={failure.get('failed_side')} "
            f"rc={failure['return_code']}\n{build_log}",
            result,
        )
    return result


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
    p.add_argument("--backend", choices=BACKENDS, default=None)
    p.add_argument("--container-cpus", type=int, default=DEFAULT_CONTAINER_CPUS)
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
            backend=args.backend,
            container_cpus=args.container_cpus,
        )
    except CompileError as exc:
        print(f"compile failed: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"fitness error: {exc}", file=sys.stderr)
        return 2

    # Print a compact summary; the full dict is a lot.
    summary = {k: v for k, v in result.to_dict().items() if k != "per_match"}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


__all__ = [
    "CompileError",
    "FitnessResult",
    "GuardError",
    "StaleImageError",
    "evaluate_fitness",
    "resolve_backend",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
