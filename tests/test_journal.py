"""Tests for ``scripts/journal.py`` (M15c learning journal).

Coverage:

* Schema validation (happy path, stall path, missing fields).
* Metric grounding: 1 % tolerance, string equality, unknown-key rejection.
* Rewrite budget: fallback entry written after 2 failed rewrites.
* Canonicalisation: tag case/spacing / deduplication.
* Recall determinism: byte-for-byte identical output on repeat.
* Recall policy: recency, extremes, tag overlap, stall inclusion.
* Byte-cap truncation.
* Corrupted-trailing-line tolerance.
* ``render_for_prompt`` contains structured data first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from journal import (
    JOURNAL_SCHEMA_VERSION,
    MAX_REWRITES,
    METRIC_REL_TOLERANCE,
    append_entry,
    canonicalise_entry,
    read_entries,
    recall,
    render_for_prompt,
    validate_against_aar,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _ok_entry(
    generation: int,
    *,
    verdict: str = "confirmed",
    fitness: float | None = 0.5,
    fitness_delta: float | None = 0.1,
    tags: list[str] | None = None,
    cited: dict | None = None,
    status: str = "ok",
) -> dict:
    if tags is None:
        tags = ["tight_formation", "focus_fire"]
    if cited is None:
        cited = {"focus_fire_redundancy": 0.25}
    entry = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "generation": generation,
        "timestamp_utc": "2026-04-23T20:00:00Z",
        "parent_generation": generation - 1 if generation > 0 else None,
        "track": "A",
        "model": "claude-opus-4-7",
        "seed": 42,
        "status": status,
        "fitness": fitness,
        "fitness_delta": fitness_delta,
        "outcome_summary": f"gen {generation} ran",
        "hypothesis_tested": "try tighter formation at close range",
        "mechanism_expected": "concentrated fire wins exchanges",
        "mechanism_observed": "focus fire lands consistently",
        "verdict": verdict,
        "tactic_tags": tags,
        "advice_to_future_self": "keep formation tight under 30 units",
        "aar_metrics_cited": cited,
        "validation": {
            "schema_valid": True,
            "metrics_match_aar": True,
            "rewrites": 0,
        },
    }
    return entry


def _stall_entry(generation: int) -> dict:
    e = _ok_entry(
        generation,
        verdict="stalled",
        fitness=None,
        fitness_delta=None,
        status="compile_failed",
        cited={},
    )
    e["tactic_tags"] = []
    return e


_AAR_OK = {
    "focus_fire_redundancy": 0.25,
    "mean_pairwise_distance_us": 12.34,
    "cooldown_utilization_us": 0.31,
    "outcome": "TEAM_A_WIN",
}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_schema_accepts_ok_entry() -> None:
    entry = canonicalise_entry(_ok_entry(1))
    result = validate_against_aar(entry, _AAR_OK)
    assert result.ok, result.errors
    assert result.schema_valid
    assert result.metrics_match_aar


def test_schema_accepts_stall_entry() -> None:
    entry = canonicalise_entry(_stall_entry(3))
    result = validate_against_aar(entry, None)
    assert result.ok, result.errors


def test_schema_rejects_missing_required_field() -> None:
    entry = canonicalise_entry(_ok_entry(1))
    del entry["hypothesis_tested"]
    result = validate_against_aar(entry, _AAR_OK)
    assert not result.ok
    assert not result.schema_valid
    assert any("hypothesis_tested" in e for e in result.errors)


def test_schema_rejects_stalled_with_non_null_fitness() -> None:
    """Stall entries must have null fitness (allOf if/then/else branch)."""
    entry = canonicalise_entry(_stall_entry(2))
    entry["fitness"] = 0.5
    result = validate_against_aar(entry, None)
    assert not result.ok


def test_schema_rejects_ok_with_stalled_verdict() -> None:
    entry = canonicalise_entry(_ok_entry(1))
    entry["verdict"] = "stalled"
    result = validate_against_aar(entry, _AAR_OK)
    assert not result.ok


# ---------------------------------------------------------------------------
# Metric grounding
# ---------------------------------------------------------------------------


def test_metric_grounding_accepts_within_tolerance() -> None:
    aar = {"focus_fire_redundancy": 0.2500}
    entry = canonicalise_entry(_ok_entry(1, cited={"focus_fire_redundancy": 0.2505}))
    # relative error 0.2% < 1% tolerance
    result = validate_against_aar(entry, aar)
    assert result.ok


def test_metric_grounding_rejects_outside_tolerance() -> None:
    aar = {"focus_fire_redundancy": 0.25}
    entry = canonicalise_entry(_ok_entry(1, cited={"focus_fire_redundancy": 0.30}))
    result = validate_against_aar(entry, aar)
    assert not result.ok
    assert not result.metrics_match_aar
    assert any("focus_fire_redundancy" in e for e in result.errors)


def test_metric_grounding_rejects_unknown_key() -> None:
    aar = {"focus_fire_redundancy": 0.25}
    entry = canonicalise_entry(_ok_entry(1, cited={"made_up_metric": 0.9}))
    result = validate_against_aar(entry, aar)
    assert not result.ok
    assert any("made_up_metric" in e for e in result.errors)


def test_metric_grounding_string_equality() -> None:
    aar = {"outcome": "TEAM_A_WIN"}
    entry = canonicalise_entry(_ok_entry(1, cited={"outcome": "TEAM_A_WIN"}))
    assert validate_against_aar(entry, aar).ok
    entry2 = canonicalise_entry(_ok_entry(1, cited={"outcome": "TEAM_B_WIN"}))
    assert not validate_against_aar(entry2, aar).ok


def test_metric_grounding_zero_truth_uses_absolute_tolerance() -> None:
    aar = {"shots_hit_them": 0}
    entry = canonicalise_entry(_ok_entry(1, cited={"shots_hit_them": 0.00005}))
    # within 1e-4 absolute
    assert validate_against_aar(entry, aar).ok
    entry2 = canonicalise_entry(_ok_entry(1, cited={"shots_hit_them": 0.01}))
    assert not validate_against_aar(entry2, aar).ok


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def test_canonicalise_lowercases_and_snakecases_tags() -> None:
    entry = _ok_entry(1, tags=["Tight Formation", "FOCUS-FIRE", "tight_formation"])
    canon = canonicalise_entry(entry)
    # Duplicates collapsed; spaces/dashes -> underscore.
    assert canon["tactic_tags"] == ["tight_formation", "focus_fire"]


def test_canonicalise_drops_nonstring_tags() -> None:
    entry = _ok_entry(1, tags=["ok_tag"])
    entry["tactic_tags"] = ["ok_tag", 42, None]  # type: ignore[list-item]
    canon = canonicalise_entry(entry)
    assert canon["tactic_tags"] == ["ok_tag"]


def test_canonicalise_defaults_schema_version() -> None:
    entry = _ok_entry(1)
    del entry["schema_version"]
    canon = canonicalise_entry(entry)
    assert canon["schema_version"] == JOURNAL_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Append path
# ---------------------------------------------------------------------------


def test_append_writes_canonical_line(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    result = append_entry(path, _ok_entry(0), _AAR_OK)
    assert result.ok and result.written
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    # Line must be deterministic (sort_keys); re-encoding yields the same.
    parsed = json.loads(lines[0])
    assert json.dumps(parsed, sort_keys=True) == lines[0]


def test_append_refuses_invalid_without_fallback(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    entry = _ok_entry(0, cited={"nonexistent": 1.0})
    result = append_entry(path, entry, _AAR_OK, allow_fallback=False)
    assert not result.ok
    assert not result.written
    assert not path.exists()


def test_append_writes_fallback_after_rewrite_budget(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    entry = _ok_entry(0, cited={"nonexistent": 1.0})
    entry["validation"]["rewrites"] = MAX_REWRITES  # exhausted
    result = append_entry(path, entry, _AAR_OK)
    assert not result.ok
    assert result.written  # fallback written
    line = json.loads(path.read_text().splitlines()[0])
    assert line["validation"]["metrics_match_aar"] is False
    assert line["tactic_tags"] == []
    assert line["aar_metrics_cited"] == {}
    assert "fallback" in line["validation"]["notes"]


def test_append_fallback_preserves_stall_nulls(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    entry = _stall_entry(4)
    # Make it invalid by citing a non-existent metric and exhausting budget.
    entry["aar_metrics_cited"] = {"nonexistent": 1.0}
    entry["validation"]["rewrites"] = MAX_REWRITES
    result = append_entry(path, entry, _AAR_OK)
    assert result.written and not result.ok
    line = json.loads(path.read_text().splitlines()[0])
    assert line["status"] == "compile_failed"
    assert line["verdict"] == "stalled"
    assert line["fitness"] is None
    assert line["fitness_delta"] is None


def test_append_appends_not_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    for g in range(3):
        r = append_entry(path, _ok_entry(g), _AAR_OK)
        assert r.ok and r.written
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    gens = [json.loads(ln)["generation"] for ln in lines]
    assert gens == [0, 1, 2]


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def test_read_nonexistent_returns_empty(tmp_path: Path) -> None:
    assert read_entries(tmp_path / "never.jsonl") == []


def test_read_tolerates_trailing_partial_line(tmp_path: Path) -> None:
    """Corrupt trailing line (kill -9 mid-write) must not raise."""
    path = tmp_path / "journal.jsonl"
    append_entry(path, _ok_entry(0), _AAR_OK)
    # Append a truncated line (no newline, invalid JSON).
    with path.open("a") as fh:
        fh.write('{"generation":1,"timestamp_utc":"2026-04-23T20:00:00')
    entries = read_entries(path)
    assert len(entries) == 1
    assert entries[0]["generation"] == 0


def test_read_raises_on_non_trailing_corruption(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    append_entry(path, _ok_entry(0), _AAR_OK)
    # Prepend garbage; corruption is NOT on the trailing line.
    existing = path.read_text()
    path.write_text("GARBAGE\n" + existing)
    with pytest.raises(json.JSONDecodeError):
        read_entries(path)


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


def _build_30_entry_fixture(path: Path, aar: dict) -> None:
    """Populate a path with 30 entries spanning gens 0..29."""
    for g in range(30):
        # Occasional stall at gen 5 and 18.
        if g in (5, 18):
            entry = _stall_entry(g)
        else:
            entry = _ok_entry(
                g,
                fitness=0.5 + 0.01 * g,
                fitness_delta=(0.05 if g % 4 == 0 else -0.02) * (g + 1) / 30,
                tags=["tight_formation"] if g % 3 == 0 else ["spread", "kiting"],
                cited={"focus_fire_redundancy": 0.25},
            )
        append_entry(path, entry, aar)


def test_recall_deterministic_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    out1 = recall(path, planned_tags=["kiting"])
    out2 = recall(path, planned_tags=["kiting"])
    assert json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True)


def test_recall_includes_recency(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, recency_k=3, extremes_k=0, max_entries=10)
    gens = {int(e["generation"]) for e in picked}
    # Last 3 entries are gens 27, 28, 29.
    assert {27, 28, 29}.issubset(gens)


def test_recall_includes_extremes(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, recency_k=0, extremes_k=3, max_entries=10)
    # Largest |delta| in our fixture are at high generations with positive
    # delta (g%4==0) — these must appear.
    deltas = [abs(float(e["fitness_delta"])) for e in picked if e["fitness_delta"] is not None]
    assert len(deltas) <= 3
    assert len(deltas) >= 1


def test_recall_includes_stalls(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, recency_k=1, extremes_k=1, max_entries=10)
    stall_gens = [int(e["generation"]) for e in picked if e.get("verdict") == "stalled"]
    assert stall_gens, "recall must include at least one stall when stalls exist"


def test_recall_tag_overlap_selects_matching(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    # Planned tags target the 'tight_formation' lineage.
    picked = recall(
        path, recency_k=0, extremes_k=0, planned_tags=["tight_formation"], max_entries=20
    )
    gens = {int(e["generation"]) for e in picked}
    # At least one tight_formation entry selected (gens where g%3==0).
    assert any(g % 3 == 0 for g in gens)


def test_recall_caps_at_max_entries(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(
        path,
        recency_k=10,
        extremes_k=10,
        max_entries=5,
        planned_tags=["tight_formation", "spread", "kiting"],
    )
    assert len(picked) <= 5


def test_recall_caps_at_max_bytes(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, max_bytes=500, max_entries=100, planned_tags=["tight_formation"])
    total_bytes = sum(len(json.dumps(e, sort_keys=True)) + 1 for e in picked)
    assert total_bytes <= 500 or len(picked) == 1  # at least one kept


def test_recall_returns_chronological_order(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, planned_tags=["spread"])
    gens = [int(e["generation"]) for e in picked]
    assert gens == sorted(gens)


def test_recall_empty_journal(tmp_path: Path) -> None:
    assert recall(tmp_path / "nothing.jsonl") == []


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_for_prompt_empty() -> None:
    md = render_for_prompt([])
    assert "(none" in md


def test_render_for_prompt_contains_structured_fields(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    picked = recall(path, planned_tags=["kiting"])
    md = render_for_prompt(picked)
    # Structured lines come before advice prose.
    tags_idx = md.find("- tags:")
    advice_idx = md.find("- advice:")
    assert 0 <= tags_idx < advice_idx


def test_render_for_prompt_token_cap() -> None:
    # Build a big entry list and cap aggressively.
    entries = [_ok_entry(g) for g in range(50)]
    md = render_for_prompt(entries, max_tokens=50)  # 200 chars max
    assert "(truncated)" in md or len(md) <= 200 + len("\n...(truncated)\n")


# ---------------------------------------------------------------------------
# Resume semantics (§3.X.7 criterion 3)
# ---------------------------------------------------------------------------


def test_resume_reproduces_next_recall(tmp_path: Path) -> None:
    """Replaying from journal.jsonl reproduces the recall output exactly."""
    path = tmp_path / "journal.jsonl"
    _build_30_entry_fixture(path, _AAR_OK)
    before = recall(path, planned_tags=["spread"])
    # Simulate process restart: reread from disk.
    after = recall(path, planned_tags=["spread"])
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_constants_match_spec() -> None:
    assert JOURNAL_SCHEMA_VERSION == 1
    assert MAX_REWRITES == 2
    assert abs(METRIC_REL_TOLERANCE - 0.01) < 1e-9
