"""SwarmEvolve M19 track runners.

Each track encodes a specific experimental setup on top of
``scripts/evolve.py`` and ``scripts/tournament.py``:

- :mod:`scripts.tracks.track_a` — LLM vs. frozen ``pursuit_v1`` baseline
  for ``N`` generations, multi-seed.
- :mod:`scripts.tracks.track_b` — monotonic self-play; each generation
  is evaluated against the previous generation's champion. A final
  round-robin (via ``tournament.run_tournament``) checks monotonic
  improvement across the chain.
- :mod:`scripts.tracks.track_c` — A-vs-B co-evolution with AARs and
  periodic external-yardstick evaluations against ``pursuit_v1``.

All three share a CLI skeleton (see :mod:`scripts.tracks._common`) and
produce a JSON manifest under the track's run directory. Resume
semantics delegate to the underlying ``evolve.main(--resume …)`` pass.
"""

from __future__ import annotations

__all__ = ["_common", "track_a", "track_b", "track_c"]
