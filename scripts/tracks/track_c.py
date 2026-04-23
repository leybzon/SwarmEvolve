#!/usr/bin/env python3
"""Track C — A-vs-B co-evolution with periodic external yardstick.

Two parallel lineages share a seed. Each generation ``N``:

1. Snapshot both lineages' current champions.
2. Lineage ``A`` runs one ``evolve`` step with opponent = ``B``'s
   current champion. Lineage ``B`` runs one step with opponent =
   ``A``'s snapshot from step 1 (so both A-step and B-step see the
   *same* opponent snapshot at the start of the round).
3. If ``--yardstick-every`` divides ``N``, both updated champions
   play a fixed number of matches against ``pursuit_v1``; results
   append to ``<out-dir>/seed<N>/yardstick.jsonl``.

The layout is

    <out-dir>/seed<SEED>/
        A/gen<NNNN>/ ...  (one evolve run per step)
        B/gen<NNNN>/ ...
        champions/
            A/gen_seed.cpp, gen_0000.cpp, ...
            B/gen_seed.cpp, gen_0000.cpp, ...
        yardstick.jsonl

Resume: the outer loop walks ``gen<NNNN>/`` subdirs; any step whose
``state.json`` already has history is skipped. Yardstick entries are
deduplicated by generation index.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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

TRACK = "C"
_LOG = logging.getLogger("swarmevolve.tracks.c")

_LINEAGES = ("A", "B")


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
        prog="track_c",
        description="Track C: A-vs-B co-evolution with yardstick.",
    )
    p.add_argument("--seeds", required=True,
                   help="Seeds to run (independent co-evolution runs)")
    p.add_argument("--seed-ai-a", default=None,
                   help="Initial champion for lineage A (defaults to pursuit_v1)")
    p.add_argument("--seed-ai-b", default=None,
                   help="Initial champion for lineage B (defaults to pursuit_v1)")
    p.add_argument("--yardstick-every", type=int, default=5,
                   help="Play yardstick matches every K generations (0=off)")
    p.add_argument("--yardstick-n-matches", type=int, default=None,
                   help="Matches per yardstick pairing (defaults to --n-matches)")
    return p


def _coevo_dir(track_root: Path, seed: int) -> Path:
    return track_root / f"seed{seed}"


def _lineage_root(coevo_dir: Path, lineage: str) -> Path:
    return coevo_dir / lineage


def _step_dir(lineage_root: Path, gen_idx: int) -> Path:
    return lineage_root / f"gen{gen_idx:04d}"


def _champions_dir(coevo_dir: Path, lineage: str) -> Path:
    return coevo_dir / "champions" / lineage


def _step_complete(step_dir: Path) -> bool:
    st = read_state(step_dir)
    return bool(st and st.get("history"))


def _run_one_step(
    *, seed: int, lineage_label: str, step_dir: Path,
    seed_ai: Path, opponent: Path,
    as_team: str, common_argv: list[str], resume: bool,
) -> None:
    step_state = step_dir / "state.json"
    if resume and step_state.is_file() and _step_complete(step_dir):
        _LOG.info("seed=%d %s step=%s: already complete",
                  seed, lineage_label, step_dir.name)
        return
    if resume and step_state.is_file():
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
    _LOG.info("seed=%d %s step=%s: fresh (seed-ai=%s opponent=%s)",
              seed, lineage_label, step_dir.name,
              seed_ai.name, opponent.name)
    invoke_evolve(argv)


def _promote_champion(
    *, step_dir: Path, champions_dir: Path, gen_idx: int,
    fallback: Path,
) -> Path:
    """Copy the step's champion (or the fallback) into the coevo
    champions directory. Returns the promoted path."""
    snap = read_champion_path(step_dir)
    dst = champions_dir / f"gen_{gen_idx:04d}.cpp"
    if snap is None:
        copy_snapshot(fallback, dst)
    else:
        copy_snapshot(snap, dst)
    return dst


# ---------------------------------------------------------------------------
# Yardstick
# ---------------------------------------------------------------------------


def _run_yardstick(
    *, coevo_dir: Path, gen_idx: int,
    champion_paths: dict[str, Path],
    yardstick_opponent: Path, n_matches: int, workers: int | None,
) -> dict[str, Any]:
    """Play ``n_matches`` games between each champion and
    ``yardstick_opponent`` (in both team-slot orientations); append one
    row per lineage to ``yardstick.jsonl`` and return the row payload."""
    scratch = coevo_dir / "_yardstick_scratch"
    # Lineage champion snapshots have ``namespace TeamA`` baked in;
    # the tournament needs to compile each source into either team
    # slot, so we stage a neutralised copy alongside the scratch dir.
    staging = coevo_dir / "_yardstick_staging"
    entries: dict[str, tournament.AIEntry] = {}
    for lab, p in champion_paths.items():
        staged = neutralised_copy(
            p, staging / f"{lab}_gen{gen_idx:04d}.cpp"
        )
        entries[lab] = tournament.AIEntry(
            name=f"{lab}_gen{gen_idx:04d}",
            path=str(staged),
            sha256=sha256_file(staged),
        )
    # pursuit_v1 already uses TEAM_NS_PLACEHOLDER, so no staging needed
    # (neutralise_namespace() is a no-op in that case).
    yardstick = tournament.AIEntry(
        name="pursuit_v1",
        path=str(yardstick_opponent),
        sha256=sha256_file(yardstick_opponent),
    )
    rows: dict[str, Any] = {
        "generation": gen_idx,
        "results": {},
    }
    for lab, ai in entries.items():
        result = tournament.run_tournament(
            [ai, yardstick],
            mode="round_robin",
            n_matches=n_matches,
            seed_base=0,
            workers=workers,
            scratch_root=scratch,
        )
        payload = result.to_json() if hasattr(result, "to_json") else {}
        rows["results"][lab] = {
            "final_ratings": payload.get("final_ratings"),
            "n_matches": n_matches,
        }
    try:
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:  # pragma: no cover
        pass
    out = coevo_dir / "yardstick.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Deduplicate by generation: skip appending if the latest row already
    # has the same generation (useful on resume).
    existing = _read_yardstick_generations(out)
    if gen_idx in existing:
        _LOG.info("yardstick gen=%d already recorded; skipping append", gen_idx)
        return rows
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rows, sort_keys=True))
        fh.write("\n")
    return rows


def _read_yardstick_generations(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    gens: set[int] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            gens.add(int(json.loads(raw).get("generation", -1)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return gens


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _run_coevo(
    *, seed: int, coevo_dir: Path,
    seed_ai_a: Path, seed_ai_b: Path,
    generations: int, yardstick_every: int, yardstick_n_matches: int,
    common_argv: list[str], resume: bool, workers: int | None,
    budget: TokenBudget, track_root: Path,
) -> tuple[dict[str, Path], bool]:
    """Drive the two lineages in lock-step. Returns the final champion
    path for each lineage and a flag that is True iff the budget
    tripped during the run."""
    coevo_dir.mkdir(parents=True, exist_ok=True)

    # Seed snapshots.
    champion_a = copy_snapshot(
        seed_ai_a, _champions_dir(coevo_dir, "A") / "gen_seed.cpp"
    )
    champion_b = copy_snapshot(
        seed_ai_b, _champions_dir(coevo_dir, "B") / "gen_seed.cpp"
    )

    lineage_roots = {lab: _lineage_root(coevo_dir, lab) for lab in _LINEAGES}
    for r in lineage_roots.values():
        r.mkdir(parents=True, exist_ok=True)

    current = {"A": champion_a, "B": champion_b}

    for gen_idx in range(generations):
        try:
            budget.enforce(track_root, logger=_LOG)
        except BudgetExceeded as e:
            _LOG.error("%s", e)
            return current, True
        # Snapshot both champions at the start of the round so that
        # each lineage's step sees the *same* opponent throughout gen_idx.
        opp_snapshots = dict(current)

        # A-step: opponent = opp_snapshots["B"]
        step_a = _step_dir(lineage_roots["A"], gen_idx)
        _run_one_step(
            seed=seed, lineage_label="A", step_dir=step_a,
            seed_ai=current["A"], opponent=opp_snapshots["B"],
            as_team="A", common_argv=common_argv, resume=resume,
        )
        new_a = _promote_champion(
            step_dir=step_a,
            champions_dir=_champions_dir(coevo_dir, "A"),
            gen_idx=gen_idx, fallback=current["A"],
        )

        # B-step: opponent = opp_snapshots["A"] (original, not new_a)
        step_b = _step_dir(lineage_roots["B"], gen_idx)
        _run_one_step(
            seed=seed, lineage_label="B", step_dir=step_b,
            seed_ai=current["B"], opponent=opp_snapshots["A"],
            as_team="A",  # each lineage plays the A slot locally
            common_argv=common_argv, resume=resume,
        )
        new_b = _promote_champion(
            step_dir=step_b,
            champions_dir=_champions_dir(coevo_dir, "B"),
            gen_idx=gen_idx, fallback=current["B"],
        )

        current = {"A": new_a, "B": new_b}

        if yardstick_every > 0 and (gen_idx % yardstick_every == 0):
            _run_yardstick(
                coevo_dir=coevo_dir,
                gen_idx=gen_idx,
                champion_paths=current,
                yardstick_opponent=PURSUIT_V1.resolve(),
                n_matches=yardstick_n_matches,
                workers=workers,
            )

    return current, False


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

    seed_ai_a = (
        Path(args.seed_ai_a).resolve() if args.seed_ai_a else PURSUIT_V1.resolve()
    )
    seed_ai_b = (
        Path(args.seed_ai_b).resolve() if args.seed_ai_b else PURSUIT_V1.resolve()
    )
    for p in (seed_ai_a, seed_ai_b):
        if not p.is_file():
            _LOG.error("seed-ai not found: %s", p)
            return EXIT_INVALID_INPUT

    track_root: Path = args.out_dir.resolve()
    track_root.mkdir(parents=True, exist_ok=True)

    common_argv = forward_common_argv(args)
    ys_every = max(0, args.yardstick_every)
    ys_n = args.yardstick_n_matches or args.n_matches
    budget = TokenBudget(max_tokens=max(0, args.max_tokens))

    lineages: list = []
    budget_exceeded = False
    for seed in seeds:
        coevo_dir = _coevo_dir(track_root, seed)
        final, tripped = _run_coevo(
            seed=seed, coevo_dir=coevo_dir,
            seed_ai_a=seed_ai_a, seed_ai_b=seed_ai_b,
            generations=args.generations,
            yardstick_every=ys_every,
            yardstick_n_matches=ys_n,
            common_argv=common_argv, resume=args.resume,
            workers=args.workers,
            budget=budget, track_root=track_root,
        )
        # One manifest row per (seed, lineage).
        for lab in _LINEAGES:
            last_step = _step_dir(
                _lineage_root(coevo_dir, lab), args.generations - 1
            )
            row = summarise_lineage(
                last_step if last_step.is_dir() else coevo_dir,
                seed=seed,
            )
            # Encode lineage label in the run_dir for disambiguation.
            row.run_dir = str(_lineage_root(coevo_dir, lab).resolve())
            lineages.append(row)
        if tripped:
            budget_exceeded = True
            break

    manifest_path = track_root / "track_c_manifest.json"
    write_manifest(
        manifest_path,
        track=TRACK,
        model=(args.model or ""),
        lineages=lineages,
        extra={
            "seed_ai_a": str(seed_ai_a),
            "seed_ai_b": str(seed_ai_b),
            "generations_requested": args.generations,
            "n_matches": args.n_matches,
            "yardstick_every": ys_every,
            "yardstick_n_matches": ys_n,
            "aar_enabled": args.aar,
            "journal_enabled": args.journal,
            "max_tokens": budget.max_tokens,
            "tokens_total": budget.total(track_root),
            "budget_exceeded": budget_exceeded,
        },
    )
    _LOG.info("Track C manifest: %s (%d lineage rows)",
              manifest_path, len(lineages))
    return EXIT_BUDGET_EXCEEDED if budget_exceeded else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
