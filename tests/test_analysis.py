"""Tests for ``scripts.analysis`` (M12).

We avoid the expensive real-match path by synthesising a small
tournament.json dict in-memory and calling the analysis functions
directly. matplotlib is optional; tests gate on its presence.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import analysis as a

try:
    import matplotlib  # noqa: F401

    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _synthetic_tournament(names: list[str]) -> dict[str, object]:
    """Build a 1-round round-robin tournament record by hand.

    The concrete numbers: the first AI dominates, the last AI loses
    every pairing. This gives a non-trivial behaviour-distance matrix
    and a non-degenerate rating trajectory.
    """
    entries = [{"name": n, "path": f"/tmp/{n}.cpp", "sha256": "0" * 64} for n in names]
    n_match = 4
    win_matrix: dict[str, dict[str, dict[str, int]]] = {
        a_: {b_: {"wins_a": 0, "wins_b": 0, "draws": 0, "invalid": 0} for b_ in names}
        for a_ in names
    }
    pairings: list[dict[str, object]] = []
    elo = {n: 1500.0 for n in names}
    for i, a_ in enumerate(names):
        for j, b_ in enumerate(names):
            if i == j:
                continue
            wins_a = n_match if i < j else 0
            wins_b = 0 if i < j else n_match
            win_matrix[a_][b_] = {
                "wins_a": wins_a,
                "wins_b": wins_b,
                "draws": 0,
                "invalid": 0,
            }
            pairings.append(
                {
                    "team_a": a_,
                    "team_b": b_,
                    "n_matches": n_match,
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "draws": 0,
                    "invalid": 0,
                    "seed_base": 42 + 100 * i + j,
                }
            )
            # Quick Elo update (K=32) for the snapshot.
            score = 1.0 if wins_a > wins_b else 0.0
            ea = 1.0 / (1.0 + 10.0 ** ((elo[b_] - elo[a_]) / 400.0))
            for _ in range(n_match):
                elo[a_] += 32.0 * (score - ea)
                elo[b_] += 32.0 * ((1.0 - score) - (1.0 - ea))
    return {
        "mode": "round_robin",
        "n_matches": n_match,
        "seed_base": 42,
        "elo_k": 32.0,
        "elo_start": 1500.0,
        "entries": entries,
        "rounds": [
            {
                "index": 0,
                "pairings": pairings,
                "ratings_snapshot": dict(sorted(elo.items())),
            }
        ],
        "final_ratings": dict(sorted(elo.items())),
        "win_matrix": win_matrix,
    }


def test_load_tournament_accepts_dir_and_file(tmp_path):
    data = _synthetic_tournament(["a", "b"])
    (tmp_path / "tournament.json").write_text(json.dumps(data))
    assert a.load_tournament(tmp_path)["mode"] == "round_robin"
    assert a.load_tournament(tmp_path / "tournament.json")["mode"] == "round_robin"


def test_behaviour_distance_is_symmetric_and_zero_diagonal():
    data = _synthetic_tournament(["alpha", "beta", "gamma"])
    names, dist = a.behaviour_distance(data)
    n = len(names)
    for i in range(n):
        assert dist[i][i] == 0.0
    for i in range(n):
        for j in range(i + 1, n):
            assert dist[i][j] == dist[j][i]
            assert 0.0 <= dist[i][j] <= 1.0


def test_top_matches_sorts_by_surprise_desc_with_name_tiebreak():
    data = _synthetic_tournament(["alpha", "beta", "gamma"])
    picks = a.top_matches(data, k=6)
    # Non-increasing by surprise.
    for p1, p2 in itertools.pairwise(picks):
        assert p1["surprise"] >= p2["surprise"]
    # All fields populated.
    for p in picks:
        assert {"team_a", "team_b", "round", "expected_score_a", "actual_score_a"} <= p.keys()


@pytest.mark.skipif(not HAS_MPL, reason="matplotlib not available")
def test_analyse_tournament_writes_all_artefacts(tmp_path):
    data = _synthetic_tournament(["a", "b", "c", "d"])
    trn_dir = tmp_path / "trn"
    trn_dir.mkdir()
    (trn_dir / "tournament.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    status = a.analyse_tournament(trn_dir, out, top_k=2)
    assert status["matplotlib"] is True
    assert status["rating_trajectory"] is True
    assert status["win_matrix_heatmap"] is True
    assert status["clustering"] is True
    assert status["top_matches"] == 2
    for name in (
        "rating_trajectory.png",
        "win_matrix_heatmap.png",
        "clustering.png",
        "top_matches.json",
    ):
        assert (out / name).is_file(), f"missing {name}"
    # top_matches.json must be valid JSON.
    picks = json.loads((out / "top_matches.json").read_text())
    assert len(picks) == 2


@pytest.mark.skipif(HAS_MPL, reason="only run when matplotlib missing")
def test_analyse_tournament_skipped_without_mpl(tmp_path):
    data = _synthetic_tournament(["a", "b"])
    trn_dir = tmp_path / "trn"
    trn_dir.mkdir()
    (trn_dir / "tournament.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    status = a.analyse_tournament(trn_dir, out)
    assert status["matplotlib"] is False
    assert (out / "skipped.json").is_file()
    # top_matches.json still produced — it's plot-free.
    assert (out / "top_matches.json").is_file()


def test_cli_rejects_no_input(tmp_path):
    rc = a.main(["--out", str(tmp_path / "o")])
    assert rc == a.EXIT_USAGE


def test_cli_accepts_tournament(tmp_path):
    data = _synthetic_tournament(["pursuit", "cluster", "stationary"])
    trn_dir = tmp_path / "trn"
    trn_dir.mkdir()
    (trn_dir / "tournament.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    rc = a.main(["--tournament", str(trn_dir), "--out", str(out)])
    assert rc == a.EXIT_OK
    assert (out / "summary.json").is_file()
    assert (out / "tournament" / "top_matches.json").is_file()
