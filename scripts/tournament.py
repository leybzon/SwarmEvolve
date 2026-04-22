#!/usr/bin/env python3
"""Tournament runner (M12).

Pairs a set of AI sources against each other and produces Elo-like
ratings plus a raw win matrix. Two scheduling modes:

``round_robin``
    Every unordered pair plays ``n_matches`` games on each side (so a
    pair ``{A, B}`` produces ``2 * n_matches`` match outcomes: A vs B
    and B vs A). This averages out the Team-A / Team-B sign asymmetry
    that the engine's spawn layout introduces.

``swiss``
    ``rounds`` rounds of Swiss pairings (Monrad): within each round
    every participant is paired against the nearest-rated opponent
    they have not yet played this round; ties broken deterministically
    by AI name. Each pairing again runs ``n_matches`` games per side.

Both modes share the same inner per-pairing primitive: a call to
``fitness.evaluate_fitness`` with a deterministic seed derived from
``seed_base`` and the ordered AI pair.

Output artifacts under ``--out-dir`` (created if missing):

- ``tournament.json``  — canonical record (config, AI list, per-
  pairing results, per-round events, final ratings, win matrix).
- ``win_matrix.csv``   — square matrix of W/L/D aggregates per
  ordered pair.
- ``ratings.csv``      — final Elo ratings sorted desc.
- ``events.jsonl``     — append-only ExperimentLog.

Determinism
-----------
Given the same AI source hashes, the same ``--seed-base``,
``--n-matches``, ``--mode``, ``--rounds``, and the same compiler,
two runs produce byte-identical ``tournament.json`` (modulo the
non-deterministic ``wall_seconds`` and timestamps, which are
excluded from the canonical record). This is enforced by
``tests/test_tournament.py::test_round_robin_stable_across_reruns``.

Exit codes
----------
- ``0`` : tournament completed
- ``2`` : CLI usage error
- ``40``: compile failure in at least one pairing
- ``41``: no compiler found
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allow ``python3 scripts/tournament.py`` as well as ``from scripts import
# tournament``; the module side-imports ``fitness`` and ``experiment_log``
# from the same directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from experiment_log import ExperimentLog  # noqa: E402
from fitness import CompileError, evaluate_fitness  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_COMPILE_FAILED = 40
EXIT_NO_COMPILER = 41

DEFAULT_ELO_START = 1500.0
DEFAULT_ELO_K = 32.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIEntry:
    """One AI participating in the tournament."""

    name: str
    path: str
    sha256: str


@dataclass
class PairingResult:
    """Result of one ordered pairing (team_a vs team_b, both directions)."""

    team_a: str  # AI name playing the TeamA slot
    team_b: str
    n_matches: int
    wins_a: int  # a-slot wins (from evaluate_fitness.wins_a)
    wins_b: int
    draws: int
    invalid: int
    seed_base: int

    def outcomes(self) -> list[str]:
        """Return per-match outcome list for Elo updates (order-stable).

        We materialise wins_a A-side wins, then wins_b B-side wins, then
        draws, then invalids (treated as draws). This is deterministic
        and does not require re-reading per-match data.
        """
        return ["A"] * self.wins_a + ["B"] * self.wins_b + ["D"] * (self.draws + self.invalid)


@dataclass
class RoundRecord:
    """One tournament round (round-robin pass or Swiss round)."""

    index: int
    pairings: list[PairingResult] = field(default_factory=list)
    ratings_snapshot: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Elo helpers
# ---------------------------------------------------------------------------


def _expected_score(ra: float, rb: float) -> float:
    """Classical Elo expected-score formula."""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def _elo_update(
    ratings: dict[str, float],
    name_a: str,
    name_b: str,
    score_a: float,
    *,
    k: float,
) -> None:
    """Apply one in-place Elo update for a single (a, b, score_a) match.

    ``score_a`` is 1.0 / 0.5 / 0.0. Higher-rated winner gains fewer
    points than lower-rated winner (property-tested).
    """
    ra = ratings[name_a]
    rb = ratings[name_b]
    ea = _expected_score(ra, rb)
    ratings[name_a] = ra + k * (score_a - ea)
    ratings[name_b] = rb + k * ((1.0 - score_a) - (1.0 - ea))


def _apply_pairing_to_elo(ratings: dict[str, float], pairing: PairingResult, *, k: float) -> None:
    """Apply every match outcome in ``pairing`` to ``ratings`` in order."""
    for outcome in pairing.outcomes():
        if outcome == "A":
            _elo_update(ratings, pairing.team_a, pairing.team_b, 1.0, k=k)
        elif outcome == "B":
            _elo_update(ratings, pairing.team_a, pairing.team_b, 0.0, k=k)
        else:  # "D"
            _elo_update(ratings, pairing.team_a, pairing.team_b, 0.5, k=k)


# ---------------------------------------------------------------------------
# Seed derivation (deterministic from ordered pair)
# ---------------------------------------------------------------------------


def _pair_seed(seed_base: int, team_a: str, team_b: str) -> int:
    """Derive a deterministic seed base for one ordered pairing.

    Use SHA-256 of "seed_base|team_a|team_b" truncated to 32 bits. This
    ensures two runs with the same config produce identical per-pair
    seeds even if dict-iteration order were to vary across Python
    versions.
    """
    key = f"{seed_base}|{team_a}|{team_b}".encode()
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:4], "big")


# ---------------------------------------------------------------------------
# Pairing execution
# ---------------------------------------------------------------------------


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _derive_name(path: Path) -> str:
    """Use the file stem as the AI name; callers can override with --names."""
    return path.stem


def _play_pairing(
    a: AIEntry,
    b: AIEntry,
    *,
    n_matches: int,
    seed_base: int,
    max_ticks: int,
    timeout: float,
    compiler: str | None,
    scratch_root: Path | None,
    workers: int | None,
) -> PairingResult:
    """Run ``n_matches`` games of ``a`` (as TeamA) vs ``b`` (as TeamB)."""
    result = evaluate_fitness(
        a.path,
        b.path,
        n_matches=n_matches,
        seed_base=_pair_seed(seed_base, a.name, b.name),
        workers=workers,
        max_ticks=max_ticks,
        timeout=timeout,
        compiler=compiler,
        scratch_root=scratch_root,
    )
    return PairingResult(
        team_a=a.name,
        team_b=b.name,
        n_matches=n_matches,
        wins_a=result.wins_a,
        wins_b=result.wins_b,
        draws=result.draws,
        invalid=result.invalid,
        seed_base=_pair_seed(seed_base, a.name, b.name),
    )


# ---------------------------------------------------------------------------
# Schedulers
# ---------------------------------------------------------------------------


def _schedule_round_robin(entries: list[AIEntry]) -> list[list[tuple[AIEntry, AIEntry]]]:
    """One round = one pass through every ordered pair (i != j).

    Ordered (not unordered) pairs matter because the engine is
    asymmetric in TeamA/TeamB spawn layout. Playing both directions
    in the same round averages the asymmetry out of the final rating.
    Rounds are identity in round-robin — we produce exactly one round.
    """
    pairs: list[tuple[AIEntry, AIEntry]] = []
    for a, b in itertools.combinations(entries, 2):
        pairs.append((a, b))
        pairs.append((b, a))
    return [pairs]


def _swiss_next_round(
    entries: list[AIEntry],
    ratings: dict[str, float],
    played: set[tuple[str, str]],
) -> list[tuple[AIEntry, AIEntry]]:
    """Monrad pairing: sort by rating desc (tiebreak: name asc), then pair
    consecutive unpaired players. If the naive pairing collides with an
    already-played ordered pair, swap with the next candidate.

    Each returned pairing is one ordered (a, b); we also append the
    reverse (b, a) so both TeamA/TeamB slots are exercised, matching
    the round-robin convention.
    """
    order = sorted(entries, key=lambda e: (-ratings[e.name], e.name))
    remaining = list(order)
    pairs: list[tuple[AIEntry, AIEntry]] = []
    while len(remaining) >= 2:
        a = remaining.pop(0)
        # Find first opponent not already played this ordered direction.
        idx = 0
        while idx < len(remaining):
            b = remaining[idx]
            if (a.name, b.name) not in played and (b.name, a.name) not in played:
                break
            idx += 1
        else:
            # All remaining have been played; take the head anyway
            # (inevitable if rounds > round-robin capacity).
            idx = 0
        b = remaining.pop(idx)
        pairs.append((a, b))
        pairs.append((b, a))
    return pairs


# ---------------------------------------------------------------------------
# Tournament entry point (public API)
# ---------------------------------------------------------------------------


@dataclass
class TournamentResult:
    mode: str
    n_matches: int
    seed_base: int
    rounds: list[RoundRecord]
    final_ratings: dict[str, float]
    entries: list[AIEntry]
    win_matrix: dict[str, dict[str, dict[str, int]]]
    elo_k: float
    elo_start: float
    compiler: str
    wall_seconds: float

    def to_dict(self, *, include_volatile: bool = True) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        ``include_volatile=False`` drops ``wall_seconds`` and other
        wall-clock/hardware fields, producing a *canonical* record
        suitable for byte-equality comparisons across reruns.
        """
        d: dict[str, Any] = {
            "mode": self.mode,
            "n_matches": self.n_matches,
            "seed_base": self.seed_base,
            "elo_k": self.elo_k,
            "elo_start": self.elo_start,
            "entries": [{"name": e.name, "path": e.path, "sha256": e.sha256} for e in self.entries],
            "rounds": [
                {
                    "index": r.index,
                    "pairings": [
                        {
                            "team_a": p.team_a,
                            "team_b": p.team_b,
                            "n_matches": p.n_matches,
                            "wins_a": p.wins_a,
                            "wins_b": p.wins_b,
                            "draws": p.draws,
                            "invalid": p.invalid,
                            "seed_base": p.seed_base,
                        }
                        for p in r.pairings
                    ],
                    "ratings_snapshot": dict(sorted(r.ratings_snapshot.items())),
                }
                for r in self.rounds
            ],
            "final_ratings": dict(sorted(self.final_ratings.items())),
            "win_matrix": self.win_matrix,
        }
        if include_volatile:
            d["compiler"] = self.compiler
            d["wall_seconds"] = self.wall_seconds
        return d


def run_tournament(
    entries: list[AIEntry],
    *,
    mode: str = "round_robin",
    n_matches: int = 10,
    rounds: int = 1,
    seed_base: int = 0,
    elo_start: float = DEFAULT_ELO_START,
    elo_k: float = DEFAULT_ELO_K,
    max_ticks: int = 1000,
    timeout: float = 10.0,
    compiler: str | None = None,
    scratch_root: Path | None = None,
    workers: int | None = None,
    log: ExperimentLog | None = None,
) -> TournamentResult:
    """Run the tournament and return the full result record.

    All scheduling + rating logic is in-process; the only side-effects
    are inside ``evaluate_fitness`` (compile + match runs) and — if
    ``log`` is given — per-round ``ExperimentLog`` writes.
    """
    if mode not in {"round_robin", "swiss"}:
        raise ValueError(f"unknown mode: {mode!r}")
    if len(entries) < 2:
        raise ValueError("tournament requires >= 2 AIs")
    if n_matches < 1:
        raise ValueError("n_matches must be >= 1")

    ratings: dict[str, float] = {e.name: float(elo_start) for e in entries}
    played: set[tuple[str, str]] = set()
    round_records: list[RoundRecord] = []
    win_matrix: dict[str, dict[str, dict[str, int]]] = {
        e.name: {o.name: {"wins_a": 0, "wins_b": 0, "draws": 0, "invalid": 0} for o in entries}
        for e in entries
    }

    t0 = time.perf_counter()

    # Pre-compute schedule. Round-robin has a single round; Swiss grows
    # round-by-round based on current ratings (empty list → populated
    # per-round inside the loop).
    schedule = _schedule_round_robin(entries) if mode == "round_robin" else []

    round_idx = 0
    while True:
        if mode == "round_robin":
            if round_idx >= len(schedule):
                break
            pairs = schedule[round_idx]
        else:
            if round_idx >= rounds:
                break
            pairs = _swiss_next_round(entries, ratings, played)
            if not pairs:
                break

        record = RoundRecord(index=round_idx)
        if log is not None:
            log.write(
                "round_start",
                round=round_idx,
                mode=mode,
                pair_count=len(pairs),
                ratings_snapshot=dict(sorted(ratings.items())),
            )

        for a, b in pairs:
            pairing = _play_pairing(
                a,
                b,
                n_matches=n_matches,
                seed_base=seed_base,
                max_ticks=max_ticks,
                timeout=timeout,
                compiler=compiler,
                scratch_root=scratch_root,
                workers=workers,
            )
            record.pairings.append(pairing)
            played.add((a.name, b.name))

            # Update Elo with per-match granularity.
            _apply_pairing_to_elo(ratings, pairing, k=elo_k)

            # Update win matrix (stored in [team_a][team_b] keyed form).
            cell = win_matrix[a.name][b.name]
            cell["wins_a"] += pairing.wins_a
            cell["wins_b"] += pairing.wins_b
            cell["draws"] += pairing.draws
            cell["invalid"] += pairing.invalid

            if log is not None:
                log.write(
                    "pairing_result",
                    round=round_idx,
                    team_a=a.name,
                    team_b=b.name,
                    wins_a=pairing.wins_a,
                    wins_b=pairing.wins_b,
                    draws=pairing.draws,
                    invalid=pairing.invalid,
                    seed_base=pairing.seed_base,
                )

        record.ratings_snapshot = dict(sorted(ratings.items()))
        round_records.append(record)

        if log is not None:
            log.write(
                "round_end",
                round=round_idx,
                ratings_snapshot=record.ratings_snapshot,
            )

        round_idx += 1

    wall = time.perf_counter() - t0
    return TournamentResult(
        mode=mode,
        n_matches=n_matches,
        seed_base=seed_base,
        rounds=round_records,
        final_ratings=dict(sorted(ratings.items())),
        entries=entries,
        win_matrix=win_matrix,
        elo_k=elo_k,
        elo_start=elo_start,
        compiler=compiler or "",
        wall_seconds=wall,
    )


# ---------------------------------------------------------------------------
# Artefact writers
# ---------------------------------------------------------------------------


def write_artifacts(result: TournamentResult, out_dir: Path) -> None:
    """Write ``tournament.json``, ``win_matrix.csv``, ``ratings.csv``."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # tournament.json — includes volatile fields for human consumption,
    # but the canonical record is a separate tournament.canonical.json.
    (out_dir / "tournament.json").write_text(
        json.dumps(result.to_dict(include_volatile=True), indent=2, sort_keys=False) + "\n"
    )
    (out_dir / "tournament.canonical.json").write_text(
        json.dumps(result.to_dict(include_volatile=False), indent=2, sort_keys=True) + "\n"
    )

    # win_matrix.csv — square AxA with W/L/D split per ordered cell.
    names = [e.name for e in result.entries]
    with (out_dir / "win_matrix.csv").open("w", newline="") as f:
        w = csv.writer(f)
        header = ["team_a\\team_b"]
        for n in names:
            header += [f"{n}_wins_a", f"{n}_wins_b", f"{n}_draws", f"{n}_invalid"]
        w.writerow(header)
        for a in names:
            row: list[Any] = [a]
            for b in names:
                cell = result.win_matrix[a][b]
                row += [cell["wins_a"], cell["wins_b"], cell["draws"], cell["invalid"]]
            w.writerow(row)

    # ratings.csv — sorted desc by rating.
    with (out_dir / "ratings.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "rating"])
        ranked = sorted(result.final_ratings.items(), key=lambda kv: (-kv[1], kv[0]))
        for i, (name, rating) in enumerate(ranked, start=1):
            w.writerow([i, name, f"{rating:.4f}"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run a tournament among AI sources and emit Elo ratings + win matrix."
    )
    p.add_argument(
        "--ai",
        action="append",
        required=True,
        metavar="PATH",
        help="Path to an AI source file. Repeatable (>= 2 times).",
    )
    p.add_argument(
        "--name",
        action="append",
        default=None,
        metavar="NAME",
        help="Optional explicit name for each --ai (same order). Default: file stem.",
    )
    p.add_argument("--mode", choices=("round_robin", "swiss"), default="round_robin")
    p.add_argument("--n-matches", type=int, default=10, help="Matches per ordered pairing")
    p.add_argument("--rounds", type=int, default=1, help="Swiss rounds (ignored for round_robin)")
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--elo-start", type=float, default=DEFAULT_ELO_START)
    p.add_argument("--elo-k", type=float, default=DEFAULT_ELO_K)
    p.add_argument("--max-ticks", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--compiler", default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--out-dir", type=Path, required=True)
    return p


def _resolve_entries(ai_paths: list[str], names: list[str] | None) -> list[AIEntry]:
    paths = [Path(p).resolve() for p in ai_paths]
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"AI source not found: {p}")
    if names is not None:
        if len(names) != len(paths):
            raise ValueError(f"--name count ({len(names)}) must match --ai count ({len(paths)})")
        resolved_names = list(names)
    else:
        resolved_names = [_derive_name(p) for p in paths]
    # Name uniqueness.
    if len(set(resolved_names)) != len(resolved_names):
        raise ValueError(f"AI names must be unique; got {resolved_names!r}")
    return [
        AIEntry(name=n, path=str(p), sha256=_sha256_path(p))
        for n, p in zip(resolved_names, paths, strict=True)
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if len(args.ai) < 2:
        print("tournament.py: need >= 2 --ai entries", file=sys.stderr)
        return EXIT_USAGE

    try:
        entries = _resolve_entries(args.ai, args.name)
    except (FileNotFoundError, ValueError) as exc:
        print(f"tournament.py: {exc}", file=sys.stderr)
        return EXIT_USAGE

    args.out_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "mode": args.mode,
        "n_matches": args.n_matches,
        "rounds": args.rounds,
        "seed_base": args.seed_base,
        "elo_start": args.elo_start,
        "elo_k": args.elo_k,
        "max_ticks": args.max_ticks,
        "workers": args.workers,
        "entries": [{"name": e.name, "path": e.path, "sha256": e.sha256} for e in entries],
    }

    with ExperimentLog(args.out_dir) as log:
        log.write_start(experiment_type="tournament", config=config)
        try:
            result = run_tournament(
                entries,
                mode=args.mode,
                n_matches=args.n_matches,
                rounds=args.rounds,
                seed_base=args.seed_base,
                elo_start=args.elo_start,
                elo_k=args.elo_k,
                max_ticks=args.max_ticks,
                timeout=args.timeout,
                compiler=args.compiler,
                scratch_root=args.out_dir / "scratch",
                workers=args.workers,
                log=log,
            )
        except CompileError as exc:
            log.write("error", kind="compile_failed", msg=str(exc)[:4000])
            print(f"tournament.py: compile failed: {exc}", file=sys.stderr)
            return EXIT_COMPILE_FAILED
        except RuntimeError as exc:
            # fitness.evaluate_fitness raises RuntimeError if no compiler.
            if "compiler" in str(exc).lower():
                log.write("error", kind="no_compiler", msg=str(exc))
                print(f"tournament.py: {exc}", file=sys.stderr)
                return EXIT_NO_COMPILER
            raise

        write_artifacts(result, args.out_dir)
        log.write(
            "tournament_end",
            mode=result.mode,
            rounds=len(result.rounds),
            pair_count=sum(len(r.pairings) for r in result.rounds),
            final_ratings=result.final_ratings,
            wall_seconds=result.wall_seconds,
        )

    # Console summary.
    ranked = sorted(result.final_ratings.items(), key=lambda kv: (-kv[1], kv[0]))
    print(
        f"tournament: mode={result.mode} rounds={len(result.rounds)} pairs_total="
        f"{sum(len(r.pairings) for r in result.rounds)} "
        f"wall={result.wall_seconds:.2f}s"
    )
    for i, (name, rating) in enumerate(ranked, start=1):
        print(f"  #{i:>2} {name:<20} {rating:>8.2f}")
    print(f"artifacts: {args.out_dir}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
