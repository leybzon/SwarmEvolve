#!/usr/bin/env python3
"""Dual-LLM architecture: Planner + Coder separation of concerns.

Wraps the single-LLM flow from evolve.py with a two-stage pipeline:

1. **Planner** (e.g., Claude Opus) reads AAR + journal → outputs TacticSpec JSON
2. **Coder** (e.g., Claude Haiku) reads TacticSpec → outputs C++ implementation

This separation forces the planner to articulate concrete tactics and enables
validation of the spec before code generation.
"""

from __future__ import annotations

import json
import logging
import re

# Make sibling scripts importable
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_client  # noqa: E402
import tactic_spec as ts_mod  # noqa: E402

REPO_ROOT = _HERE.parent
PROMPTS = REPO_ROOT / "prompts"
# v3 prompts; prompts/*.md (v1/v2) stay as they were for reference, and the
# legacy arm runs its own frozen copies (legacy/m25/).
PLANNER_TEMPLATE = PROMPTS / "v3" / "planner.md"
CODER_TEMPLATE = PROMPTS / "v3" / "coder.md"
TYPES_HEADER = REPO_ROOT / "src" / "types.h"
ABI_HEADER = REPO_ROOT / "src" / "ai_abi.h"

# Loop switches. inherit: does the prompt carry the evolving team's champion
# (the parent)? feedback: does the planner see the AAR, the journal lessons
# and the champion's numbers?
INHERIT_MODES = ("parent", "none")
FEEDBACK_MODES = ("aar", "none")

_BLOCK = re.compile(r"^<!-- if (\w+) -->\n(.*?)^<!-- end \1 -->\n", re.MULTILINE | re.DOTALL)
_MARKER = re.compile(r"<!-- (?:if|end) \w+ -->")
_SLOT = re.compile(r"\{([A-Z][A-Z_]*)\}")


_LOG = logging.getLogger("swarmevolve.dual_llm")


@dataclass
class DualLLMResponse:
    """Output from the dual-LLM pipeline."""

    # Planner outputs
    tactic_spec: ts_mod.TacticSpec
    planner_response: llm_client.LLMResponse
    planner_retries: int  # How many validation failures before success

    # Coder outputs
    cpp_code: str
    coder_response: llm_client.LLMResponse

    # Combined token counts
    total_prompt_tokens: int
    total_completion_tokens: int

    # Exactly what was sent: one planner prompt per attempt, then the coder's
    planner_prompts: list[str] = field(default_factory=list)
    coder_prompt: str = ""


class DualLLMError(RuntimeError):
    """Raised when dual-LLM pipeline fails after retries."""

    pass


def _fill(template: str, *, blocks: dict[str, bool], values: dict[str, str], name: str) -> str:
    """Keep or drop each ``<!-- if X -->`` block, then fill ``{SLOT}`` placeholders.

    The fill is a single pass over the template, so text inside an inserted
    value (C++ source, the AAR) is never itself treated as a placeholder:
    every value lands in the prompt byte for byte. Unknown blocks, unbalanced
    markers and unfilled slots are errors rather than silently dropped text.
    """

    def keep(m: re.Match[str]) -> str:
        if m.group(1) not in blocks:
            raise ValueError(f"{name}: unknown block {m.group(1)!r}")
        return m.group(2) if blocks[m.group(1)] else ""

    text = _BLOCK.sub(keep, template)
    if _MARKER.search(text):
        raise ValueError(f"{name}: unbalanced block markers")
    missing = sorted({s for s in _SLOT.findall(text) if s not in values})
    if missing:
        raise ValueError(f"{name}: no value for {missing}")
    return _SLOT.sub(lambda m: values[m.group(1)], text)


def render_planner_prompt(
    *,
    team_letter: str,
    opponent_name: str,
    opponent_source: str,
    parent_source: str | None = None,
    aar_markdown: str | None = None,
    prior_lessons: str | None = None,
    champion_fitness: float | None = None,
    last_generation: int | None = None,
    last_hypothesis: str | None = None,
    last_fitness: float | None = None,
    template_path: Path = PLANNER_TEMPLATE,
) -> str:
    """Render the v3 planner prompt.

    ``parent_source`` (the evolving team's champion) fills the parent block;
    ``None`` drops it (inherit = none). ``aar_markdown`` fills the feedback
    blocks (AAR, journal lessons, champion numbers); ``None`` drops them all
    (feedback = none).
    """
    feedback = aar_markdown is not None
    values = {
        "TEAM_LETTER": team_letter,
        "OPPONENT_NAME": opponent_name,
        "OPPONENT_SOURCE": opponent_source,
        "PARENT_CODE": parent_source or "",
        "AAR": aar_markdown or "",
        "PRIOR_LESSONS": prior_lessons if prior_lessons else "(none)",
        "CHAMPION_FITNESS": f"{champion_fitness:.3f}"
        if champion_fitness is not None
        else "N/A (first generation)",
        "LAST_GENERATION": str(last_generation) if last_generation is not None else "N/A",
        "LAST_HYPOTHESIS": last_hypothesis if last_hypothesis else "N/A (first generation)",
        "LAST_FITNESS": f"{last_fitness:.3f}" if last_fitness is not None else "N/A",
    }
    return _fill(
        template_path.read_text(encoding="utf-8"),
        blocks={"parent": parent_source is not None, "feedback": feedback},
        values=values,
        name=template_path.name,
    )


def render_coder_prompt(
    *,
    team_letter: str,
    namespace: str,
    opponent_name: str,
    opponent_source: str,
    tactic_spec_json: str,
    types_header: str,
    abi_header: str,
    parent_source: str | None = None,
    template_path: Path = CODER_TEMPLATE,
) -> str:
    """Render the v3 coder prompt; ``parent_source=None`` drops the parent block."""
    impl = json.loads(tactic_spec_json).get("implementation_guidance", {})
    opponent_team = "B" if team_letter == "A" else "A"
    values = {
        "TEAM_LETTER": team_letter,
        "NAMESPACE": namespace,
        "OPPONENT_NAME": opponent_name,
        "OPPONENT_SOURCE": opponent_source,
        "PARENT_CODE": parent_source or "",
        "TACTIC_SPEC": tactic_spec_json,
        "TYPES_HEADER": types_header,
        "ABI_HEADER": abi_header,
        "MESSAGE_PROTOCOL": impl.get("message_protocol", "unspecified"),
        "MEMORY_LAYOUT": impl.get("memory_layout", "unspecified"),
        "SPECIAL_CASES": impl.get("special_cases", "none"),
        "TEAM_LETTER_LOWER": team_letter.lower(),
        "OPPONENT_TEAM_LETTER_LOWER": opponent_team.lower(),
    }
    return _fill(
        template_path.read_text(encoding="utf-8"),
        blocks={"parent": parent_source is not None},
        values=values,
        name=template_path.name,
    )


def extract_json_from_response(text: str) -> str:
    """Extract first JSON object from LLM response.

    Handles both fenced blocks:
        ```json
        {...}
        ```

    And bare JSON (starts with '{', ends with '}').
    """
    # Try fenced JSON block first
    fence_match = re.search(
        r"```(?:json)?\s*\n(.*?)\n```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        return fence_match.group(1).strip()

    # Fall back to bare JSON (first '{' to matching '}')
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response (no '{')")

    # Find matching closing brace
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("No complete JSON object found (unmatched braces)")


def call_planner(
    client: llm_client.LLMClient,
    prompt: str,
    *,
    max_retries: int = 2,
    max_tokens: int = 4096,
) -> tuple[ts_mod.TacticSpec, llm_client.LLMResponse, int]:
    """Call planner LLM and validate TacticSpec.

    Returns:
        (TacticSpec, LLMResponse, retry_count)

    Raises:
        DualLLMError: if validation fails after max_retries
    """
    for attempt in range(max_retries + 1):
        _LOG.info("planner-attempt n=%d", attempt + 1)

        try:
            response = client.generate(prompt, max_tokens=max_tokens)
            json_text = extract_json_from_response(response.text)
            spec = ts_mod.parse_and_validate_tactic_spec(json_text)

            _LOG.info(
                "planner-success tactic=%s attempts=%d",
                spec.tactic_name,
                attempt + 1,
            )
            return spec, response, attempt

        except (json.JSONDecodeError, ValueError) as e:
            _LOG.warning("planner-parse-failed attempt=%d err=%s", attempt + 1, e)
            if attempt < max_retries:
                # Retry with feedback
                prompt += (
                    f"\n\n---\n\n"
                    f"Your previous response failed parsing: {e}\n"
                    f"Please return valid JSON matching the schema exactly."
                )
                continue
            raise DualLLMError(
                f"Planner failed to return valid JSON after {max_retries + 1} attempts: {e}"
            ) from e

        except ts_mod.TacticSpecValidationError as e:
            _LOG.warning("planner-validation-failed attempt=%d err=%s", attempt + 1, e)
            if attempt < max_retries:
                # Retry with validation feedback
                prompt += (
                    f"\n\n---\n\n"
                    f"Your TacticSpec failed validation:\n{e}\n"
                    f"Please fix the issues and return a valid spec."
                )
                continue
            raise DualLLMError(
                f"Planner TacticSpec validation failed after {max_retries + 1} attempts: {e}"
            ) from e

    # Unreachable but satisfies type checker
    raise DualLLMError("Planner failed (unreachable)")


def call_coder(
    client: llm_client.LLMClient,
    prompt: str,
    *,
    max_tokens: int = 4096,
) -> tuple[str, llm_client.LLMResponse]:
    """Call coder LLM and extract C++ code.

    Returns:
        (cpp_code, LLMResponse)

    Raises:
        DualLLMError: if no C++ fence block found
    """
    _LOG.info("coder-call")

    response = client.generate(prompt, max_tokens=max_tokens)

    # Extract first C++ fenced block
    fence_match = re.search(
        r"```(?:c\+\+|cpp|CPP|C\+\+)?\s*\n(.*?)\n```",
        response.text,
        re.DOTALL,
    )
    if not fence_match:
        raise DualLLMError("Coder response missing C++ fenced block")

    cpp_code = fence_match.group(1)
    _LOG.info("coder-success cpp_lines=%d", len(cpp_code.splitlines()))
    return cpp_code, response


class _Recording:
    """Client wrapper that reports every prompt before it is sent."""

    def __init__(
        self,
        client: llm_client.LLMClient,
        role: str,
        sent: list[str],
        on_prompt: Callable[[str, str], None] | None,
    ):
        self._client = client
        self._role = role
        self._sent = sent
        self._on_prompt = on_prompt
        self.model = client.model

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> llm_client.LLMResponse:
        self._sent.append(prompt)
        if self._on_prompt is not None:
            self._on_prompt(self._role, prompt)
        return self._client.generate(prompt, max_tokens=max_tokens)


def dual_llm_generate(
    *,
    planner_client: llm_client.LLMClient,
    coder_client: llm_client.LLMClient,
    team_letter: str,
    namespace: str,
    opponent_name: str,
    opponent_source: str,
    parent_source: str,
    inherit: str = "parent",
    feedback: str = "aar",
    aar_markdown: str | None = None,
    prior_lessons: str | None = None,
    champion_fitness: float | None = None,
    last_generation: int | None = None,
    last_hypothesis: str | None = None,
    last_fitness: float | None = None,
    planner_max_retries: int = 2,
    planner_max_tokens: int = 4096,
    coder_max_tokens: int = 4096,
    on_prompt: Callable[[str, str], None] | None = None,
) -> DualLLMResponse:
    """Run the full dual-LLM pipeline: planner → validate → coder → code.

    Args:
        planner_client: LLM for tactical analysis (e.g., Opus)
        coder_client: LLM for C++ implementation (e.g., Haiku/Sonnet)
        team_letter: "A" or "B"
        namespace: "TeamA" or "TeamB"
        opponent_name: e.g., "pursuit_v1"
        opponent_source: opponent's C++ source code
        parent_source: the evolving team's current champion C++ source
        inherit: "parent" puts parent_source in both prompts; "none" leaves
            it out
        feedback: "aar" gives the planner aar_markdown, prior_lessons and the
            champion's numbers; "none" leaves all of them out
        aar_markdown: After-Action Report of the champion's recorded match
            (required with feedback="aar")
        prior_lessons: Journal recall from prior generations
        planner_max_retries: How many times to retry planner on validation failure
        planner_max_tokens: Max tokens for planner response
        coder_max_tokens: Max tokens for coder response
        on_prompt: called as on_prompt(role, prompt) just before each model
            call, role "planner" or "coder", so callers can save prompts
            even when a stage later fails

    Returns:
        DualLLMResponse with tactic spec, code, token counts and the prompts sent

    Raises:
        DualLLMError: if either stage fails after retries
        ValueError: for an unknown mode, or feedback="aar" without an AAR
    """
    if inherit not in INHERIT_MODES:
        raise ValueError(f"inherit must be one of {INHERIT_MODES}, got {inherit!r}")
    if feedback not in FEEDBACK_MODES:
        raise ValueError(f"feedback must be one of {FEEDBACK_MODES}, got {feedback!r}")
    if feedback == "aar" and not aar_markdown:
        raise ValueError("feedback='aar' needs a non-empty aar_markdown")
    parent = parent_source if inherit == "parent" else None
    with_feedback = feedback == "aar"

    planner_sent: list[str] = []
    coder_sent: list[str] = []

    # Stage 1: Planner
    planner_prompt = render_planner_prompt(
        team_letter=team_letter,
        opponent_name=opponent_name,
        opponent_source=opponent_source,
        parent_source=parent,
        aar_markdown=aar_markdown if with_feedback else None,
        prior_lessons=prior_lessons if with_feedback else None,
        champion_fitness=champion_fitness if with_feedback else None,
        last_generation=last_generation if with_feedback else None,
        last_hypothesis=last_hypothesis if with_feedback else None,
        last_fitness=last_fitness if with_feedback else None,
    )

    spec, planner_resp, planner_retries = call_planner(
        _Recording(planner_client, "planner", planner_sent, on_prompt),
        planner_prompt,
        max_retries=planner_max_retries,
        max_tokens=planner_max_tokens,
    )

    # Stage 2: Coder
    types_header = TYPES_HEADER.read_text(encoding="utf-8")
    abi_header = ABI_HEADER.read_text(encoding="utf-8")

    coder_prompt = render_coder_prompt(
        team_letter=team_letter,
        namespace=namespace,
        opponent_name=opponent_name,
        opponent_source=opponent_source,
        parent_source=parent,
        tactic_spec_json=json.dumps(spec.to_dict(), indent=2),
        types_header=types_header,
        abi_header=abi_header,
    )

    cpp_code, coder_resp = call_coder(
        _Recording(coder_client, "coder", coder_sent, on_prompt),
        coder_prompt,
        max_tokens=coder_max_tokens,
    )

    # Combine results
    total_prompt_tokens = planner_resp.prompt_tokens + coder_resp.prompt_tokens
    total_completion_tokens = planner_resp.completion_tokens + coder_resp.completion_tokens

    return DualLLMResponse(
        tactic_spec=spec,
        planner_response=planner_resp,
        planner_retries=planner_retries,
        cpp_code=cpp_code,
        coder_response=coder_resp,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        planner_prompts=planner_sent,
        coder_prompt=coder_sent[0],
    )


if __name__ == "__main__":
    # Smoke test with mock clients
    from llm_client import LLMResponse, MockClient

    # Mock planner response (valid TacticSpec JSON)
    mock_planner_json = json.dumps(
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

    planner_mock = MockClient(
        [
            LLMResponse(text=mock_planner_json, model="mock-planner"),
        ]
    )

    # Mock coder response
    coder_mock = MockClient(
        [
            LLMResponse(
                text='```cpp\n#include "../ai_abi.h"\n// mock code\n```',
                model="mock-coder",
            ),
        ]
    )

    try:
        result = dual_llm_generate(
            planner_client=planner_mock,
            coder_client=coder_mock,
            team_letter="A",
            namespace="TeamA",
            opponent_name="test_opponent",
            opponent_source="// opponent code",
            parent_source="// parent code",
            aar_markdown="Outcome: DRAW",
            prior_lessons="None",
        )
        print("✅ Smoke test passed")
        print(f"   Tactic: {result.tactic_spec.tactic_name}")
        print(f"   Planner retries: {result.planner_retries}")
        print(f"   Total tokens: {result.total_prompt_tokens + result.total_completion_tokens}")
    except Exception as e:
        import traceback

        print(f"❌ Smoke test failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
