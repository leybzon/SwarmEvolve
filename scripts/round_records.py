#!/usr/bin/env python3
"""Per-round records shared by the dual-LLM evolution loops.

``scripts/evolve_coevolve.py`` and ``scripts/evolve_dual.py`` use these to

* record the AAR: one match of the evolving team's champion against the
  current opponent, on a fixed seed per round, played through
  ``fitness.evaluate_fitness`` (the eval container unless the backend is
  overridden) with ``--record --record-actions``, rendered by
  ``telemetry_aar`` from the evolving team's side and saved as ``aar.md``,
  ``aar.json`` and ``aar_trace.jsonl``. It is recorded in every round
  whatever the feedback switch says, so grounding (below) can be measured
  in arms whose planner never sees it;
* save every prompt sent (``planner_prompt.md``, ``planner_prompt_<k>.md``
  for retry ``k``, ``coder_prompt.md``);
* measure parent-child similarity (line and token ``difflib`` ratios);
* measure grounding: every metric value the planner cites (numbers in
  ``key_metrics``, and each ``old_value`` in ``expected_changes``) checked
  against the AAR;
* write the round's ``summary.json``.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import fitness  # noqa: E402
import tactic_spec as ts_mod  # noqa: E402
import telemetry_aar  # noqa: E402

# The AAR match of round r plays seed `seed + AAR_SEED_OFFSET + r`: fixed per
# round, and apart from the evaluation seeds `seed + r * 1000 + [0, n)` for
# fewer than 500 rounds.
AAR_SEED_OFFSET = 500_000

# AAR fields that are fractions; the AAR markdown shows them as percentages.
FRACTION_FIELDS = frozenset(
    {
        "focus_fire_redundancy",
        "cooldown_utilization_us",
        "cooldown_utilization_them",
        "long_range_shot_share_them",
        "hit_rate_us",
        "hit_rate_them",
    }
)

_NUMBER = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)(?!\w)(?!\.\d)(\s*%)?")
_TOKEN = re.compile(r"\w+|[^\w\s]")


def aar_seed(seed: int, round_num: int) -> int:
    """The fixed seed of round ``round_num``'s AAR match."""
    return seed + AAR_SEED_OFFSET + round_num


# ---------------------------------------------------------------------------
# AAR
# ---------------------------------------------------------------------------


def record_aar(
    program_path: Path,
    opponent_path: Path,
    *,
    team: str,
    seed: int,
    round_dir: Path,
) -> telemetry_aar.AARReport:
    """Record and render one AAR match of ``program_path`` playing ``team``.

    Writes ``aar_trace.jsonl``, ``aar.md`` and ``aar.json`` (the structured
    metrics, from which ``aar.md`` is rendered) to ``round_dir``.
    """
    team_a, team_b = (program_path, opponent_path) if team == "A" else (opponent_path, program_path)
    trace = round_dir / "aar_trace.jsonl"
    fitness.evaluate_fitness(
        team_a,
        team_b,
        n_matches=1,
        seed_base=seed,
        under_test=team,
        record_trace_for_match_idx=0,
        trace_output_path=trace,
    )
    report = telemetry_aar.render_aar(trace, perspective=team, fmt="both")
    if not report.markdown.strip():
        raise ValueError(f"empty AAR rendered from {trace}")
    (round_dir / "aar.md").write_text(report.markdown, encoding="utf-8")
    (round_dir / "aar.json").write_text(json.dumps(report.structured, indent=2) + "\n")
    return report


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class PromptSaver:
    """``on_prompt`` callback for ``dual_llm.dual_llm_generate``.

    The planner's first attempt goes to ``planner_prompt.md`` and retry ``k``
    to ``planner_prompt_<k>.md``; the coder's prompt to ``coder_prompt.md``.
    """

    def __init__(self, round_dir: Path):
        self.round_dir = round_dir
        self.planner_attempts = 0

    def __call__(self, role: str, prompt: str) -> None:
        if role == "planner":
            self.planner_attempts += 1
            k = self.planner_attempts
            name = "planner_prompt.md" if k == 1 else f"planner_prompt_{k}.md"
        else:
            name = "coder_prompt.md"
        (self.round_dir / name).write_text(prompt, encoding="utf-8")


# ---------------------------------------------------------------------------
# Journal lessons
# ---------------------------------------------------------------------------

LESSONS_K = 3


def format_lessons(entries: list[dict[str, Any]]) -> str | None:
    """The last ``LESSONS_K`` journal entries as one line each, or ``None``."""
    lines = []
    for e in entries[-LESSONS_K:]:
        fit = e.get("fitness")
        fit_text = f"{fit:.3f}" if isinstance(fit, (int, float)) else "n/a"
        lines.append(
            f"Gen {e.get('generation', '?')}: {e.get('verdict', 'unknown')} "
            f"(fitness={fit_text}) - {e.get('hypothesis_tested', 'unknown')}"
        )
    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def similarity(parent: str, child: str) -> dict[str, Any]:
    """Parent-child similarity: ``difflib`` ratios over lines and tokens.

    Tokens are identifiers/numbers and single punctuation characters. 1.0
    means identical; ``autojunk`` is off so long files are compared in full.
    """
    p_lines, c_lines = parent.splitlines(), child.splitlines()
    p_tok, c_tok = _TOKEN.findall(parent), _TOKEN.findall(child)
    return {
        "line_ratio": round(
            difflib.SequenceMatcher(None, p_lines, c_lines, autojunk=False).ratio(), 4
        ),
        "token_ratio": round(
            difflib.SequenceMatcher(None, p_tok, c_tok, autojunk=False).ratio(), 4
        ),
        "parent_lines": len(p_lines),
        "child_lines": len(c_lines),
        "parent_tokens": len(p_tok),
        "child_tokens": len(c_tok),
    }


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def _aar_values(aar: dict[str, Any]) -> dict[str, float]:
    """Numeric AAR values a planner can cite, including the hit rates the
    markdown derives."""
    values = {
        k: float(v)
        for k, v in aar.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "metrics_schema_version"
    }
    for side in ("us", "them"):
        fired = aar.get(f"shots_fired_{side}", 0)
        values[f"hit_rate_{side}"] = aar.get(f"shots_hit_{side}", 0) / fired if fired else 0.0
    return values


def _matches(text: str, percent: bool, field: str, value: float) -> bool:
    """Does the cited number ``text`` equal ``value`` at its cited precision?

    Fraction fields also match as percentages (31 or 31% for 0.31); a
    number cited with ``%`` only matches as a percentage.
    """
    cited = float(text)
    decimals = len(text.split(".")[1]) if "." in text else 0
    tol = 0.5 * 10**-decimals + 1e-9
    scales = [100.0] if percent else [1.0, 100.0]
    if field not in FRACTION_FIELDS:
        scales = [] if percent else [1.0]
    return any(abs(cited - value * s) <= tol for s in scales)


def _outcome_word(aar: dict[str, Any]) -> str:
    tag = str(aar.get("outcome", ""))
    if tag == "DRAW":
        return "DRAW"
    if tag.startswith("TEAM_") and tag.endswith("_WIN"):
        return "WIN" if tag[5] == str(aar.get("perspective", "")) else "LOSS"
    return tag


def grounding(spec: ts_mod.TacticSpec, aar: dict[str, Any]) -> dict[str, Any]:
    """Check every metric value the planner cites against the AAR.

    * Each number in a ``key_metrics`` line matches if it equals any AAR
      value at the precision it was cited with.
    * Each ``expected_changes[i].old_value`` matches if it equals the AAR
      value of the metric it names (for ``outcome``: WIN, LOSS or DRAW from
      the evolving team's side, or the engine's tag).
    """
    values = _aar_values(aar)
    items: list[dict[str, Any]] = []
    for i, line in enumerate(spec.key_metrics):
        for m in _NUMBER.finditer(line):
            hit = next(
                (f for f, v in values.items() if _matches(m.group(1), bool(m.group(2)), f, v)),
                None,
            )
            items.append(
                {
                    "source": f"key_metrics[{i}]",
                    "cited": m.group(0).strip(),
                    "matched": hit is not None,
                    "aar_field": hit,
                }
            )
    for i, change in enumerate(spec.expected_changes):
        old = change.old_value
        text = str(old).strip()
        if change.metric == "outcome":
            word, tag = _outcome_word(aar), str(aar.get("outcome", ""))
            ok = bool(re.search(rf"\b{word}\b", text.upper())) or tag in text.upper()
        else:
            num = _NUMBER.fullmatch(text) if not isinstance(old, bool) else None
            ok = (
                num is not None
                and change.metric in values
                and _matches(num.group(1), bool(num.group(2)), change.metric, values[change.metric])
            )
        items.append(
            {
                "source": f"expected_changes[{i}].old_value",
                "metric": change.metric,
                "cited": text,
                "matched": ok,
                "aar_field": change.metric if ok else None,
            }
        )
    matched = sum(1 for it in items if it["matched"])
    return {
        "cited": len(items),
        "matched": matched,
        "unmatched": len(items) - matched,
        "share_matched": round(matched / len(items), 4) if items else None,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def write_summary(round_dir: Path, summary: dict[str, Any]) -> None:
    """Write ``summary.json``, listing the files the round left behind."""
    out = dict(summary)
    out["files"] = sorted(p.name for p in round_dir.iterdir() if p.name != "summary.json")
    (round_dir / "summary.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
