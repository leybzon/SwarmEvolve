"""Tests for :mod:`scripts.dual_llm` and the v3 prompts.

The inherit switch puts the evolving team's champion (the parent) into both
prompts byte for byte, or leaves it out; the feedback switch gives the
planner the AAR, the journal lessons and the champion's numbers, or none of
them. Offline: uses ``MockClient``; no API calls.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dual_llm
import tactic_spec
from llm_client import LLMResponse, MockClient

# Text that a naive placeholder fill would corrupt: slots and markers inside
# the inserted source must reach the prompt unchanged.
PARENT = (
    "// PARENT-SOURCE-MARKER {AAR} {PARENT_CODE} {TEAM_LETTER}\n"
    "<!-- end parent -->\n"
    "namespace TeamA { int f() { return 1; } }\n"
)
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


AAR = "# After-Action Report (perspective: Team A)\n\n- **Outcome**: DRAW after 9 ticks\n"
LESSONS = "Gen 0: confirmed (fitness=0.100) - LESSON-MARKER"


def _planner_kwargs(**over):
    kw = dict(
        team_letter="A",
        opponent_name="opp",
        opponent_source=OPPONENT,
        parent_source=PARENT,
        aar_markdown=AAR,
        prior_lessons=LESSONS,
        champion_fitness=0.25,
    )
    kw.update(over)
    return kw


def _coder_prompt(parent_source):
    return dual_llm.render_coder_prompt(
        team_letter="A",
        namespace="TeamA",
        opponent_name="opp",
        opponent_source=OPPONENT,
        parent_source=parent_source,
        tactic_spec_json=PLANNER_JSON,
        types_header="",
        abi_header="",
    )


def _assert_clean(prompt):
    """No block marker or unfilled slot outside the inserted parent source."""
    rest = prompt.replace(PARENT, "")
    assert not dual_llm._MARKER.search(rest)
    leftover = set(dual_llm._SLOT.findall(rest))
    assert not leftover, leftover


def test_prompts_use_the_v3_templates():
    assert dual_llm.PLANNER_TEMPLATE == REPO_ROOT / "prompts" / "v3" / "planner.md"
    assert dual_llm.CODER_TEMPLATE == REPO_ROOT / "prompts" / "v3" / "coder.md"


def test_planner_prompt_carries_parent_byte_for_byte():
    prompt = dual_llm.render_planner_prompt(**_planner_kwargs())
    assert f"```cpp\n{PARENT}\n```" in prompt
    assert "## Parent Program (Your Team's Current Champion)" in prompt
    _assert_clean(prompt)


def test_coder_prompt_carries_parent_and_says_to_start_from_it():
    prompt = _coder_prompt(PARENT)
    assert f"```cpp\n{PARENT}\n```" in prompt
    assert "Start from this program. Change only what the tactical specification requires" in prompt
    _assert_clean(prompt)


def test_inherit_none_leaves_the_parent_out():
    planner = dual_llm.render_planner_prompt(**_planner_kwargs(parent_source=None))
    coder = _coder_prompt(None)
    for prompt in (planner, coder):
        assert "PARENT-SOURCE-MARKER" not in prompt
        assert "Parent Program" not in prompt
        _assert_clean(prompt)


def test_feedback_aar_carries_aar_lessons_and_champion_numbers():
    prompt = dual_llm.render_planner_prompt(**_planner_kwargs())
    assert f"# After-Action Report (Current Champion)\n\n{AAR}\n" in prompt
    assert f"# Lessons from Prior Generations\n\n{LESSONS}\n" in prompt
    assert "**Champion Fitness:** 0.250" in prompt


def test_feedback_none_leaves_aar_lessons_and_champion_numbers_out():
    prompt = dual_llm.render_planner_prompt(
        **_planner_kwargs(aar_markdown=None, prior_lessons=None, champion_fitness=None)
    )
    for absent in ("After-Action Report (Current Champion)", "Lessons from Prior", "LESSON-MARKER",
                   "Outcome**: DRAW", "**Champion Fitness:**", "**Last Hypothesis:**"):  # fmt: skip
        assert absent not in prompt, absent
    _assert_clean(prompt)


def test_v3_planner_lists_exactly_the_metrics_the_validator_accepts():
    text = dual_llm.PLANNER_TEMPLATE.read_text()
    section = text.split("Valid metric names:", 1)[1].split("**Do not invent metrics.**", 1)[0]
    listed = set(re.findall(r"^- `(\w+)`", section, re.MULTILINE))
    listed |= {"cooldown_utilization_them", "mean_pairwise_distance_them", "alive_final_them",
               "shots_fired_them", "shots_hit_them"}  # fmt: skip
    assert listed == tactic_spec.VALID_AAR_METRICS


def test_unknown_block_and_unfilled_slot_are_errors(tmp_path):
    bad_block = tmp_path / "a.md"
    bad_block.write_text("<!-- if other -->\nx\n<!-- end other -->\n")
    with pytest.raises(ValueError, match="unknown block"):
        dual_llm.render_planner_prompt(**_planner_kwargs(), template_path=bad_block)
    bad_slot = tmp_path / "b.md"
    bad_slot.write_text("{NOT_A_SLOT}\n")
    with pytest.raises(ValueError, match="NOT_A_SLOT"):
        dual_llm.render_planner_prompt(**_planner_kwargs(), template_path=bad_slot)


def _generate(**over):
    planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")])
    coder = MockClient([LLMResponse(text="```cpp\nnamespace TeamA {}\n```", model="mock-coder")])
    kw: dict[str, Any] = dict(
        planner_client=planner,
        coder_client=coder,
        team_letter="A",
        namespace="TeamA",
        opponent_name="opp",
        opponent_source=OPPONENT,
        parent_source=PARENT,
        aar_markdown=AAR,
        prior_lessons=LESSONS,
    )
    kw.update(over)
    return dual_llm.dual_llm_generate(**kw), planner, coder


def test_generate_sends_parent_and_reports_every_prompt():
    seen = []
    result, planner, coder = _generate(on_prompt=lambda role, p: seen.append((role, p)))
    assert PARENT in planner.calls[0] and PARENT in coder.calls[0]
    assert seen == [("planner", planner.calls[0]), ("coder", coder.calls[0])]
    assert result.planner_prompts == planner.calls and result.coder_prompt == coder.calls[0]


def test_generate_with_both_switches_off():
    _, planner, coder = _generate(inherit="none", feedback="none")
    assert PARENT not in planner.calls[0] and PARENT not in coder.calls[0]
    assert AAR not in planner.calls[0] and LESSONS not in planner.calls[0]


def test_generate_rejects_unknown_modes_and_missing_aar():
    with pytest.raises(ValueError, match="inherit"):
        _generate(inherit="maybe")
    with pytest.raises(ValueError, match="feedback"):
        _generate(feedback="maybe")
    with pytest.raises(ValueError, match="aar_markdown"):
        _generate(aar_markdown="")
