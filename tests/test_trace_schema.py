"""Schema-validation tests for the engine trace format (M4).

Validates:
  1. `docs/trace_schema.json` is a well-formed JSON Schema (draft-07).
  2. Every line of the checked-in golden trace validates against it.
  3. A handful of malformed-line fixtures are *rejected* — so the schema
     actually constrains (a permissive schema that accepts everything would
     pass #2 vacuously).

These tests do NOT depend on a C++ compiler; they read the golden trace
straight from ``tests/fixtures/golden/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "trace_schema.json"
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "seed42_pursuit_vs_cluster.jsonl"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_schema_is_well_formed() -> None:
    """The schema itself must pass draft-07 meta-validation."""
    schema = _load_schema()
    # Raises if the schema is invalid against its own meta-schema.
    Draft7Validator.check_schema(schema)


def test_golden_trace_validates_line_by_line() -> None:
    """Every line of the golden trace must validate against the schema."""
    assert GOLDEN_PATH.exists(), f"golden trace missing: {GOLDEN_PATH}"
    validator = Draft7Validator(_load_schema())
    lines = GOLDEN_PATH.read_text().splitlines()
    assert lines, "golden trace is empty"
    for i, raw in enumerate(lines):
        obj = json.loads(raw)
        errors = sorted(validator.iter_errors(obj), key=lambda e: e.path)
        assert not errors, f"line {i}: {[e.message for e in errors]}"


def test_golden_trace_final_line_has_outcome() -> None:
    """The final trace line must carry the ``outcome`` field."""
    lines = GOLDEN_PATH.read_text().splitlines()
    last = json.loads(lines[-1])
    assert "outcome" in last
    assert last["outcome"] in {"TEAM_A_WIN", "TEAM_B_WIN", "DRAW"}


def test_golden_trace_non_final_lines_have_no_outcome() -> None:
    """Only the final line carries ``outcome``."""
    lines = GOLDEN_PATH.read_text().splitlines()
    for i, raw in enumerate(lines[:-1]):
        obj = json.loads(raw)
        assert "outcome" not in obj, f"line {i} unexpectedly has outcome: {obj.get('outcome')!r}"


# ---------------------------------------------------------------------------
# Negative (malformed) cases. These exist so a vacuous schema can't pass the
# golden-validation tests by accepting everything.
# ---------------------------------------------------------------------------

_VALID_DRONE = {"id": 0, "x": 1.0, "y": 2.0, "cooldown": 0, "alive": True}


@pytest.mark.parametrize(
    "bad_obj,reason",
    [
        ({"tick": -1, "team_a": [_VALID_DRONE], "team_b": [_VALID_DRONE]}, "negative tick"),
        ({"tick": 0, "team_b": [_VALID_DRONE]}, "missing team_a"),
        ({"tick": 0, "team_a": [_VALID_DRONE], "team_b": [_VALID_DRONE], "extra": 1}, "additional property"),
        ({"tick": 0, "team_a": [], "team_b": [_VALID_DRONE]}, "empty team"),
        (
            {
                "tick": 0,
                "team_a": [{"id": 0, "x": 1.0, "y": 2.0, "cooldown": -1, "alive": True}],
                "team_b": [_VALID_DRONE],
            },
            "negative cooldown",
        ),
        (
            {
                "tick": 0,
                "team_a": [{"id": 0, "x": "one", "y": 2.0, "cooldown": 0, "alive": True}],
                "team_b": [_VALID_DRONE],
            },
            "string x",
        ),
        (
            {
                "tick": 0,
                "team_a": [_VALID_DRONE],
                "team_b": [_VALID_DRONE],
                "outcome": "TEAM_C_WIN",
            },
            "invalid outcome enum",
        ),
    ],
)
def test_schema_rejects_malformed(bad_obj: dict, reason: str) -> None:
    """Each malformed fixture must raise ``ValidationError``."""
    validator = Draft7Validator(_load_schema())
    with pytest.raises(ValidationError):
        validator.validate(bad_obj)
