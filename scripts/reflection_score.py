#!/usr/bin/env python3
"""Reflection Rubric Scorer (M17).

Scores every entry in a SwarmEvolve learning journal on three axes
(causal diagnosis, counter-tactic specificity, ABI feasibility) using
either an LLM-as-judge (default) or a deterministic rule-based fallback.

Inputs
------
* ``journal.jsonl`` — produced by ``scripts/journal.py`` /
  ``scripts/evolve.py`` (M15c/M16). Each line is a JSON entry with at
  least ``generation``, ``track``, ``model``, ``seed``, ``status``,
  ``verdict``, ``hypothesis_tested``, ``tactic_tags``, optional
  ``mechanism_expected`` / ``mechanism_observed`` / ``advice_to_future_self``
  / ``aar_metrics_cited``.
* A judge configuration (``judge_model``, backend). When the LLM judge
  fails (non-JSON output, missing key, parse error after retries) the
  scorer falls back to the rule-based scorer so pipelines never stall.

Outputs
-------
* ``<run_dir>/reflection_scores.csv`` with columns:
  ``generation, track, model, seed, causal_diagnosis,
  counter_tactic_specificity, abi_feasibility, judge_kind,
  judge_model, justification``.
* A calibration command computes Cohen's kappa between a human CSV and
  the judge CSV for a held-out set.

Design
------
* **Rubric is frozen** (``prompts/reflection_rubric.md``) — any change
  bumps the schema version.
* **Rule-based fallback** is deterministic: scores derive from counts
  of AAR metric citations, presence of quantitative advice (numeric
  tokens), and a forbidden-construct deny-list for ABI feasibility.
* **Judge retries**: up to 2 rewrite rounds before falling back.
* **Token accounting**: judge token usage is summed and printed so the
  caller can budget against per-track caps (M20).

CLI
---
::

    reflection_score.py score <journal.jsonl> <out.csv> \\
        [--judge {mock,anthropic,rule}] [--judge-model MODEL]
    reflection_score.py calibrate <human.csv> <judge.csv> [--axis AXIS]

See NEXT_PHASE_PLAN.md §M17 for acceptance criteria (Cohen's κ ≥ 0.5
human vs. judge on the 50-sample calibration set).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

# --- local imports: scripts/ on sys.path ----------------------------------
_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import journal as journal_mod  # noqa: E402
import llm_client  # noqa: E402

# --------------------------------------------------------------------------
# Constants (frozen — bump SCHEMA_VERSION on change)
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1
AXES: tuple[str, str, str] = (
    "causal_diagnosis",
    "counter_tactic_specificity",
    "abi_feasibility",
)
SCORE_MIN = 1
SCORE_MAX = 5
MAX_JUDGE_RETRIES = 2

#: The rubric prompt template, substituted with ENTRY_JSON / GENERATION /
#: MODEL / TRACK / ABI_HEADER at call time.
DEFAULT_RUBRIC_PATH = (_SCRIPTS.parent / "prompts" / "reflection_rubric.md")
DEFAULT_ABI_HEADER_PATH = (_SCRIPTS.parent / "src" / "ai_abi.h")

_NUMERIC_RE = re.compile(r"\b-?\d+(\.\d+)?\b")

_FORBIDDEN_ABI_SUBSTRINGS: tuple[str, ...] = (
    "malloc", "std::vector", "std::string", "std::map", "std::list",
    "std::unordered_map", "std::deque", "std::thread", "std::mutex",
    "std::atomic", "<thread>", "<mutex>", "<atomic>",
    "<fstream>", "<iostream>", "<filesystem>",
    "new ", "delete ", "rand()", "srand", "asm ", "__asm__",
    "popen(", "system(", "execve(",
)

#: Keys in AAR metrics that we treat as legitimate "cited evidence".
#: Matches telemetry_aar's emitted structured metrics (M15b).
_AAR_METRIC_KEYS: frozenset[str] = frozenset({
    "focus_fire_redundancy",
    "mean_pairwise_distance_us",
    "mean_pairwise_distance_them",
    "cooldown_uptime_us",
    "cooldown_uptime_them",
    "cluster_cohesion_us",
    "kill_to_loss_ratio",
    "time_to_first_kill_us",
    "time_to_first_kill_them",
    "win_margin",
})

_LOG = logging.getLogger("swarmevolve.reflection")


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RubricScore:
    """One row of scores for one journal entry."""
    generation: int
    track: str
    model: str
    seed: int
    causal_diagnosis: int
    counter_tactic_specificity: int
    abi_feasibility: int
    judge_kind: str          # "anthropic" | "mock" | "rule"
    judge_model: str
    justification: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_csv_row(self) -> dict[str, Any]:
        row = asdict(self)
        # Tokens go to events/logs, not the CSV (keeps the CSV stable for
        # downstream kappa computation).
        row.pop("prompt_tokens", None)
        row.pop("completion_tokens", None)
        return row

    @classmethod
    def csv_fieldnames(cls) -> list[str]:
        return [
            "generation", "track", "model", "seed",
            "causal_diagnosis", "counter_tactic_specificity",
            "abi_feasibility",
            "judge_kind", "judge_model", "justification",
        ]


class Judge(Protocol):
    """Callable scoring protocol. `score(entry)` returns a RubricScore."""
    kind: str
    model: str

    def score(self, entry: dict[str, Any]) -> RubricScore: ...


# --------------------------------------------------------------------------
# Rule-based judge (deterministic fallback)
# --------------------------------------------------------------------------


def _clamp(x: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(x)))


def _score_causal_diagnosis(entry: dict[str, Any]) -> int:
    """Heuristic:
    1: no mechanism text at all.
    2: mechanism text with < 30 chars, no metrics cited.
    3: mechanism text with >= 30 chars but no AAR metrics cited.
    4: mechanism + 1 AAR metric citation.
    5: mechanism + >= 2 AAR metric citations (observed or expected).
    """
    obs = (entry.get("mechanism_observed") or "").strip()
    exp = (entry.get("mechanism_expected") or "").strip()
    combined = f"{exp}\n{obs}".strip()
    cited = entry.get("aar_metrics_cited") or {}
    cited_keys = {k for k in cited.keys() if k in _AAR_METRIC_KEYS}

    if not combined:
        return 1
    n_cit = len(cited_keys)
    if n_cit >= 2:
        return 5
    if n_cit == 1:
        return 4
    if len(combined) < 30:
        return 2
    return 3


def _has_quantitative(text: str) -> bool:
    return bool(_NUMERIC_RE.search(text))


def _score_counter_tactic(entry: dict[str, Any]) -> int:
    """Heuristic over `advice_to_future_self` + hypothesis:
    1: missing or <= 10 chars.
    2: present, no tactic tags, no numbers.
    3: present, >=1 tactic tag, no numbers.
    4: present, >=1 tactic tag, has a number.
    5: present, >=2 tactic tags, has >=2 numbers (trigger+response).
    """
    advice = (entry.get("advice_to_future_self") or "").strip()
    hyp = (entry.get("hypothesis_tested") or "").strip()
    tags = entry.get("tactic_tags") or []

    if not advice or len(advice) <= 10:
        return 1

    nums = len(_NUMERIC_RE.findall(f"{advice}\n{hyp}"))
    n_tags = len(tags)

    if n_tags >= 2 and nums >= 2:
        return 5
    if n_tags >= 1 and nums >= 1:
        return 4
    if n_tags >= 1:
        return 3
    if nums >= 1:
        return 3
    return 2


def _score_abi_feasibility(entry: dict[str, Any]) -> int:
    """Heuristic over prose fields:
    1: mentions a forbidden construct (heap/STL/thread/IO/RNG/asm).
    2: mentions enemy cooldown (hidden input).
    3: default — prose is feasible but we have no concrete signal.
    4: concrete, references my_memory or named ABI inputs.
    5: concrete + mentions MEM_SIZE/MAX_DRONES + bounded-compute phrasing.
    """
    advice = (entry.get("advice_to_future_self") or "")
    hyp = (entry.get("hypothesis_tested") or "")
    obs = (entry.get("mechanism_observed") or "")
    exp = (entry.get("mechanism_expected") or "")
    full = f"{hyp}\n{exp}\n{obs}\n{advice}"
    lower = full.lower()

    for bad in _FORBIDDEN_ABI_SUBSTRINGS:
        if bad.lower() in lower:
            return 1

    if "enemy cooldown" in lower or "enemies[].cooldown" in lower \
            or "enemies.cooldown" in lower:
        return 2

    signals = 0
    for token in ("my_memory", "allies[", "enemies[",
                  "out_action", "incoming_messages",
                  "params->", "target_id", "message_out"):
        if token in full:
            signals += 1
            break  # one ABI-input reference is enough for level-4 bump

    bounded = any(t in full for t in (
        "MAX_DRONES", "MEM_SIZE", "MSG_SIZE",
        "bounded", "for (int", "compile-time",
    ))

    if signals and bounded:
        return 5
    if signals:
        return 4
    return 3


class RuleJudge:
    """Deterministic scorer. Zero tokens, zero network."""
    kind = "rule"
    model = "rule-v1"

    def score(self, entry: dict[str, Any]) -> RubricScore:
        cd = _clamp(_score_causal_diagnosis(entry))
        ct = _clamp(_score_counter_tactic(entry))
        af = _clamp(_score_abi_feasibility(entry))
        return RubricScore(
            generation=int(entry.get("generation", -1)),
            track=str(entry.get("track", "")),
            model=str(entry.get("model", "")),
            seed=int(entry.get("seed", -1)),
            causal_diagnosis=cd,
            counter_tactic_specificity=ct,
            abi_feasibility=af,
            judge_kind=self.kind,
            judge_model=self.model,
            justification=_rule_justification(cd, ct, af, entry),
            prompt_tokens=0,
            completion_tokens=0,
        )


def _rule_justification(cd: int, ct: int, af: int, entry: dict[str, Any]) -> str:
    cited = len([k for k in (entry.get("aar_metrics_cited") or {})
                 if k in _AAR_METRIC_KEYS])
    tags = len(entry.get("tactic_tags") or [])
    return (
        f"rule: cited={cited} tags={tags} "
        f"cd={cd} ct={ct} af={af}"
    )[:200]


# --------------------------------------------------------------------------
# LLM judge (wraps llm_client.LLMClient)
# --------------------------------------------------------------------------


class JudgeParseError(ValueError):
    """Raised when a judge response cannot be coerced to the 3-axis dict."""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first well-formed JSON object out of `text`. Rejects
    markdown fences and prose wrappers (common LLM habit)."""
    stripped = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences.
    m = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", stripped, re.DOTALL)
    if m:
        stripped = m.group(1).strip()

    # Try direct json.loads first.
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced {...} region.
    start = stripped.find("{")
    if start < 0:
        raise JudgeParseError("no JSON object found in response")
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(stripped[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise JudgeParseError(
                        f"balanced region not JSON: {exc}"
                    ) from exc
                if not isinstance(obj, dict):
                    raise JudgeParseError(
                        "JSON root is not an object"
                    )
                return obj
    raise JudgeParseError("unbalanced braces in response")


def _coerce_axis(obj: dict[str, Any], axis: str) -> int:
    if axis not in obj:
        raise JudgeParseError(f"missing axis: {axis}")
    val = obj[axis]
    try:
        ival = int(val)
    except (TypeError, ValueError) as exc:
        raise JudgeParseError(f"axis {axis!r} not an integer: {val!r}") from exc
    if ival < SCORE_MIN or ival > SCORE_MAX:
        raise JudgeParseError(
            f"axis {axis!r} out of range [{SCORE_MIN},{SCORE_MAX}]: {ival}"
        )
    return ival


def _build_judge_prompt(
    *, entry: dict[str, Any], rubric_template: str, abi_header: str,
) -> str:
    # Minimise context: ship just the reflective fields + tactic tags +
    # cited metrics. The rubric itself mandates grounding in those.
    trimmed_entry = {
        k: v for k, v in entry.items()
        if k in (
            "generation", "verdict", "status",
            "hypothesis_tested", "mechanism_expected",
            "mechanism_observed", "advice_to_future_self",
            "tactic_tags", "aar_metrics_cited",
            "outcome_summary", "fitness", "fitness_delta",
        )
    }
    entry_json = json.dumps(trimmed_entry, indent=2, sort_keys=True)
    return (
        rubric_template
        .replace("{GENERATION}", str(entry.get("generation", "?")))
        .replace("{MODEL}", str(entry.get("model", "?")))
        .replace("{TRACK}", str(entry.get("track", "?")))
        .replace("{ABI_HEADER}", abi_header)
        .replace("{ENTRY_JSON}", entry_json)
    )


class LLMJudge:
    """LLM-as-judge scorer with rewrite retries and deterministic error
    surfaces. Does *not* auto-fallback — callers decide whether to drop
    to a ``RuleJudge`` on repeated failure."""

    def __init__(
        self,
        *,
        client: llm_client.LLMClient,
        rubric_template: str,
        abi_header: str,
        max_tokens: int = 512,
    ):
        self._client = client
        self._rubric = rubric_template
        self._abi_header = abi_header
        self._max_tokens = int(max_tokens)

    @property
    def kind(self) -> str:
        return "anthropic" if isinstance(
            self._client, llm_client.AnthropicClient
        ) else "mock"

    @property
    def model(self) -> str:
        return getattr(self._client, "model", "unknown")

    def score(self, entry: dict[str, Any]) -> RubricScore:
        prompt = _build_judge_prompt(
            entry=entry, rubric_template=self._rubric,
            abi_header=self._abi_header,
        )
        last_err: Exception | None = None
        prompt_tokens = 0
        completion_tokens = 0
        parsed: dict[str, Any] | None = None
        for _attempt in range(MAX_JUDGE_RETRIES + 1):
            try:
                resp = self._client.generate(
                    prompt, max_tokens=self._max_tokens,
                )
            except llm_client.LLMError as exc:
                last_err = exc
                _LOG.warning("judge llm error: %s",
                             llm_client.redact_secrets(str(exc)))
                break
            prompt_tokens += getattr(resp, "prompt_tokens", 0) or 0
            completion_tokens += getattr(resp, "completion_tokens", 0) or 0
            try:
                parsed = _extract_json_object(resp.text)
                # Coerce each axis: any failure triggers a rewrite.
                for ax in AXES:
                    _coerce_axis(parsed, ax)
                break
            except JudgeParseError as exc:
                last_err = exc
                parsed = None
                continue
        if parsed is None:
            raise JudgeParseError(
                f"judge failed after {MAX_JUDGE_RETRIES + 1} attempts: "
                f"{last_err!s}"
            )
        justification = str(parsed.get("justification", ""))[:200]
        return RubricScore(
            generation=int(entry.get("generation", -1)),
            track=str(entry.get("track", "")),
            model=str(entry.get("model", "")),
            seed=int(entry.get("seed", -1)),
            causal_diagnosis=_coerce_axis(parsed, "causal_diagnosis"),
            counter_tactic_specificity=_coerce_axis(
                parsed, "counter_tactic_specificity"),
            abi_feasibility=_coerce_axis(parsed, "abi_feasibility"),
            judge_kind=self.kind,
            judge_model=self.model,
            justification=justification,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


# --------------------------------------------------------------------------
# Score-many + fallback composition
# --------------------------------------------------------------------------


def _safe_score(judge: Judge, entry: dict[str, Any],
                fallback: Judge | None) -> RubricScore:
    try:
        return judge.score(entry)
    except JudgeParseError as exc:
        if fallback is None:
            raise
        _LOG.warning(
            "judge fell back to rule scorer for gen=%s: %s",
            entry.get("generation"), exc,
        )
        return fallback.score(entry)
    except llm_client.LLMError as exc:
        if fallback is None:
            raise
        _LOG.warning(
            "llm error → rule fallback for gen=%s: %s",
            entry.get("generation"),
            llm_client.redact_secrets(str(exc)),
        )
        return fallback.score(entry)


def score_journal(
    *,
    entries: Iterable[dict[str, Any]],
    judge: Judge,
    fallback: Judge | None = None,
) -> list[RubricScore]:
    """Score every entry. If `fallback` is set, judge failures are
    swallowed and replaced with the fallback's score (with `judge_kind`
    reflecting the fallback)."""
    results: list[RubricScore] = []
    for entry in entries:
        results.append(_safe_score(judge, entry, fallback))
    return results


def write_csv(scores: Iterable[RubricScore], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = RubricScore.csv_fieldnames()
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for s in scores:
            writer.writerow(s.as_csv_row())
    tmp.replace(out_path)


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


# --------------------------------------------------------------------------
# Cohen's kappa (weighted linear) — used by the calibration sub-command
# --------------------------------------------------------------------------


def cohens_kappa(a: list[int], b: list[int], *, weights: str = "linear") -> float:
    """Weighted Cohen's kappa over a 1..K ordinal scale.

    - ``linear``: (i-j)/(K-1) disagreement weight.
    - ``unweighted``: exact-match only (standard kappa).

    Implementation is self-contained (no scipy); validated against
    known pairs in tests.
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("empty vectors")
    categories = sorted(set(a) | set(b))
    k = len(categories)
    if k < 2:
        # Degenerate: everyone agrees on one value → kappa undefined but
        # conventionally 1.0 if a == b, else 0.0. Chose 1.0 only if
        # every pair matches.
        return 1.0 if all(x == y for x, y in zip(a, b)) else 0.0
    idx = {c: i for i, c in enumerate(categories)}
    n = len(a)
    observed = [[0] * k for _ in range(k)]
    row = [0] * k
    col = [0] * k
    for x, y in zip(a, b):
        i, j = idx[x], idx[y]
        observed[i][j] += 1
        row[i] += 1
        col[j] += 1
    kmax = k - 1
    if weights == "linear":
        def w(i: int, j: int) -> float:
            return abs(i - j) / kmax if kmax else 0.0
    elif weights == "unweighted":
        def w(i: int, j: int) -> float:
            return 0.0 if i == j else 1.0
    else:
        raise ValueError(f"unknown weights scheme: {weights!r}")
    num = 0.0
    den = 0.0
    for i in range(k):
        for j in range(k):
            wij = w(i, j)
            num += wij * observed[i][j]
            den += wij * (row[i] * col[j]) / n
    if den == 0.0:
        # Everyone picked the same category and they all agreed.
        return 1.0 if num == 0.0 else 0.0
    return 1.0 - num / den


def calibrate_csv_pair(
    human_path: Path, judge_path: Path, *, axis: str | None = None,
) -> dict[str, float]:
    """Compute per-axis (or single-axis) weighted kappa between two
    score CSVs. Rows are matched by ``(track, model, seed, generation)``.
    Raises if there are fewer than 5 matched rows.
    """
    human_rows = read_csv(human_path)
    judge_rows = read_csv(judge_path)

    def key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (row["track"], row["model"], row["seed"], row["generation"])

    human_idx = {key(r): r for r in human_rows}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for r in judge_rows:
        k = key(r)
        if k in human_idx:
            pairs.append((human_idx[k], r))
    if len(pairs) < 5:
        raise ValueError(
            f"only {len(pairs)} matched rows; need >= 5 for meaningful kappa"
        )
    axes = (axis,) if axis else AXES
    out: dict[str, float] = {}
    for ax in axes:
        a = [int(h[ax]) for h, _ in pairs]
        b = [int(j[ax]) for _, j in pairs]
        out[ax] = cohens_kappa(a, b, weights="linear")
    out["_n_pairs"] = float(len(pairs))
    return out


# --------------------------------------------------------------------------
# I/O helpers
# --------------------------------------------------------------------------


def load_rubric_template(path: Path | None = None) -> str:
    p = path or DEFAULT_RUBRIC_PATH
    return p.read_text(encoding="utf-8")


def load_abi_header(path: Path | None = None) -> str:
    p = path or DEFAULT_ABI_HEADER_PATH
    if not p.is_file():
        return "/* ai_abi.h not available at score time */"
    return p.read_text(encoding="utf-8")


def load_journal(path: Path) -> list[dict[str, Any]]:
    # journal.read_entries already tolerates trailing partial lines.
    return journal_mod.read_entries(path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_judge(
    kind: str, *, judge_model: str | None,
    rubric_template: str, abi_header: str,
    mock_response_paths: list[Path] | None,
) -> tuple[Judge, Judge | None]:
    if kind == "rule":
        return RuleJudge(), None
    if kind == "mock":
        responses: list[llm_client.LLMResponse] = []
        for mp in mock_response_paths or []:
            responses.append(llm_client.LLMResponse(
                text=mp.read_text(encoding="utf-8"),
                model=judge_model or "mock-judge",
            ))
        client = llm_client.MockClient(
            responses=responses,
            model=judge_model or "mock-judge",
        )
        judge = LLMJudge(
            client=client, rubric_template=rubric_template,
            abi_header=abi_header,
        )
        return judge, RuleJudge()
    if kind == "anthropic":
        client = llm_client.AnthropicClient(model=judge_model)
        judge = LLMJudge(
            client=client, rubric_template=rubric_template,
            abi_header=abi_header,
        )
        return judge, RuleJudge()
    raise ValueError(f"unknown judge kind: {kind!r}")


def _cmd_score(args: argparse.Namespace) -> int:
    journal_path = Path(args.journal).resolve()
    out_path = Path(args.out).resolve()
    rubric_template = load_rubric_template(
        Path(args.rubric_path) if args.rubric_path else None
    )
    abi_header = load_abi_header(
        Path(args.abi_path) if args.abi_path else None
    )

    mock_paths: list[Path] | None = None
    if args.mock_response_dir:
        mr = Path(args.mock_response_dir).resolve()
        mock_paths = sorted(mr.glob("*.md")) + sorted(mr.glob("*.txt"))
        if not mock_paths:
            _LOG.error("mock response dir %s empty", mr)
            return 2

    judge, fallback = _build_judge(
        args.judge, judge_model=args.judge_model,
        rubric_template=rubric_template, abi_header=abi_header,
        mock_response_paths=mock_paths,
    )

    entries = load_journal(journal_path)
    if not entries:
        _LOG.error("no journal entries in %s", journal_path)
        return 3

    scores = score_journal(entries=entries, judge=judge, fallback=fallback)
    write_csv(scores, out_path)

    # Summary line for callers/CI.
    total_prompt = sum(s.prompt_tokens for s in scores)
    total_completion = sum(s.completion_tokens for s in scores)
    by_axis = {
        ax: Counter(getattr(s, ax) for s in scores) for ax in AXES
    }
    print(
        f"reflection_score: wrote {len(scores)} rows to {out_path} "
        f"judge={judge.kind} model={judge.model} "
        f"tokens=({total_prompt},{total_completion})"
    )
    for ax, counts in by_axis.items():
        dist = " ".join(f"{k}:{counts[k]}" for k in sorted(counts))
        print(f"  {ax}: {dist}")
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    """Select a deterministic calibration sample from one or more journals.

    The sample is stratified by ``(status, verdict)`` and capped at ``--size``
    rows total. The ordering is a stable sort on
    ``(status, verdict, track, model, seed, generation)`` so the selection
    is reproducible across machines. Emits:

    * ``<out_dir>/sample.csv`` — human-scoring template with blank axis
      columns ready to fill in.
    * ``<out_dir>/entries/<row_id>.json`` — full journal entry for each
      sampled row, so the human scorer has complete context.
    """
    import random

    paths = [Path(p).resolve() for p in args.journal]
    all_entries: list[dict[str, Any]] = []
    for p in paths:
        if not p.is_file():
            _LOG.error("journal not found: %s", p)
            return 2
        all_entries.extend(load_journal(p))
    if not all_entries:
        _LOG.error("no entries loaded from %s", paths)
        return 3

    # Stratify by (status, verdict).
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in all_entries:
        key = (str(e.get("status", "")), str(e.get("verdict", "")))
        buckets.setdefault(key, []).append(e)

    # Deterministic order within each bucket.
    def order_key(e: dict[str, Any]) -> tuple[str, str, int, int]:
        return (
            str(e.get("track", "")),
            str(e.get("model", "")),
            int(e.get("seed", 0) or 0),
            int(e.get("generation", 0) or 0),
        )
    for b in buckets.values():
        b.sort(key=order_key)

    # Round-robin across buckets to reach --size rows.
    rng = random.Random(args.seed)
    ordered_buckets = sorted(buckets.items(),
                             key=lambda kv: (kv[0][0], kv[0][1]))
    selected: list[dict[str, Any]] = []
    cursors = {k: 0 for k, _ in ordered_buckets}
    while len(selected) < args.size:
        progressed = False
        for key, items in ordered_buckets:
            if cursors[key] < len(items):
                selected.append(items[cursors[key]])
                cursors[key] += 1
                progressed = True
                if len(selected) >= args.size:
                    break
        if not progressed:
            break

    # Emit the template CSV + per-row entry sidecars.
    out_dir = Path(args.out_dir).resolve()
    entries_dir = out_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "sample.csv"
    fieldnames = RubricScore.csv_fieldnames()
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for e in selected:
            row_id = (
                f"{e.get('track','?')}-{e.get('model','?')}-"
                f"{e.get('seed','?')}-{e.get('generation','?')}"
            )
            (entries_dir / f"{row_id}.json").write_text(
                json.dumps(e, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            w.writerow({
                "generation": e.get("generation", ""),
                "track": e.get("track", ""),
                "model": e.get("model", ""),
                "seed": e.get("seed", ""),
                "causal_diagnosis": "",  # human fills these in
                "counter_tactic_specificity": "",
                "abi_feasibility": "",
                "judge_kind": "human",
                "judge_model": args.human_label,
                "justification": "",
            })

    # Quiet noise for CI by default; verbose listing on --verbose.
    print(
        f"reflection_score: wrote {len(selected)}/{args.size} sample rows "
        f"to {csv_path}; per-row entries in {entries_dir}"
    )
    _ = rng  # seed recorded for reproducibility even though we sort deterministically
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    human_path = Path(args.human).resolve()
    judge_path = Path(args.judge).resolve()
    kappas = calibrate_csv_pair(
        human_path, judge_path, axis=args.axis,
    )
    n = int(kappas.pop("_n_pairs"))
    min_kappa = min(kappas.values())
    print(f"n_pairs={n}")
    for ax, k in sorted(kappas.items()):
        flag = "OK" if k >= 0.5 else "LOW"
        print(f"  {ax}: kappa={k:.3f}  [{flag}]")
    return 0 if min_kappa >= 0.5 else 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SwarmEvolve M17 reflection rubric scorer.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="score a journal.jsonl → CSV")
    s.add_argument("journal", help="path to journal.jsonl")
    s.add_argument("out", help="path to output CSV")
    s.add_argument("--judge", choices=("rule", "mock", "anthropic"),
                   default="rule")
    s.add_argument("--judge-model", default=None,
                   help="override judge model id")
    s.add_argument("--rubric-path", default=None,
                   help="override rubric template path")
    s.add_argument("--abi-path", default=None,
                   help="override ai_abi.h header path")
    s.add_argument("--mock-response-dir", default=None,
                   help="directory of judge responses for --judge mock")
    s.set_defaults(func=_cmd_score)

    p_sample = sub.add_parser(
        "sample",
        help=(
            "select a deterministic calibration sample from one or more "
            "journal.jsonl files and emit a human-scoring template"
        ),
    )
    p_sample.add_argument("--out-dir", required=True,
                           help="directory to write sample.csv + entries/")
    p_sample.add_argument("--size", type=int, default=50,
                           help="maximum number of sampled rows")
    p_sample.add_argument("--seed", type=int, default=0,
                           help="random seed (reserved for tie-breaking)")
    p_sample.add_argument("--human-label", default="human-v1",
                           help="value for judge_model in the template CSV")
    p_sample.add_argument("journal", nargs="+",
                           help="one or more journal.jsonl files")
    p_sample.set_defaults(func=_cmd_sample)

    c = sub.add_parser("calibrate",
                       help="Cohen's kappa between two score CSVs")
    c.add_argument("human", help="CSV with human ground-truth scores")
    c.add_argument("judge", help="CSV written by `score`")
    c.add_argument("--axis", default=None,
                   help="compute kappa for one axis only")
    c.set_defaults(func=_cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "SCHEMA_VERSION", "AXES", "SCORE_MIN", "SCORE_MAX",
    "MAX_JUDGE_RETRIES",
    "RubricScore", "RuleJudge", "LLMJudge",
    "Judge", "JudgeParseError",
    "score_journal", "write_csv", "read_csv",
    "cohens_kappa", "calibrate_csv_pair",
    "load_rubric_template", "load_abi_header", "load_journal",
    "build_parser", "main",
]
