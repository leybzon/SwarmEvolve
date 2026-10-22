"""SwarmEvolve learning journal (M15c).

A per-lineage, append-only record of what an LLM authored each
generation: what it tried, what worked, what didn't.  The journal's
principal defence against self-flattering hallucinations is that
every entry is validated against the M15b AAR before it is written
— any metric citation that disagrees with the AAR beyond 1 %
relative tolerance is rejected.

Storage layout (per NEXT_PHASE_PLAN.md §3.X.1):

    data/runs/<track>/<model>/<seed>/journal.jsonl

All file I/O is deterministic:

* ``append_entry`` validates + canonicalises then writes a single
  ``json.dumps(..., sort_keys=True)`` line followed by ``\\n``.  It
  fsyncs the file before returning so a kill -9 cannot produce a
  half-written trailing line.
* ``read_entries`` tolerates a trailing partial line (truncated by a
  mid-write crash) and reports it on ``ValidationResult.notes`` but
  never raises.
* ``recall`` is embedding-free and fully deterministic: identical
  history → identical byte output (tested in test_journal.py).
* ``render_for_prompt`` emits a token-capped Markdown block for
  injection into the next-generation prompt.

Public API:

    append_entry(path, entry, aar) -> ValidationResult
    read_entries(path) -> list[dict]
    recall(path, *, recency_k=3, extremes_k=3, tag_overlap=0.3,
           max_entries=10, max_bytes=3000,
           planned_tags=None) -> list[dict]
    render_for_prompt(entries, *, max_tokens=1500) -> str
    canonicalise_entry(entry) -> dict          # test helper
    validate_against_aar(entry, aar) -> ValidationResult

This module has no import-time side effects; ``main()`` is a thin
CLI used by tests and the M16 wiring.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency, exercised in CI.
    from jsonschema import Draft7Validator
except Exception:  # pragma: no cover
    Draft7Validator = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "journal_schema.json"

JOURNAL_SCHEMA_VERSION = 1
METRIC_REL_TOLERANCE = 0.01  # 1% per §3.X.3 metric grounding rule
MAX_REWRITES = 2  # orchestrator rewrite budget cap (§3.X.3)

# Controlled vocabulary normaliser: lowercase, collapse non-alphanumerics
# to underscore, strip leading/trailing underscores.
_TAG_NORMALISER = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Validation result (returned by append_entry)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating / appending a single journal entry."""

    ok: bool
    schema_valid: bool
    metrics_match_aar: bool
    rewrites: int
    errors: tuple[str, ...] = field(default_factory=tuple)
    written: bool = False

    @property
    def summary(self) -> str:
        if self.ok:
            return "ok"
        return "; ".join(self.errors) or "invalid"


# ---------------------------------------------------------------------------
# Schema loading (cached)
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads(SCHEMA_PATH.read_text())
    return _SCHEMA_CACHE


def _schema_errors(entry: dict[str, Any]) -> list[str]:
    if Draft7Validator is None:  # pragma: no cover - test env always has it
        return ["jsonschema not available"]
    validator = Draft7Validator(_load_schema())
    errs = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errs]


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def _canonicalise_tag(tag: str) -> str:
    """Lowercase + snake_case; collapse runs of non-alnum to a single `_`."""
    t = _TAG_NORMALISER.sub("_", tag.strip().lower()).strip("_")
    return t


def canonicalise_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a *new* dict with tags canonicalised and defaults filled in.

    Never mutates the input. The canonicalisation rules mirror
    NEXT_PHASE_PLAN.md §3.X.5 (fairness across models):

    * ``tactic_tags`` lowercased, non-alnum → ``_``, deduplicated while
      preserving first-seen order.
    * ``schema_version`` defaulted to :data:`JOURNAL_SCHEMA_VERSION`.
    """
    out = dict(entry)
    out.setdefault("schema_version", JOURNAL_SCHEMA_VERSION)
    tags_in = out.get("tactic_tags") or []
    seen: set[str] = set()
    tags_out: list[str] = []
    for t in tags_in:
        if not isinstance(t, str):
            continue
        ct = _canonicalise_tag(t)
        if ct and ct not in seen:
            seen.add(ct)
            tags_out.append(ct)
    out["tactic_tags"] = tags_out
    return out


# ---------------------------------------------------------------------------
# Reasoning depth validation (M21 enhancement)
# ---------------------------------------------------------------------------

# Banned phrases that indicate low-quality reflection
BANNED_PHRASES = [
    "try a different mechanism",
    "try different",
    "different approach",
    "improve targeting",
    "improve formation",
    "improve coordination",
    "better tactics",
    "more effective",
]

# Generic-only tags that should not be the only tags present
GENERIC_TAGS = {"accept_if_better", "status_quo", "no_change"}


def _validate_reasoning_depth(entry: dict[str, Any]) -> list[str]:
    """Validate reasoning depth heuristics (M21 enhancement).

    Returns list of error messages (empty if all checks pass).
    """
    errors: list[str] = []

    # Check 1: hypothesis_tested must be >= 10 words
    hypothesis = entry.get("hypothesis_tested", "")
    if isinstance(hypothesis, str):
        word_count = len(hypothesis.split())
        if word_count < 10:
            errors.append(f"hypothesis_tested too short: {word_count} words (need >=10)")

    # Check 2: mechanism_observed must cite at least 1 metric
    mechanism = entry.get("mechanism_observed", "")
    cited_metrics = entry.get("aar_metrics_cited", {})
    if isinstance(mechanism, str) and cited_metrics:
        # Check if any metric key appears in mechanism
        has_metric_citation = any(
            key.replace("_", " ") in mechanism.lower() or key in mechanism for key in cited_metrics
        )
        if not has_metric_citation:
            errors.append("mechanism_observed does not cite any AAR metric")

    # Check 3: advice_to_future_self cannot match banned phrases
    advice = entry.get("advice_to_future_self", "")
    if isinstance(advice, str):
        advice_lower = advice.lower()

        # Special case: "carry forward" allowed only if win rate >= 0.8
        if "carry forward" in advice_lower:
            outcome_summary = entry.get("outcome_summary", "")
            # Parse outcome_summary like "accepted: a=8 b=1 draws=1 invalid=0"
            if "a=" in outcome_summary:
                import re

                match = re.search(r"a=(\d+)\s+b=(\d+)\s+draws=(\d+)", outcome_summary)
                if match:
                    wins = int(match.group(1))
                    losses = int(match.group(2))
                    total = wins + losses + int(match.group(3))
                    win_rate = wins / total if total > 0 else 0.0
                    if win_rate < 0.8:
                        errors.append(
                            f"'carry forward' not allowed with win_rate={win_rate:.2f} < 0.8"
                        )

        # Check other banned phrases
        for phrase in BANNED_PHRASES:
            if phrase in advice_lower:
                errors.append(f"banned phrase in advice: '{phrase}'")
                break  # Only report first match

    # Check 4: tactic_tags must have >= 2 tags, at least one non-generic
    tags = entry.get("tactic_tags", [])
    if isinstance(tags, list):
        if len(tags) < 2:
            errors.append(f"tactic_tags must have >=2 tags, got {len(tags)}")
        else:
            non_generic = [t for t in tags if t not in GENERIC_TAGS]
            if not non_generic:
                errors.append(f"tactic_tags must have >=1 non-generic tag (got only {tags})")

    return errors


# ---------------------------------------------------------------------------
# Metric grounding
# ---------------------------------------------------------------------------


def _metric_matches(cited: Any, truth: Any) -> bool:
    """True iff ``cited`` matches the AAR ``truth`` within tolerance.

    * Numbers: within 1 % relative tolerance OR within 1e-4 absolute
      for values near zero.
    * Strings / booleans / None: exact equality.
    """
    if isinstance(cited, bool) or isinstance(truth, bool):
        return cited == truth
    if cited is None or truth is None:
        return cited == truth
    if isinstance(cited, (int, float)) and isinstance(truth, (int, float)):
        if truth == 0.0:
            return abs(cited) <= 1e-4
        return abs(cited - truth) <= max(abs(truth) * METRIC_REL_TOLERANCE, 1e-4)
    return cited == truth


def validate_against_aar(
    entry: dict[str, Any],
    aar: dict[str, Any] | None,
    *,
    strict_reflection: bool = False,
) -> ValidationResult:
    """Validate a *canonicalised* entry against schema + AAR.

    ``aar`` is the structured-metrics dict from M15b's
    ``compute_metrics``. Passing ``aar=None`` skips the grounding
    check — used for stall generations that had no match to analyse.

    If ``strict_reflection=True``, also validates reasoning depth
    heuristics (M21 enhancement): hypothesis length, metric citations,
    banned phrases, tactic tag quality.
    """
    errors: list[str] = []

    schema_errs = _schema_errors(entry)
    if schema_errs:
        errors.extend(f"schema: {e}" for e in schema_errs)

    metrics_ok = True
    if aar is not None:
        cited = entry.get("aar_metrics_cited") or {}
        for key, value in cited.items():
            if key not in aar:
                metrics_ok = False
                errors.append(f"aar: cited key '{key}' not in AAR")
                continue
            if not _metric_matches(value, aar[key]):
                metrics_ok = False
                errors.append(f"aar: '{key}' cited={value!r} vs aar={aar[key]!r}")

    # M21: Optional reasoning depth validation
    if strict_reflection:
        depth_errs = _validate_reasoning_depth(entry)
        if depth_errs:
            errors.extend(f"reasoning: {e}" for e in depth_errs)

    # Stall entries must have null fitness + delta (schema enforces)
    return ValidationResult(
        ok=not errors,
        schema_valid=not schema_errs,
        metrics_match_aar=metrics_ok,
        rewrites=int(entry.get("validation", {}).get("rewrites", 0)),
        errors=tuple(errors),
        written=False,
    )


# ---------------------------------------------------------------------------
# Append path
# ---------------------------------------------------------------------------


def _write_line(path: Path, entry: dict[str, Any]) -> None:
    """Append a single canonical JSON line and fsync before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
    # Open + append + fsync atomically-ish. We don't need cross-process
    # locking here: the orchestrator is the only writer per-lineage.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def append_entry(
    path: Path | str,
    entry: dict[str, Any],
    aar: dict[str, Any] | None,
    *,
    allow_fallback: bool = True,
) -> ValidationResult:
    """Canonicalise, validate, then append ``entry`` to ``path``.

    If validation passes, the entry is written verbatim (after
    canonicalisation) and ``result.written`` is True.

    If validation fails and ``allow_fallback`` is set and the entry has
    already consumed its rewrite budget (``validation.rewrites >= 2``),
    a fallback entry is written with ``validation.metrics_match_aar:
    false`` and ``notes`` summarising the failure. This matches
    §3.X.3's "after 2 failures, the entry is written ..." rule.

    Otherwise the caller receives a ``ValidationResult`` with
    ``written=False`` and should prompt the LLM to rewrite.
    """
    path = Path(path)
    canon = canonicalise_entry(entry)
    result = validate_against_aar(canon, aar)

    if result.ok:
        _write_line(path, canon)
        return ValidationResult(
            ok=True,
            schema_valid=True,
            metrics_match_aar=True,
            rewrites=result.rewrites,
            errors=(),
            written=True,
        )

    # Rewrite budget exhausted and fallback permitted -> write a
    # quarantined entry so the lineage record stays complete.
    rewrites_used = int(canon.get("validation", {}).get("rewrites", 0))
    if allow_fallback and rewrites_used >= MAX_REWRITES:
        fallback = _build_fallback(canon, result.errors)
        _write_line(path, fallback)
        return ValidationResult(
            ok=False,
            schema_valid=False,  # original was invalid; fallback was written
            metrics_match_aar=False,
            rewrites=rewrites_used,
            errors=result.errors,
            written=True,
        )

    return result


def _build_fallback(canon: dict[str, Any], errors: Sequence[str]) -> dict[str, Any]:
    """Produce a deterministic fallback entry after 2 failed rewrites.

    We preserve the original bookkeeping fields (generation, timestamp,
    track, model, seed, status, fitness, fitness_delta) and zero out
    the prose fields so the lineage record cannot silently assimilate
    unverified LLM claims.
    """
    # Start from only the fields we trust structurally.
    passthrough_keys = (
        "schema_version",
        "generation",
        "timestamp_utc",
        "parent_generation",
        "track",
        "model",
        "seed",
        "status",
        "fitness",
        "fitness_delta",
    )
    fb: dict[str, Any] = {k: canon.get(k) for k in passthrough_keys if k in canon}
    fb.setdefault("schema_version", JOURNAL_SCHEMA_VERSION)
    status = fb.get("status", "ok")
    # If the author tried to claim a non-stall verdict we rewrite to
    # "rejected" (status==ok) or "stalled" (otherwise).
    fb["verdict"] = "rejected" if status == "ok" else "stalled"
    fb["hypothesis_tested"] = "(validation failed; fallback entry)"
    fb["tactic_tags"] = []
    fb["aar_metrics_cited"] = {}
    fb["outcome_summary"] = ""
    fb["advice_to_future_self"] = ""
    fb["validation"] = {
        "schema_valid": False,
        "metrics_match_aar": False,
        "rewrites": MAX_REWRITES,
        "notes": ("fallback after validation failure: " + "; ".join(errors))[:400],
    }
    # Stall entries must have null fitness; non-stall retained.
    if status != "ok":
        fb["fitness"] = None
        fb["fitness_delta"] = None
    return fb


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def read_entries(path: Path | str) -> list[dict[str, Any]]:
    """Return journal entries in file order. Tolerates a partial last line.

    A corrupted trailing line (e.g. kill -9 during write) is silently
    dropped — §6.1 mandates that "a corrupted last line must not
    poison the journal". All other decode errors are raised.
    """
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for i, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(raw) - 1:
                # Trailing partial line after crash — drop it silently.
                break
            raise
    return out


# ---------------------------------------------------------------------------
# Recall (deterministic, embedding-free retrieval)
# ---------------------------------------------------------------------------


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def recall(
    path: Path | str,
    *,
    recency_k: int = 3,
    extremes_k: int = 3,
    tag_overlap: float = 0.3,
    max_entries: int = 10,
    max_bytes: int = 3000,
    planned_tags: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic retrieval per NEXT_PHASE_PLAN.md §3.X.4.

    The returned list preserves *generation* order (oldest first) so
    callers can render it as a chronology.
    """
    entries = read_entries(path)
    if not entries:
        return []

    # Stable primary sort by generation number for predictable output.
    entries = sorted(entries, key=lambda e: e.get("generation", 0))

    # Selection sets keyed by "generation" to dedupe across strategies.
    selected: dict[int, dict[str, Any]] = {}

    # 1. Recency.
    for e in entries[-recency_k:]:
        selected[int(e["generation"])] = e

    # 2. Extremes by |fitness_delta| (skip nulls).
    with_delta = [e for e in entries if isinstance(e.get("fitness_delta"), (int, float))]
    with_delta.sort(
        key=lambda e: (abs(float(e["fitness_delta"])), int(e["generation"])),
        reverse=True,
    )
    for e in with_delta[:extremes_k]:
        selected[int(e["generation"])] = e

    # 3. Tag overlap with the planned direction for generation N.
    if planned_tags:
        planned_canon = [_canonicalise_tag(t) for t in planned_tags if t]
        for e in entries:
            if _jaccard(planned_canon, e.get("tactic_tags") or []) >= tag_overlap:
                selected[int(e["generation"])] = e

    # 4. Stall inclusion: at least one if any exist.
    stalls = [e for e in entries if e.get("verdict") == "stalled"]
    if stalls and not any(s.get("verdict") == "stalled" for s in selected.values()):
        # Choose the most recent stall deterministically.
        s = stalls[-1]
        selected[int(s["generation"])] = s

    # 5. Cap by max_entries and max_bytes, dropping non-extreme non-stall
    #    non-recent entries first (i.e. tag-overlap additions) by age.
    # Compute a protection score per entry: higher = keep longer.
    recent_gens = {int(e["generation"]) for e in entries[-recency_k:]}
    extreme_gens = {int(e["generation"]) for e in with_delta[:extremes_k]}
    stall_gens = {int(e["generation"]) for e in stalls}

    def _protection(e: dict[str, Any]) -> tuple[int, int]:
        gen = int(e["generation"])
        score = 0
        if gen in recent_gens:
            score += 4
        if gen in extreme_gens:
            score += 2
        if gen in stall_gens:
            score += 1
        return (score, gen)  # ties broken by recency

    ordered = sorted(selected.values(), key=_protection, reverse=True)
    kept: list[dict[str, Any]] = []
    running_bytes = 0
    for e in ordered:
        line = json.dumps(e, sort_keys=True, ensure_ascii=False)
        if len(kept) >= max_entries:
            break
        if running_bytes + len(line) + 1 > max_bytes and kept:
            break
        kept.append(e)
        running_bytes += len(line) + 1

    # Final output: chronological (oldest -> newest).
    kept.sort(key=lambda e: int(e["generation"]))
    return kept


# ---------------------------------------------------------------------------
# Render path
# ---------------------------------------------------------------------------


def render_for_prompt(
    entries: Sequence[dict[str, Any]],
    *,
    max_tokens: int = 1500,
) -> str:
    """Render recalled entries into a prompt-ready Markdown block.

    Uses the same 4-chars-per-token heuristic as the AAR (see
    ``telemetry_aar.estimate_tokens``) to enforce the cap. Structured
    fields come first per §3.X.5 (fairness mitigations); prose advice
    follows last-and-least.
    """
    if not entries:
        return "## Prior lessons\n\n(none — first generation in this lineage.)\n"
    chunks: list[str] = ["## Prior lessons (recalled journal)\n"]
    for e in entries:
        gen = e.get("generation", "?")
        verdict = e.get("verdict", "?")
        fd = e.get("fitness_delta")
        fd_s = "n/a" if fd is None else f"{fd:+.3f}"
        tags = e.get("tactic_tags") or []
        tag_s = ", ".join(tags) if tags else "(none)"
        hypo = (e.get("hypothesis_tested") or "").strip()
        advice = (e.get("advice_to_future_self") or "").strip()
        observed = (e.get("mechanism_observed") or "").strip()
        cited = e.get("aar_metrics_cited") or {}
        cited_s = ", ".join(f"{k}={v}" for k, v in sorted(cited.items())) or "(none)"
        block = (
            f"### gen {gen} — verdict: {verdict} (Δfitness={fd_s})\n"
            f"- tags: {tag_s}\n"
            f"- cited: {cited_s}\n"
            f"- hypothesis: {hypo}\n"
            f"- observed: {observed}\n"
            f"- advice: {advice}\n"
        )
        chunks.append(block)
    rendered = "\n".join(chunks).rstrip() + "\n"
    # Token cap (4 chars/token heuristic).
    max_chars = max_tokens * 4
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars].rstrip() + "\n...(truncated)\n"
    return rendered


# ---------------------------------------------------------------------------
# CLI (thin wrapper for manual use + M16 wiring)
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Append/recall learning-journal entries.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_app = sub.add_parser("append", help="Append one JSON entry read from stdin.")
    p_app.add_argument("--path", required=True, type=Path)
    p_app.add_argument(
        "--aar",
        type=Path,
        default=None,
        help="Path to AAR JSON (M15b sidecar). Omit to skip grounding.",
    )

    p_rec = sub.add_parser("recall", help="Print recalled entries as JSON list.")
    p_rec.add_argument("--path", required=True, type=Path)
    p_rec.add_argument("--planned-tags", nargs="*", default=None)
    p_rec.add_argument("--recency-k", type=int, default=3)
    p_rec.add_argument("--extremes-k", type=int, default=3)
    p_rec.add_argument("--max-entries", type=int, default=10)
    p_rec.add_argument("--max-bytes", type=int, default=3000)

    p_ren = sub.add_parser("render", help="Recall then render as Markdown.")
    p_ren.add_argument("--path", required=True, type=Path)
    p_ren.add_argument("--planned-tags", nargs="*", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.cmd == "append":
        entry = json.loads(sys.stdin.read())
        aar = json.loads(args.aar.read_text()) if args.aar else None
        result = append_entry(args.path, entry, aar)
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "written": result.written,
                    "schema_valid": result.schema_valid,
                    "metrics_match_aar": result.metrics_match_aar,
                    "rewrites": result.rewrites,
                    "errors": list(result.errors),
                },
                sort_keys=True,
            )
        )
        return 0 if result.ok else 3
    if args.cmd == "recall":
        picked = recall(
            args.path,
            recency_k=args.recency_k,
            extremes_k=args.extremes_k,
            max_entries=args.max_entries,
            max_bytes=args.max_bytes,
            planned_tags=args.planned_tags,
        )
        print(json.dumps(picked, sort_keys=True, indent=2))
        return 0
    if args.cmd == "render":
        picked = recall(args.path, planned_tags=args.planned_tags)
        sys.stdout.write(render_for_prompt(picked))
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
