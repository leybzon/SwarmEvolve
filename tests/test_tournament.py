"""Tests for ``scripts.tournament`` — M12 exit criterion plus property tests.

The exit criterion (IMPLEMENTATION_PLAN §14) is:

    Tournament with 4 known AIs produces stable rankings across 3 reruns.

We satisfy it with ``test_round_robin_4ai_stable_across_3_reruns``. The
remaining tests are property-style guards against plausible future
regressions:

* ``test_win_matrix_conservation``    — accounting invariant.
* ``test_elo_update_monotone``        — higher-rated winner gains less.
* ``test_swiss_top1_matches_rr_top1`` — dominance regime sanity.
* ``test_events_log_schema``          — ExperimentLog integration.
* ``test_canonical_json_stable_across_reruns`` — byte-stable record.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import tournament as t  # noqa: E402
from experiment_log import ExperimentLog  # noqa: E402

from tests._build_helper import CXX  # noqa: E402

pytestmark = pytest.mark.skipif(
    CXX is None, reason="no C++ compiler on this host; tournament tests need a compiler"
)

BASELINES = REPO_ROOT / "src" / "baselines"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

AI_POOL = {
    "pursuit": BASELINES / "pursuit_v1.cpp",
    "cluster": BASELINES / "cluster_v1.cpp",
    "stationary": BASELINES / "stationary_v1.cpp",
    "drift": FIXTURES / "drift_ai.cpp",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entries(names: list[str]) -> list[t.AIEntry]:
    """Construct ``AIEntry`` objects for a subset of the pool."""
    paths = [AI_POOL[n] for n in names]
    return [
        t.AIEntry(name=n, path=str(p), sha256=t._sha256_path(p))
        for n, p in zip(names, paths, strict=True)
    ]


def _run_small_tournament(
    tmp_path: Path,
    names: list[str],
    *,
    mode: str = "round_robin",
    n_matches: int = 3,
    rounds: int = 1,
    seed_base: int = 0,
    workers: int = 1,
) -> t.TournamentResult:
    """Run a small tournament; reusable test-helper.

    ``n_matches=3`` is enough to exercise all Elo-update paths while
    keeping the test suite fast (~15 s for a 4-AI round-robin on
    laptop hardware).
    """
    scratch = tmp_path / "scratch"
    return t.run_tournament(
        _entries(names),
        mode=mode,
        n_matches=n_matches,
        rounds=rounds,
        seed_base=seed_base,
        scratch_root=scratch,
        workers=workers,
    )


# ---------------------------------------------------------------------------
# Fast unit tests (no matches run) — Elo + scheduling
# ---------------------------------------------------------------------------


def test_elo_update_monotone_higher_rating_gains_less():
    """A 1700 Elo player beating a 1500 Elo player gains fewer points
    than a 1500 Elo player beating a 1700 Elo player. Pure-math check
    of the Elo update — no matches run."""
    base = {"hi": 1700.0, "lo": 1500.0}

    winner_hi = dict(base)
    t._elo_update(winner_hi, "hi", "lo", 1.0, k=32.0)
    gain_when_favourite_wins = winner_hi["hi"] - base["hi"]

    winner_lo = dict(base)
    t._elo_update(winner_lo, "lo", "hi", 1.0, k=32.0)
    gain_when_underdog_wins = winner_lo["lo"] - base["lo"]

    assert 0 < gain_when_favourite_wins < gain_when_underdog_wins
    # Zero-sum update.
    assert abs((winner_hi["hi"] - base["hi"]) + (winner_hi["lo"] - base["lo"])) < 1e-9


def test_elo_update_draw_shifts_rating_towards_mean():
    """A draw between 1700 and 1500 shifts the higher player down."""
    r = {"hi": 1700.0, "lo": 1500.0}
    t._elo_update(r, "hi", "lo", 0.5, k=32.0)
    assert r["hi"] < 1700.0
    assert r["lo"] > 1500.0
    # Zero-sum.
    assert abs((r["hi"] + r["lo"]) - (1700.0 + 1500.0)) < 1e-9


def test_pair_seed_is_deterministic_and_distinct():
    """Seed derivation must be stable across runs and distinct per pair."""
    s_ab = t._pair_seed(42, "pursuit", "cluster")
    s_ab2 = t._pair_seed(42, "pursuit", "cluster")
    s_ba = t._pair_seed(42, "cluster", "pursuit")
    s_other = t._pair_seed(43, "pursuit", "cluster")
    assert s_ab == s_ab2
    assert s_ab != s_ba
    assert s_ab != s_other
    # 32-bit unsigned range.
    assert 0 <= s_ab < 2**32


def test_round_robin_schedule_covers_every_ordered_pair():
    entries = _entries(["pursuit", "cluster", "stationary"])
    sched = t._schedule_round_robin(entries)
    assert len(sched) == 1  # one round
    pairs = sched[0]
    # n*(n-1) ordered pairs = 6.
    assert len(pairs) == 6
    names = [(a.name, b.name) for a, b in pairs]
    assert set(names) == {
        ("pursuit", "cluster"),
        ("cluster", "pursuit"),
        ("pursuit", "stationary"),
        ("stationary", "pursuit"),
        ("cluster", "stationary"),
        ("stationary", "cluster"),
    }


def test_pairing_outcomes_list_matches_counts():
    """PairingResult.outcomes() encodes wins_a + wins_b + draws + invalid."""
    p = t.PairingResult(
        team_a="a",
        team_b="b",
        n_matches=10,
        wins_a=5,
        wins_b=3,
        draws=1,
        invalid=1,
        seed_base=0,
    )
    outs = p.outcomes()
    assert len(outs) == 10
    assert outs.count("A") == 5
    assert outs.count("B") == 3
    assert outs.count("D") == 2  # draws + invalid


# ---------------------------------------------------------------------------
# Integration tests (run real matches) — the exit criterion + schema
# ---------------------------------------------------------------------------


def test_round_robin_4ai_stable_across_3_reruns(tmp_path):
    """M12 exit criterion: 4-AI round-robin produces stable rankings.

    We run the same 4-AI round-robin three times with the same
    seed_base; the final name→rank mapping must be byte-identical
    across all three runs, and the rating dict must match to the last
    float (the rating computation is purely deterministic given
    identical match outcomes).
    """
    names = ["pursuit", "cluster", "stationary", "drift"]
    results = []
    for run in range(3):
        scratch = tmp_path / f"run{run}"
        scratch.mkdir()
        res = _run_small_tournament(scratch, names, n_matches=3)
        results.append(res)

    # Rating dict exactly equal across reruns.
    r0 = results[0].final_ratings
    for i, res in enumerate(results[1:], start=1):
        assert res.final_ratings == r0, (
            f"run {i} diverged from run 0:\n  run 0: {r0}\n  run {i}: {res.final_ratings}"
        )

    # Rank order equal across reruns (redundant given exact equality,
    # but cheap and guards against future float-formatting changes).
    def rank_order(r: dict[str, float]) -> list[str]:
        return [n for n, _ in sorted(r.items(), key=lambda kv: (-kv[1], kv[0]))]

    ranks = [rank_order(r.final_ratings) for r in results]
    assert ranks[0] == ranks[1] == ranks[2], f"rank order drifted: {ranks}"

    # Sanity: pursuit — the strongest of the four — should finish in
    # the top 2 (it can't always be first because cluster is also
    # strong and the sample is small).
    top_two = set(ranks[0][:2])
    assert "pursuit" in top_two, f"pursuit dropped out of top-2: {ranks[0]}"


def test_win_matrix_conservation(tmp_path):
    """For every ordered cell, wins_a + wins_b + draws + invalid == n_matches."""
    res = _run_small_tournament(tmp_path, ["pursuit", "stationary"], n_matches=3)
    for a in res.win_matrix:
        for b in res.win_matrix[a]:
            cell = res.win_matrix[a][b]
            total = cell["wins_a"] + cell["wins_b"] + cell["draws"] + cell["invalid"]
            if a == b:
                assert total == 0, f"self-pair {a}=={b} should not play: {cell}"
            else:
                assert total == res.n_matches, (
                    f"cell {a}->{b}: {cell} totals {total}, expected {res.n_matches}"
                )


def test_swiss_top1_matches_rr_top1_for_dominant_player(tmp_path):
    """pursuit vs 2x stationary — pursuit dominates.

    Swiss top-1 and round-robin top-1 must agree in the dominance
    regime (it is a property of any sane rating system).
    """
    # Two stationaries as different named participants by giving them
    # different AI names but the same source — since we dedupe names,
    # we use stationary + drift (both weak) as the non-pursuit pool.
    names = ["pursuit", "stationary", "drift"]

    rr = _run_small_tournament(tmp_path / "rr", names, mode="round_robin", n_matches=3)

    swiss = _run_small_tournament(tmp_path / "sw", names, mode="swiss", n_matches=3, rounds=2)

    rr_top = max(rr.final_ratings.items(), key=lambda kv: kv[1])[0]
    sw_top = max(swiss.final_ratings.items(), key=lambda kv: kv[1])[0]
    assert rr_top == "pursuit", f"RR top was {rr_top}, expected pursuit"
    assert sw_top == "pursuit", f"Swiss top was {sw_top}, expected pursuit"


def test_events_log_has_start_and_end(tmp_path):
    """ExperimentLog schema: experiment_start → round_* / pairing_result →
    tournament_end → experiment_end (the last one is emitted by the
    ExperimentLog context manager on close).
    """
    out = tmp_path / "run"
    out.mkdir()
    names = ["pursuit", "cluster"]
    with ExperimentLog(out) as log:
        log.write_start(experiment_type="tournament", config={"test": True})
        t.run_tournament(
            _entries(names),
            mode="round_robin",
            n_matches=2,
            seed_base=0,
            scratch_root=out / "scratch",
            workers=1,
            log=log,
        )
        log.write("tournament_end", final_ratings={})

    events = ExperimentLog.read(out)
    types = [e["type"] for e in events]
    assert types[0] == "experiment_start"
    assert types[-1] == "experiment_end"  # written by ExperimentLog.__exit__
    assert types[-2] == "tournament_end"
    assert types.count("pairing_result") == 2  # 2 ordered pairs
    assert "round_start" in types
    assert "round_end" in types


def test_canonical_json_is_byte_stable_across_reruns(tmp_path):
    """tournament.canonical.json (volatile fields dropped) matches byte-for-byte."""
    names = ["pursuit", "cluster"]
    canonical_bytes: list[bytes] = []
    for run in range(2):
        out = tmp_path / f"run{run}"
        out.mkdir()
        res = _run_small_tournament(out, names, n_matches=3)
        t.write_artifacts(res, out)
        canonical_bytes.append((out / "tournament.canonical.json").read_bytes())

    assert canonical_bytes[0] == canonical_bytes[1], (
        "tournament.canonical.json drifted across runs — a volatile "
        "field is leaking into the canonical record"
    )


def test_cli_writes_ratings_csv_and_matrix(tmp_path):
    """End-to-end CLI smoke test."""
    out = tmp_path / "cli_out"
    argv = [
        "--ai",
        str(BASELINES / "pursuit_v1.cpp"),
        "--ai",
        str(BASELINES / "stationary_v1.cpp"),
        "--n-matches",
        "2",
        "--seed-base",
        "7",
        "--out-dir",
        str(out),
        "--workers",
        "1",
    ]
    rc = t.main(argv)
    assert rc == 0
    assert (out / "tournament.json").is_file()
    assert (out / "tournament.canonical.json").is_file()
    assert (out / "ratings.csv").is_file()
    assert (out / "win_matrix.csv").is_file()
    assert (out / "events.jsonl").is_file()

    # ratings.csv has 1 header + 2 AI rows.
    ratings_rows = (out / "ratings.csv").read_text().strip().splitlines()
    assert len(ratings_rows) == 3
    assert ratings_rows[0].startswith("rank,name,rating")


def test_cli_rejects_duplicate_names(tmp_path):
    out = tmp_path / "bad"
    argv = [
        "--ai",
        str(BASELINES / "pursuit_v1.cpp"),
        "--ai",
        str(BASELINES / "pursuit_v1.cpp"),  # duplicate stem
        "--n-matches",
        "1",
        "--out-dir",
        str(out),
    ]
    rc = t.main(argv)
    assert rc == t.EXIT_USAGE


def test_cli_rejects_missing_ai(tmp_path):
    out = tmp_path / "missing"
    argv = [
        "--ai",
        str(BASELINES / "pursuit_v1.cpp"),
        "--ai",
        str(tmp_path / "does_not_exist.cpp"),
        "--n-matches",
        "1",
        "--out-dir",
        str(out),
    ]
    rc = t.main(argv)
    assert rc == t.EXIT_USAGE


def test_cli_rejects_lt_two_ai(tmp_path):
    out = tmp_path / "single"
    argv = ["--ai", str(BASELINES / "pursuit_v1.cpp"), "--out-dir", str(out)]
    rc = t.main(argv)
    assert rc == t.EXIT_USAGE
