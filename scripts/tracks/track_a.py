#!/usr/bin/env python3
"""Track A — LLM vs. frozen ``pursuit_v1`` baseline, multi-seed.

Each ``--seeds`` value spins up one :mod:`evolve` lineage in
``<out-dir>/seed<N>/``. All lineages share the same frozen system
prompt and opponent (``src/baselines/pursuit_v1.cpp``). At the end, a
``track_a_manifest.json`` is written at the track root with one row
per seed (generations run, champion sha + fitness, token totals).

Resume (``--resume``) walks the same seed list: for each
``seed<N>/`` that already contains a ``state.json``, ``evolve`` is
invoked with ``--resume <seed-dir>``; missing subdirs are started
fresh. Seed-level ordering is sorted(seeds) for determinism.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the rest of scripts/ importable when run as a script.
_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tracks import _common  # noqa: E402
from tracks._common import (  # noqa: E402
    EXIT_OK, EXIT_INVALID_INPUT, EXIT_BUDGET_EXCEEDED,
    PURSUIT_V1,
    build_common_parser, forward_common_argv,
    invoke_evolve,
    read_state, summarise_lineage, write_manifest,
    BudgetExceeded, TokenBudget,
)

TRACK = "A"
_LOG = logging.getLogger("swarmevolve.tracks.a")


def _parse_seed_list(raw: str) -> list[int]:
    """Parse ``"1,2,3"`` or ``"1 2 3"`` into a sorted, deduplicated list."""
    if not raw:
        return []
    # Accept commas, whitespace, or both.
    tokens = [t for t in raw.replace(",", " ").split() if t]
    try:
        seeds = sorted({int(t) for t in tokens})
    except ValueError as e:
        raise SystemExit(f"--seeds: invalid integer in {raw!r}: {e}") from e
    return seeds


def build_parser():
    p = build_common_parser(
        prog="track_a",
        description="Track A: LLM vs. frozen pursuit_v1, multi-seed.",
    )
    p.add_argument("--seeds", required=True,
                   help="Comma- or space-separated list of seeds (e.g. '1,2,3')")
    p.add_argument("--as-team", choices=("A", "B"), default="A",
                   help="Which team slot the LLM plays (default A)")
    p.add_argument("--seed-ai", default=None,
                   help="Initial champion source (defaults to opponent=pursuit_v1)")
    p.add_argument("--opponent", default=None,
                   help=f"Opponent AI (.cpp). Defaults to {PURSUIT_V1}")
    return p


def _run_one_lineage(
    *, seed: int, seed_dir: Path, resume: bool,
    common_argv: list[str], opponent: Path, seed_ai: Path | None,
    as_team: str, generations: int,
) -> int:
    """Run (or resume) a single lineage under ``seed_dir``.

    Returns the ``evolve.main`` exit code. rc=30
    (``max_compile_failures`` hit) is treated as a soft failure: the
    lineage is recorded as exhausted but the track continues with the
    next seed. Any other non-zero rc is also surfaced (not raised) so
    one unhealthy seed cannot take down the whole track.
    """
    state_path = seed_dir / "state.json"
    if resume and state_path.is_file():
        _LOG.info("seed=%d: resuming %s", seed, seed_dir)
        return invoke_evolve(
            [
                "--resume", str(seed_dir),
                "--generations", str(generations),
            ],
            strict=False,
        )

    # Fresh run for this seed.
    seed_dir.mkdir(parents=True, exist_ok=True)
    fresh_argv = [
        "--opponent", str(opponent),
        "--as-team", as_team,
        "--seed", str(seed),
        "--generations", str(generations),
        "--out-dir", str(seed_dir),
    ]
    if seed_ai is not None:
        fresh_argv += ["--seed-ai", str(seed_ai)]
    fresh_argv += common_argv
    _LOG.info("seed=%d: starting fresh lineage %s", seed, seed_dir)
    return invoke_evolve(fresh_argv, strict=False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2
              else logging.INFO if args.verbose == 1
              else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    seeds = _parse_seed_list(args.seeds)
    if not seeds:
        _LOG.error("--seeds produced an empty list")
        return EXIT_INVALID_INPUT

    opponent = Path(args.opponent).resolve() if args.opponent else PURSUIT_V1.resolve()
    if not opponent.is_file():
        _LOG.error("opponent not found: %s", opponent)
        return EXIT_INVALID_INPUT

    seed_ai = Path(args.seed_ai).resolve() if args.seed_ai else None
    if seed_ai is not None and not seed_ai.is_file():
        _LOG.error("seed-ai not found: %s", seed_ai)
        return EXIT_INVALID_INPUT

    track_root: Path = args.out_dir.resolve()
    track_root.mkdir(parents=True, exist_ok=True)

    common_argv = forward_common_argv(args)
    budget = TokenBudget(max_tokens=max(0, args.max_tokens))

    lineages = []
    budget_exceeded = False
    exhausted_seeds: list[int] = []
    for seed in seeds:
        seed_dir = track_root / f"seed{seed}"
        try:
            budget.enforce(track_root, logger=_LOG)
        except BudgetExceeded as e:
            _LOG.error("%s", e)
            budget_exceeded = True
            break
        rc = _run_one_lineage(
            seed=seed, seed_dir=seed_dir, resume=args.resume,
            common_argv=common_argv, opponent=opponent,
            seed_ai=seed_ai, as_team=args.as_team,
            generations=args.generations,
        )
        if rc != EXIT_OK:
            # rc=30 is the documented exhausted-lineage signal from
            # evolve.main; other non-zero codes are unexpected but we
            # still continue with the remaining seeds so one bad
            # lineage cannot poison the whole track.
            exhausted_seeds.append(seed)
            _LOG.warning(
                "seed=%d: lineage ended with evolve rc=%d "
                "(continuing with remaining seeds)",
                seed, rc,
            )
        lineages.append(summarise_lineage(seed_dir, seed=seed, exit_code=rc))

    manifest_path = track_root / "track_a_manifest.json"
    write_manifest(
        manifest_path,
        track=TRACK,
        model=(args.model or ""),
        lineages=lineages,
        extra={
            "opponent": str(opponent),
            "as_team": args.as_team,
            "generations_requested": args.generations,
            "n_matches": args.n_matches,
            "aar_enabled": args.aar,
            "journal_enabled": args.journal,
            "max_tokens": budget.max_tokens,
            "tokens_total": budget.total(track_root),
            "budget_exceeded": budget_exceeded,
            # Soft-fault accounting: seeds whose lineages did not
            # finish cleanly (typically evolve rc=30).
            "exhausted_seeds": exhausted_seeds,
        },
    )
    _LOG.info("Track A manifest: %s (%d lineages)", manifest_path, len(lineages))
    return EXIT_BUDGET_EXCEEDED if budget_exceeded else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
