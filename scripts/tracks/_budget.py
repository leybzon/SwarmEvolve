"""Per-track token budget guard (M20).

Prevents a runaway evolutionary loop from exhausting the LLM API
budget by summing ``tokens_input`` + ``tokens_output`` across all
completed lineages inside a track root and raising
:class:`BudgetExceeded` when the cap is crossed.

The guard is deliberately *cooperative*: it reads the on-disk state
written by ``evolve.py`` (the same ``state.json`` files summarised
into a track manifest) and enforces the cap between generations /
between lineages. There is no attempt to interrupt an in-flight
``evolve.main`` call — the granularity of "one generation, then
check the budget" is sufficient for the 150-generation tracks this
project targets.

Usage
-----
>>> from tracks._budget import TokenBudget
>>> budget = TokenBudget(max_tokens=1_000_000)
>>> budget.check(track_root)  # raises if total crosses cap
>>> budget.total(track_root)
123456

The top-level ``enforce`` helper is the CLI surface: track runners
call ``budget.enforce(track_root, logger=_LOG)`` after every lineage
step so the guard aborts *before* the next expensive LLM call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_LOG = logging.getLogger("swarmevolve.tracks.budget")


class BudgetExceeded(RuntimeError):
    """Raised when the cumulative token cap is exceeded.

    Carries the cap and the observed total so callers can format a
    useful error message or bubble the counts up to a manifest.
    """

    def __init__(self, *, max_tokens: int, total_tokens: int, track_root: Path) -> None:
        super().__init__(
            f"token budget exceeded: {total_tokens} > {max_tokens} "
            f"in {track_root}"
        )
        self.max_tokens = max_tokens
        self.total_tokens = total_tokens
        self.track_root = track_root


@dataclass(frozen=True)
class TokenBudget:
    """A single-track token cap.

    ``max_tokens`` is a hard upper bound on the sum of
    ``tokens_input + tokens_output`` across every ``state.json`` that
    lives anywhere under the track root (lineage roots, per-step
    subdirs, co-evolution sub-lineages — anything :func:`_iter_states`
    can find). A value of ``0`` disables enforcement.
    """

    max_tokens: int

    def iter_states(self, track_root: Path) -> Iterator[dict]:
        yield from _iter_states(track_root)

    def total(self, track_root: Path) -> int:
        return _sum_tokens(track_root)

    def check(self, track_root: Path) -> int:
        """Return the current total; raise :class:`BudgetExceeded` if
        the cap has been crossed. A cap of ``0`` is a no-op.
        """
        total = _sum_tokens(track_root)
        if self.max_tokens and total > self.max_tokens:
            raise BudgetExceeded(
                max_tokens=self.max_tokens,
                total_tokens=total,
                track_root=track_root,
            )
        return total

    def enforce(
        self,
        track_root: Path,
        *,
        logger: logging.Logger | None = None,
    ) -> int:
        """Check the budget and log the current utilisation."""
        total = self.check(track_root)
        if self.max_tokens:
            log = logger or _LOG
            pct = 100.0 * total / self.max_tokens if self.max_tokens else 0.0
            log.info(
                "budget: %d / %d tokens (%.1f%%) in %s",
                total, self.max_tokens, pct, track_root,
            )
        return total


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_states(track_root: Path) -> Iterator[dict]:
    """Yield parsed ``state.json`` payloads under ``track_root``.

    Walks the tree once; tolerates malformed JSON (logs and skips).
    The iteration order is lexicographic on the relative path so the
    sum is deterministic across filesystems.
    """
    if not track_root.is_dir():
        return
    # rglob is documented to be unordered; sort for determinism.
    paths = sorted(track_root.rglob("state.json"))
    for p in paths:
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:  # pragma: no cover
            _LOG.warning("skipping unreadable state.json at %s: %s", p, e)
            continue


def _sum_tokens(track_root: Path) -> int:
    total = 0
    for st in _iter_states(track_root):
        total += int(st.get("tokens_input") or 0)
        total += int(st.get("tokens_output") or 0)
    return total


__all__ = [
    "BudgetExceeded",
    "TokenBudget",
]
