#!/usr/bin/env python3
"""Stub fitness evaluator (M7 scaffold; full impl in M9).

Defines the ``evaluate_fitness`` API shape and the ``FitnessResult``
dataclass so downstream code (M10 evolutionary loop, M12 tournament)
can import stable symbols before the real multiprocessing + bootstrap
CI machinery lands in M9.

The stub short-circuits in the only way that keeps tests honest: it
raises ``NotImplementedError``. Tests that want to exercise the
orchestrator ``evaluate`` sub-command path should inject a fake via
monkeypatching once M9 lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FitnessResult:
    """Aggregate fitness over ``n_matches`` matches.

    ``mean`` is the mean of per-match score (+1 A wins, -1 B wins, 0 draw).
    ``ci_low``/``ci_high`` are the 95% bootstrap confidence interval
    endpoints once M9 implements resampling; they are ``None`` in the
    stub.
    """

    team_a_path: str
    team_b_path: str
    n_matches: int
    seed_base: int
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    mean: float = 0.0
    stdev: float = 0.0
    ci_low: float | None = None
    ci_high: float | None = None
    per_match: list[dict[str, object]] = field(default_factory=list)


def evaluate_fitness(
    team_a_src: Path | str,
    team_b_src: Path | str,
    *,
    n_matches: int = 100,
    seed_base: int = 0,
    workers: int | None = None,
) -> FitnessResult:
    """Placeholder API. M9 replaces the body with a real evaluator."""
    raise NotImplementedError(
        "fitness evaluator not implemented yet; planned for milestone M9. "
        "See IMPLEMENTATION_PLAN.md §11."
    )


__all__ = ["FitnessResult", "evaluate_fitness"]
