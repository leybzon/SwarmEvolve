"""Tests for ``scripts.render_report`` (M12 exit criterion).

The criterion is:

    Final report template renders from a completed experiment
    directory with zero manual edits.

So we construct a synthetic experiment directory and assert that
``render_report`` produces a non-empty Markdown document containing
the expected section headers. No matches are run — we feed canned
JSON directly, matching what ``scripts/tournament.py`` and
``scripts/evolve.py`` emit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import render_report as rr  # noqa: E402


def _write_tournament_json(dir_: Path) -> None:
    data = {
        "mode": "round_robin",
        "n_matches": 5,
        "seed_base": 42,
        "elo_k": 32.0,
        "elo_start": 1500.0,
        "entries": [
            {"name": "pursuit", "path": "/tmp/pursuit.cpp", "sha256": "a" * 64},
            {"name": "cluster", "path": "/tmp/cluster.cpp", "sha256": "b" * 64},
        ],
        "rounds": [
            {
                "index": 0,
                "pairings": [
                    {
                        "team_a": "pursuit",
                        "team_b": "cluster",
                        "n_matches": 5,
                        "wins_a": 4,
                        "wins_b": 1,
                        "draws": 0,
                        "invalid": 0,
                        "seed_base": 7,
                    },
                    {
                        "team_a": "cluster",
                        "team_b": "pursuit",
                        "n_matches": 5,
                        "wins_a": 1,
                        "wins_b": 3,
                        "draws": 1,
                        "invalid": 0,
                        "seed_base": 8,
                    },
                ],
                "ratings_snapshot": {"cluster": 1450.0, "pursuit": 1550.0},
            }
        ],
        "final_ratings": {"cluster": 1450.0, "pursuit": 1550.0},
        "win_matrix": {
            "pursuit": {
                "pursuit": {"wins_a": 0, "wins_b": 0, "draws": 0, "invalid": 0},
                "cluster": {"wins_a": 4, "wins_b": 1, "draws": 0, "invalid": 0},
            },
            "cluster": {
                "pursuit": {"wins_a": 1, "wins_b": 3, "draws": 1, "invalid": 0},
                "cluster": {"wins_a": 0, "wins_b": 0, "draws": 0, "invalid": 0},
            },
        },
    }
    (dir_ / "tournament.json").write_text(json.dumps(data))


def _write_events_jsonl(dir_: Path) -> None:
    events = [
        {
            "seq": 0,
            "ts": "2026-04-22T00:00:00.000000+00:00",
            "type": "experiment_start",
            "experiment_type": "tournament",
            "config": {},
            "environment": {
                "git_sha": "abc1234",
                "git_dirty": False,
                "python_version": "3.12.8",
                "platform": "darwin",
            },
        },
        {"seq": 1, "ts": "2026-04-22T00:00:01.000000+00:00", "type": "tournament_end"},
    ]
    with (dir_ / "events.jsonl").open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _write_evolve_state(dir_: Path) -> None:
    state = {
        "history": [
            {"gen": 0, "status": "accepted", "mean": 0.1, "ci_low": -0.05, "ci_high": 0.25},
            {"gen": 1, "status": "rejected", "mean": 0.05, "ci_low": -0.1, "ci_high": 0.2},
            {"gen": 2, "status": "accepted", "mean": 0.3, "ci_low": 0.15, "ci_high": 0.45},
        ]
    }
    (dir_ / "state.json").write_text(json.dumps(state))


def test_report_renders_tournament_sections(tmp_path):
    _write_tournament_json(tmp_path)
    _write_events_jsonl(tmp_path)
    md = rr.render_report(tmp_path)
    assert "# SwarmEvolve experiment report" in md
    assert "## Environment" in md
    assert "## Tournament results" in md
    assert "### Final ratings" in md
    assert "`pursuit`" in md
    assert "`cluster`" in md
    # No evolution section — we didn't write state.json.
    assert "## Evolution run" not in md


def test_report_renders_evolve_section(tmp_path):
    _write_evolve_state(tmp_path)
    md = rr.render_report(tmp_path)
    assert "## Evolution run" in md
    assert "Generations attempted: 3" in md
    assert "Accepted generations: 2" in md
    assert "Champion fitness mean: 0.3000" in md


def test_report_handles_empty_dir_gracefully(tmp_path):
    md = rr.render_report(tmp_path)
    assert "## No results" in md
    # Still valid Markdown, still has a title.
    assert md.startswith("# SwarmEvolve experiment report")


def test_cli_writes_report_md(tmp_path):
    _write_tournament_json(tmp_path)
    rc = rr.main([str(tmp_path)])
    assert rc == rr.EXIT_OK
    out = tmp_path / "report.md"
    assert out.is_file()
    text = out.read_text()
    assert "Tournament results" in text


def test_cli_custom_output_path(tmp_path):
    _write_tournament_json(tmp_path)
    custom = tmp_path / "subdir" / "my_report.md"
    rc = rr.main([str(tmp_path), "--out", str(custom)])
    assert rc == rr.EXIT_OK
    assert custom.is_file()


def test_cli_rejects_missing_dir(tmp_path):
    rc = rr.main([str(tmp_path / "does_not_exist")])
    assert rc == rr.EXIT_MISSING_INPUT


def test_report_references_analysis_plots_if_present(tmp_path):
    _write_tournament_json(tmp_path)
    analysis = tmp_path / "analysis"
    (analysis / "tournament").mkdir(parents=True)
    # Empty PNG placeholders.
    for name in ("rating_trajectory.png", "win_matrix_heatmap.png", "clustering.png"):
        (analysis / "tournament" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    # Empty top_matches.
    (analysis / "tournament" / "top_matches.json").write_text("[]")
    md = rr.render_report(tmp_path)
    assert "![rating_trajectory.png](analysis/tournament/rating_trajectory.png)" in md
    assert "![win_matrix_heatmap.png]" in md
    assert "![clustering.png]" in md


def test_report_includes_top_matches_table(tmp_path):
    _write_tournament_json(tmp_path)
    analysis = tmp_path / "analysis" / "tournament"
    analysis.mkdir(parents=True)
    picks = [
        {
            "round": 0,
            "team_a": "pursuit",
            "team_b": "cluster",
            "n_matches": 5,
            "wins_a": 4,
            "wins_b": 1,
            "draws": 0,
            "expected_score_a": 0.5,
            "actual_score_a": 0.8,
            "surprise": 0.3,
        }
    ]
    (analysis / "top_matches.json").write_text(json.dumps(picks))
    md = rr.render_report(tmp_path)
    assert "### Most informative matches" in md
    assert "| 0 | `pursuit` | `cluster` | 0.500 | 0.800 | 0.300 |" in md
