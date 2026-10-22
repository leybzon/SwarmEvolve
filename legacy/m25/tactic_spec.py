#!/usr/bin/env python3
"""TacticSpec schema and validation for dual-LLM architecture.

The planner LLM outputs a TacticSpec (structured JSON) describing a
tactical counter-move. This module validates the spec before passing it
to the coder LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Valid AAR metric names (from telemetry_aar.py AARReport.structured)
VALID_AAR_METRICS = {
    "outcome",
    "ticks",
    "alive_final_us",
    "alive_final_them",
    "shots_fired_us",
    "shots_fired_them",
    "shots_hit_us",
    "shots_hit_them",
    "focus_fire_redundancy",
    "cooldown_utilization_us",
    "cooldown_utilization_them",
    "mean_pairwise_distance_us",
    "mean_pairwise_distance_them",
    "message_bus_entropy",
    "kiting_score_them",
    "engagement_range_mean",
}


@dataclass
class MetricChange:
    """Predicted change to a single AAR metric."""

    metric: str
    old_value: float | str
    target_value: float | str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "old_value": self.old_value,
            "target_value": self.target_value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MetricChange:
        return cls(
            metric=str(d["metric"]),
            old_value=d["old_value"],
            target_value=d["target_value"],
            reason=str(d["reason"]),
        )


@dataclass
class TacticSpec:
    """Structured tactical specification from planner LLM."""

    # OBSERVE
    key_metrics: list[str] = field(default_factory=list)

    # ORIENT
    why_we_failed: str = ""
    what_enemy_exploited: str = ""
    constraints_violated: str = ""

    # DECIDE
    tactic_name: str = ""
    mechanism: str = ""
    why_this_counters_failure: str = ""

    # ACT
    expected_changes: list[MetricChange] = field(default_factory=list)

    # IMPLEMENTATION GUIDANCE
    message_protocol: str = ""
    memory_layout: str = ""
    special_cases: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for JSON storage or prompts)."""
        return {
            "observe": {
                "key_metrics": self.key_metrics,
            },
            "orient": {
                "why_we_failed": self.why_we_failed,
                "what_enemy_exploited": self.what_enemy_exploited,
                "constraints_violated": self.constraints_violated,
            },
            "decide": {
                "tactic_name": self.tactic_name,
                "mechanism": self.mechanism,
                "why_this_counters_failure": self.why_this_counters_failure,
            },
            "act": {
                "expected_changes": [c.to_dict() for c in self.expected_changes],
            },
            "implementation_guidance": {
                "message_protocol": self.message_protocol,
                "memory_layout": self.memory_layout,
                "special_cases": self.special_cases,
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TacticSpec:
        """Parse from dict (from planner JSON response)."""
        observe = d.get("observe", {})
        orient = d.get("orient", {})
        decide = d.get("decide", {})
        act = d.get("act", {})
        impl = d.get("implementation_guidance", {})

        return cls(
            key_metrics=list(observe.get("key_metrics", [])),
            why_we_failed=str(orient.get("why_we_failed", "")),
            what_enemy_exploited=str(orient.get("what_enemy_exploited", "")),
            constraints_violated=str(orient.get("constraints_violated", "")),
            tactic_name=str(decide.get("tactic_name", "")),
            mechanism=str(decide.get("mechanism", "")),
            why_this_counters_failure=str(decide.get("why_this_counters_failure", "")),
            expected_changes=[MetricChange.from_dict(c) for c in act.get("expected_changes", [])],
            message_protocol=str(impl.get("message_protocol", "")),
            memory_layout=str(impl.get("memory_layout", "")),
            special_cases=str(impl.get("special_cases", "")),
        )


class TacticSpecValidationError(ValueError):
    """Raised when a TacticSpec fails validation."""

    pass


def validate_tactic_spec(spec: TacticSpec) -> None:
    """Validate a TacticSpec against the schema rules.

    Raises TacticSpecValidationError if any rule is violated.
    """
    errors: list[str] = []

    # Rule 1: key_metrics must have 5–8 entries
    if not (5 <= len(spec.key_metrics) <= 8):
        errors.append(f"key_metrics must have 5–8 entries, got {len(spec.key_metrics)}")

    # Rule 2: All orient fields must be present and non-empty
    for field_name in ["why_we_failed", "what_enemy_exploited", "constraints_violated"]:
        val = getattr(spec, field_name, "")
        if not val or not val.strip():
            errors.append(f"orient.{field_name} cannot be empty")

    # Rule 3: mechanism must be >= 20 words (forces specificity)
    mechanism_words = len(spec.mechanism.split())
    if mechanism_words < 20:
        errors.append(f"decide.mechanism must be >=20 words, got {mechanism_words}")

    # Rule 4: expected_changes must have >= 2 entries
    if len(spec.expected_changes) < 2:
        errors.append(
            f"act.expected_changes must have >=2 entries, got {len(spec.expected_changes)}"
        )

    # Rule 5: Every metric name must match AAR schema
    for change in spec.expected_changes:
        if change.metric not in VALID_AAR_METRICS:
            errors.append(
                f"Invalid metric '{change.metric}', must be one of {sorted(VALID_AAR_METRICS)}"
            )

    # Rule 6: tactic_name, why_this_counters_failure must be present
    if not spec.tactic_name or not spec.tactic_name.strip():
        errors.append("decide.tactic_name cannot be empty")
    if not spec.why_this_counters_failure or not spec.why_this_counters_failure.strip():
        errors.append("decide.why_this_counters_failure cannot be empty")

    # Rule 7: implementation_guidance fields must be present (can be "unused" or "none")
    for field_name in ["message_protocol", "memory_layout", "special_cases"]:
        val = getattr(spec, field_name, "")
        if not val or not val.strip():
            errors.append(f"implementation_guidance.{field_name} cannot be empty")

    if errors:
        raise TacticSpecValidationError(
            "TacticSpec validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def parse_and_validate_tactic_spec(json_text: str) -> TacticSpec:
    """Parse JSON and validate in one step.

    Raises:
        json.JSONDecodeError: if json_text is malformed
        TacticSpecValidationError: if spec violates schema rules
    """
    data = json.loads(json_text)
    spec = TacticSpec.from_dict(data)
    validate_tactic_spec(spec)
    return spec


if __name__ == "__main__":
    # Self-test fixture
    import sys

    fixture = {
        "observe": {
            "key_metrics": [
                "Outcome: LOSS (1/3/6 W/L/D)",
                "Cooldown util: 0.31 vs 0.89",
                "Focus-fire: 0.58",
                "Mean pairwise: 12.3",
                "Message entropy: 0.0",
            ],
        },
        "orient": {
            "why_we_failed": "Low cooldown utilization (0.31) due to tight clustering",
            "what_enemy_exploited": "pursuit_v1 cornered our clustered drones",
            "constraints_violated": "Message bus unused (entropy=0.0)",
        },
        "decide": {
            "tactic_name": "Message-Coordinated Targeting",
            "mechanism": (
                "Each drone broadcasts intended target_id in message_out[0]. "
                "Before selecting target, count how many allies are already "
                "targeting each enemy. Skip enemies with >=2 claimants. This "
                "reduces focus-fire redundancy from 0.58 to <0.20."
            ),
            "why_this_counters_failure": "Addresses focus_fire_redundancy=0.58",
        },
        "act": {
            "expected_changes": [
                {
                    "metric": "focus_fire_redundancy",
                    "old_value": 0.58,
                    "target_value": 0.20,
                    "reason": "message coordination",
                },
                {
                    "metric": "cooldown_utilization_us",
                    "old_value": 0.31,
                    "target_value": 0.50,
                    "reason": "more engagements",
                },
            ],
        },
        "implementation_guidance": {
            "message_protocol": "message_out[0]=target_id",
            "memory_layout": "unused",
            "special_cases": "skip if >=2 allies already targeting",
        },
    }

    try:
        spec = parse_and_validate_tactic_spec(json.dumps(fixture))
        print("✅ Fixture passes validation")
        print(f"   Tactic: {spec.tactic_name}")
        print(f"   Mechanism words: {len(spec.mechanism.split())}")
        print(f"   Expected changes: {len(spec.expected_changes)}")
    except (json.JSONDecodeError, TacticSpecValidationError) as e:
        print(f"❌ Fixture failed: {e}", file=sys.stderr)
        sys.exit(1)
