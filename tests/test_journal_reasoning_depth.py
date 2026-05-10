#!/usr/bin/env python3
"""Tests for M21 enhanced journal validation (reasoning depth checks)."""

import sys
from pathlib import Path

# Add scripts to path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import journal


def _make_entry(**overrides):
    """Helper to create a complete journal entry with required fields."""
    entry = {
        "generation": 0,
        "timestamp_utc": "2026-04-24T20:00:00Z",
        "track": "A",
        "model": "test-model",
        "seed": 42,
        "status": "ok",
        "parent_generation": None,
        "hypothesis_tested": "default hypothesis with more than ten words for validation",
        "mechanism_observed": "default mechanism with metrics",
        "advice_to_future_self": "default advice",
        "tactic_tags": ["default_tag1", "default_tag2"],
        "aar_metrics_cited": {},
        "fitness": 0.0,
        "fitness_delta": None,
        "outcome_summary": "test",
        "verdict": "confirmed",
        "validation": {"schema_valid": True, "metrics_match_aar": True, "rewrites": 0},
    }
    entry.update(overrides)
    return journal.canonicalise_entry(entry)


def test_hypothesis_too_short():
    """Reject entries with hypothesis < 10 words."""
    entry = _make_entry(hypothesis_tested="short hypothesis")
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("hypothesis_tested too short" in e for e in result.errors)


def test_mechanism_no_metric_citation():
    """Reject entries where mechanism doesn't cite any metric."""
    entry = _make_entry(
        mechanism_observed="generic description with no metrics",
        aar_metrics_cited={"cooldown_utilization_us": 0.5},
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("mechanism_observed does not cite any AAR metric" in e for e in result.errors)


def test_mechanism_cites_metric_passes():
    """Accept entries where mechanism cites a metric."""
    entry = _make_entry(
        hypothesis_tested="testing whether message coordination improves targeting efficiency in drone formation tactics",  # 12 words
        mechanism_observed="cooldown_utilization_us improved from 0.3 to 0.5 in current generation",
        aar_metrics_cited={"cooldown_utilization_us": 0.5},
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert result.ok, f"Errors: {result.errors}"


def test_banned_phrase_rejection():
    """Reject entries with banned phrases in advice."""
    entry = _make_entry(advice_to_future_self="try a different mechanism next time")
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("banned phrase" in e for e in result.errors)


def test_carry_forward_low_win_rate():
    """Reject 'carry forward' with win rate < 0.8."""
    entry = _make_entry(
        advice_to_future_self="carry forward current approach",
        outcome_summary="accepted: a=5 b=4 draws=1 invalid=0",  # 5/10 = 0.5
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("'carry forward' not allowed" in e and "< 0.8" in e for e in result.errors)


def test_carry_forward_high_win_rate_passes():
    """Accept 'carry forward' with win rate >= 0.8."""
    entry = _make_entry(
        hypothesis_tested="maintain current strategy as it achieves consistent wins against opponent baseline",  # 11 words
        mechanism_observed="cooldown_utilization_us at 0.8 maintained parity with opponent performance",
        advice_to_future_self="carry forward current winning strategy",
        outcome_summary="accepted: a=9 b=1 draws=0 invalid=0",  # 9/10 = 0.9
        aar_metrics_cited={"cooldown_utilization_us": 0.8},
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert result.ok, f"Errors: {result.errors}"


def test_tags_too_few():
    """Reject entries with < 2 tags."""
    entry = _make_entry(tactic_tags=["only_one"])
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("tactic_tags must have >=2 tags" in e for e in result.errors)


def test_tags_only_generic():
    """Reject entries with only generic tags."""
    entry = _make_entry(tactic_tags=["accept_if_better", "status_quo"])
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert not result.ok
    assert any("non-generic tag" in e for e in result.errors)


def test_strict_mode_off_bypasses():
    """When strict_reflection=False, reasoning checks are skipped."""
    entry = _make_entry(
        hypothesis_tested="short",
        advice_to_future_self="try a different mechanism",
        tactic_tags=["one"],
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=False)
    # Should pass because strict mode is off
    assert result.ok, f"Errors: {result.errors}"


def test_high_quality_entry_passes():
    """High-quality entry passes all strict checks."""
    entry = _make_entry(
        hypothesis_tested="Use message coordination to reduce focus fire redundancy from previous high levels",
        mechanism_observed="focus_fire_redundancy dropped from 0.58 to 0.21 due to message protocol",
        advice_to_future_self="Continue message-based targeting coordination, monitor for regression",
        outcome_summary="accepted: a=7 b=2 draws=1 invalid=0",
        tactic_tags=["message_coordination", "focus_fire_reduction", "targeting"],
        aar_metrics_cited={"focus_fire_redundancy": 0.21, "cooldown_utilization_us": 0.65},
        fitness=0.5,
        fitness_delta=0.3,
    )
    result = journal.validate_against_aar(entry, aar=None, strict_reflection=True)
    assert result.ok, f"Errors: {result.errors}"


if __name__ == "__main__":
    # Run all tests
    import inspect

    tests = [
        obj
        for name, obj in globals().items()
        if name.startswith("test_") and inspect.isfunction(obj)
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"✅ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
