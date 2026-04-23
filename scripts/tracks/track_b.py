#!/usr/bin/env python3
"""Track B — monotonic self-play.

Each lineage produces a chain of champions. Generation ``N``'s
candidate is evaluated against generation ``N-1``'s champion; on
acceptance the new champion replaces the opponent for generation
``N+1``. After the chain completes, we run a final round-robin (via
``tournament.run_tournament``) across every per-generation champion to
assess monotonic improvement and dump ``tournament.json`` at the track
root.

Because each ``evolve.main`` invocation expects a *static* opponent,
Track B implements one-generation-at-a-time steps. Each step lives in
its own ``gen<NNNN>/`` subdirectory under the lineage root, seeded by
the previous generation's champion (``--seed-ai``) *and* using that
same champion as the opponent (``--opponent``).

Resume semantics: the outer loop walks ``gen<NNNN>/`` subdirs in
order. If ``state.json`` is present and complete (``history`` has the
requested number of generations, which is always 1 here) we treat the
step as done; otherwise we re-enter it via ``evolve --resume``.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

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
    read_state, read_champion_path, sha256_file, copy_snapshot,
    neutralised_copy,
    summarise_lineage, write_manifest, atomic_write_json,
    BudgetExceeded, TokenBudget,
    tournament,
)

TRACK = "B"
_LOG = logging.getLogger("swarmevolve.tracks.b")


def _parse_seed_list(raw: str) -> list[int]:
    if not raw:
        return []
    tokens = [t for t in raw.replace(",", " ").split() if t]
    try:
        return sorted({int(t) for t in tokens})
    except ValueError as e:
        raise SystemExit(f"--seeds: invalid integer in {raw!r}: {e}") from e


def build_parser():
    p = build_common_parser(
        prog="track_b",
        description="Track B: monotonic self-play (Gen N vs. Gen N-1).",
    )
    p.add_argument("--seeds", required=True,
                   help="Seeds to run (independent self-play chains)")
    p.add_argument("--as-team", choices=("A", "B"), default="A",
                   help="Which team slot each challenger plays")
    p.add_argument("--seed-ai", default=None,
                   help="Initial champion for generation 0 (defaults to pursuit_v1)")
    p.add_argument("--rr-n-matches", type=int, default=None,
                   help="Per-pair matches in the final round-robin "
                        "(defaults to --n-matches)")
    p.add_argument("--no-rr", action="store_true",
                   help="Skip the final round-robin tournament")
    return p


def _lineage_dir(track_root: Path, seed: int) -> Path:
    return track_root / f"seed{seed}"


def _step_dir(lineage_dir: Path, gen_idx: int) -> Path:
    return lineage_dir / f"gen{gen_idx:04d}"


def _step_complete(step_dir: Path) -> bool:
    """A step is complete iff its state.json has at least one history row."""
    st = read_state(step_dir)
    return bool(st and st.get("history"))


def _run_one_step(
    *, seed: int, step_dir: Path,
    seed_ai: Path, opponent: Path,
    as_team: str, common_argv: list[str], resume: bool,
) -> None:
    """Evolve exactly one generation into ``step_dir``."""
    step_state = step_dir / "state.json"
    if resume and step_state.is_file() and _step_complete(step_dir):
        _LOG.info("seed=%d step=%s: already complete, skipping",
                  seed, step_dir.name)
        return
    if resume and step_state.is_file():
        # Partial state: resume.
        invoke_evolve([
            "--resume", str(step_dir),
            "--generations", "1",
        ])
        return
    step_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        "--opponent", str(opponent),
        "--as-team", as_team,
        "--seed", str(seed),
        "--generations", "1",
        "--out-dir", str(step_dir),
        "--seed-ai", str(seed_ai),
    ]
    argv += common_argv
    _LOG.info("seed=%d step=%s: fresh (seed-ai=%s opponent=%s)",
              seed, step_dir.name, seed_ai.name, opponent.name)
    invoke_evolve(argv)


def _run_chain(
    *, seed: int, lineage_dir: Path, initial_seed_ai: Path,
    as_team: str, generations: int, common_argv: list[str],
    resume: bool, budget: TokenBudget, track_root: Path,
) -> tuple[list[Path], bool]:
    """Run ``generations`` sequential 1-generation steps. Returns the
    list of champion snapshots (one per completed step, may include
    the initial seed as ``gen_seed.cpp``) and a boolean flag that is
    True iff the budget tripped mid-chain.
    """
    lineage_dir.mkdir(parents=True, exist_ok=True)
    # Stash the seed-ai for reproducibility & round-robin inclusion.
    seed_snap = lineage_dir / "champions" / "gen_seed.cpp"
    if not seed_snap.is_file():
        copy_snapshot(initial_seed_ai, seed_snap)
    champion_snapshots: list[Path] = [seed_snap]

    current_champ = seed_snap
    for gen_idx in range(generations):
        try:
            budget.enforce(track_root, logger=_LOG)
        except BudgetExceeded as e:
            _LOG.error("%s", e)
            return champion_snapshots, True
        step_dir = _step_dir(lineage_dir, gen_idx)
        _run_one_step(
            seed=seed, step_dir=step_dir,
            seed_ai=current_champ, opponent=current_champ,
            as_team=as_team, common_argv=common_argv, resume=resume,
        )
        # The step produced either an accepted snapshot (champions/gen_0000.cpp
        # inside step_dir) or no snapshot (rejected). In either case the
        # current champion for the *next* step is whatever lives at
        # step_dir's best.cpp, or the previous champion.
        new_champ = read_champion_path(step_dir)
        if new_champ is None:
            _LOG.info("seed=%d gen=%d: no champion snapshot, keeping %s",
                      seed, gen_idx, current_champ.name)
        else:
            # Promote into lineage-level champion history.
            promoted = lineage_dir / "champions" / f"gen_{gen_idx:04d}.cpp"
            copy_snapshot(new_champ, promoted)
            champion_snapshots.append(promoted)
            current_champ = promoted
    return champion_snapshots, False


def _final_round_robin(
    *, lineage_dir: Path, champion_paths: list[Path],
    n_matches: int, workers: int | None,
) -> dict:
    """Run the final round-robin and write ``tournament.json`` in
    ``lineage_dir``. Returns a small summary dict for the manifest."""
    if len(champion_paths) < 2:
        return {"skipped": True, "reason": "fewer than 2 champions"}
    # Champion snapshots emitted by evolve have ``namespace TeamA``
    # hard-coded. The round-robin tournament must be able to compile
    # each source into *either* team slot, so we stage neutralised
    # copies (with the placeholder restored) into _rr_staging/.
    staging = lineage_dir / "_rr_staging"
    entries: list[tournament.AIEntry] = []
    for p in champion_paths:
        staged = neutralised_copy(p, staging / p.name)
        entries.append(
            tournament.AIEntry(
                name=p.stem,
                path=str(staged),
                sha256=sha256_file(staged),
            )
        )
    scratch = lineage_dir / "_rr_scratch"
    result = tournament.run_tournament(
        entries,
        mode="round_robin",
        n_matches=n_matches,
        seed_base=0,
        workers=workers,
        scratch_root=scratch,
    )
    # TournamentResult is a dataclass with to_json() / __dict__-ish layout;
    # call its serialiser if present, otherwise dump as best-effort.
    if hasattr(result, "to_json"):
        payload = result.to_json()
    elif hasattr(result, "__dict__"):
        import dataclasses
        payload = dataclasses.asdict(result)
    else:
        payload = {"repr": repr(result)}
    atomic_write_json(lineage_dir / "tournament.json", payload)
    # Clean up scratch: it may be huge. Tolerate failures.
    try:
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:  # pragma: no cover
        pass
    # Produce a short summary (entries + final ratings if available).
    ratings = getattr(result, "final_ratings", None) or payload.get("final_ratings")
    return {
        "entries": [e.name for e in entries],
        "final_ratings": ratings,
        "n_matches": n_matches,
    }


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

    initial_seed_ai = (
        Path(args.seed_ai).resolve() if args.seed_ai else PURSUIT_V1.resolve()
    )
    if not initial_seed_ai.is_file():
        _LOG.error("seed-ai not found: %s", initial_seed_ai)
        return EXIT_INVALID_INPUT

    track_root: Path = args.out_dir.resolve()
    track_root.mkdir(parents=True, exist_ok=True)

    common_argv = forward_common_argv(args)
    rr_n_matches = args.rr_n_matches or args.n_matches
    budget = TokenBudget(max_tokens=max(0, args.max_tokens))

    lineages = []
    rr_summaries: dict[str, dict] = {}
    budget_exceeded = False
    for seed in seeds:
        lineage_dir = _lineage_dir(track_root, seed)
        champions, tripped = _run_chain(
            seed=seed, lineage_dir=lineage_dir,
            initial_seed_ai=initial_seed_ai,
            as_team=args.as_team, generations=args.generations,
            common_argv=common_argv, resume=args.resume,
            budget=budget, track_root=track_root,
        )
        # Summarise the *last* step's state.json as the lineage summary
        # (that's the "current" champion and the largest cumulative token
        # count).
        last_step = _step_dir(lineage_dir, args.generations - 1)
        lineages.append(summarise_lineage(
            last_step if last_step.is_dir() else lineage_dir,
            seed=seed,
        ))
        if tripped:
            budget_exceeded = True
            break
        if not args.no_rr:
            rr_summaries[f"seed{seed}"] = _final_round_robin(
                lineage_dir=lineage_dir,
                champion_paths=champions,
                n_matches=rr_n_matches,
                workers=args.workers,
            )

    manifest_path = track_root / "track_b_manifest.json"
    write_manifest(
        manifest_path,
        track=TRACK,
        model=(args.model or ""),
        lineages=lineages,
        extra={
            "initial_seed_ai": str(initial_seed_ai),
            "as_team": args.as_team,
            "generations_requested": args.generations,
            "n_matches": args.n_matches,
            "rr_n_matches": rr_n_matches,
            "rr_enabled": not args.no_rr,
            "rr_summaries": rr_summaries,
            "aar_enabled": args.aar,
            "journal_enabled": args.journal,
            "max_tokens": budget.max_tokens,
            "tokens_total": budget.total(track_root),
            "budget_exceeded": budget_exceeded,
        },
    )
    _LOG.info("Track B manifest: %s (%d lineages)",
              manifest_path, len(lineages))
    return EXIT_BUDGET_EXCEEDED if budget_exceeded else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
