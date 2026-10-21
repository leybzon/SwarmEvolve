"""Regression tests (D1) for :mod:`scripts.dual_llm`: the evolving team's
own champion code (the parent) reaches both the planner and the coder.

Offline: uses ``MockClient``; no API calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dual_llm
from llm_client import LLMResponse, MockClient

PARENT = "// PARENT-SOURCE-MARKER\nnamespace TeamA {}\n"
OPPONENT = "// OPPONENT-SOURCE-MARKER\nnamespace TeamB {}\n"

PLANNER_JSON = json.dumps(
    {
        "observe": {
            "key_metrics": [
                "Outcome: DRAW",
                "Cooldown util: 0.5 vs 0.5",
                "Focus-fire: 0.2",
                "Mean pairwise: 100.0",
                "Message entropy: 0.5",
            ],
        },
        "orient": {
            "why_we_failed": "Equal metrics led to stalemate",
            "what_enemy_exploited": "None, draw scenario",
            "constraints_violated": "None identified",
        },
        "decide": {
            "tactic_name": "Maintain Status Quo",
            "mechanism": (
                "Continue current approach as it achieves parity. "
                "No changes needed when metrics are balanced and "
                "we're not losing. Monitor for any shifts in next "
                "generation before committing to new tactics."
            ),
            "why_this_counters_failure": "No failure to counter, draw is acceptable",
        },
        "act": {
            "expected_changes": [
                {
                    "metric": "outcome",
                    "old_value": "DRAW",
                    "target_value": "DRAW",
                    "reason": "maintain parity",
                },
                {
                    "metric": "cooldown_utilization_us",
                    "old_value": 0.5,
                    "target_value": 0.5,
                    "reason": "no change",
                },
            ],
        },
        "implementation_guidance": {
            "message_protocol": "unchanged",
            "memory_layout": "unchanged",
            "special_cases": "none",
        },
    }
)


def _planner_kwargs(**over):
    kw = dict(
        team_letter="A",
        opponent_name="opp",
        opponent_source=OPPONENT,
        parent_source=PARENT,
        aar_markdown="AAR",
        prior_lessons="none",
    )
    kw.update(over)
    return kw


@pytest.mark.parametrize("use_v2", [True, False])
def test_planner_prompt_contains_parent_source(use_v2):
    prompt = dual_llm.render_planner_prompt(**_planner_kwargs(), use_v2_prompt=use_v2)
    assert "PARENT-SOURCE-MARKER" in prompt
    assert "{PARENT_SOURCE}" not in prompt


def test_coder_prompt_contains_parent_source():
    prompt = dual_llm.render_coder_prompt(
        team_letter="A",
        namespace="TeamA",
        opponent_name="opp",
        opponent_source=OPPONENT,
        parent_source=PARENT,
        tactic_spec_json=PLANNER_JSON,
        types_header="",
        abi_header="",
    )
    assert "PARENT-SOURCE-MARKER" in prompt
    assert "{PARENT_SOURCE}" not in prompt


def test_template_without_parent_slot_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "planner_analyze_aar_v2.md").write_text("{OPPONENT_SOURCE}\n{AAR}\n")
    monkeypatch.setattr(dual_llm, "PROMPTS", tmp_path)
    with pytest.raises(ValueError, match="PARENT_SOURCE"):
        dual_llm.render_planner_prompt(**_planner_kwargs())


def test_generate_sends_parent_to_planner_and_coder():
    planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")])
    coder = MockClient([LLMResponse(text="```cpp\nnamespace TeamA {}\n```", model="mock-coder")])
    dual_llm.dual_llm_generate(
        planner_client=planner,
        coder_client=coder,
        namespace="TeamA",
        **_planner_kwargs(),
    )
    assert "PARENT-SOURCE-MARKER" in planner.calls[0]
    assert "PARENT-SOURCE-MARKER" in coder.calls[0]
