#!/usr/bin/env python3
"""Closed-loop evolutionary driver (M10).

Wraps the single-iteration pipeline from :mod:`evolve_once` into a
generation loop with accept-if-better selection, append-only experiment
logging, periodic checkpointing, and resumption.

Contract
--------
Each generation executes, in order:

1. **Prompt** — render ``prompts/evolve_ai.md`` against the *current
   champion* (opponent frozen for the run). A "recent fitness" note is
   appended so the LLM sees the champion's mean score over the last few
   generations, which gives it context for what "better" means.
2. **LLM call** — via :class:`llm_client.AnthropicClient` or the
   deterministic :class:`llm_client.MockClient` test double. Failures
   are rejected, counted toward ``--max-compile-failures``, and the
   champion carries over unchanged.
3. **Parse** — extract the first fenced ``cpp`` block. Missing block
   rejects the generation.
4. **Lint + inject** — :mod:`lint_ai_tokens` (banned-token scan) then
   :mod:`inject_guards` (static upper bound on every loop). Either
   failing rejects the generation.
5. **Evaluate** — ``fitness.evaluate_fitness(candidate, opponent, ...)``.
   A compile error counts toward the failure cap.
6. **Accept-if-better** — if ``challenger.mean > champion.mean +
   accept_margin`` the challenger becomes the new champion and its
   injected source is copied to ``champions/best.cpp``. Ties keep the
   incumbent (strictly conservative; prevents drift on noise).
7. **Log** — every stage transition is an event in
   ``events.jsonl`` with timestamps + redacted payloads. Per-gen
   directories under ``gens/NNNN/`` preserve the prompt, response,
   candidate source, and (if we got that far) the full ``fitness.json``.

Every ``--checkpoint-every`` generations (default 10) a checkpoint file
``checkpoints/NNNN.json`` is atomically written and a fitness plot is
regenerated under ``plots/fitness.png``. The loop state (``state.json``)
is rewritten at every successful step so a crash between checkpoints is
still recoverable.

Resume
------
``evolve.py --resume <run_dir>`` reads ``state.json`` (or reconstructs
from the latest ``checkpoints/*.json`` / ``events.jsonl`` if missing),
appends a ``resume`` event, and picks up at the next generation. The
root seed is preserved so the per-generation RNG keeps walking the same
sequence after resume.

Exit codes
----------
``0``    clean finish (generations budget consumed).
``2``    invalid input (missing opponent, bad CLI).
``30``   aborted after hitting ``--max-compile-failures``.
``31``   aborted on unrecoverable LLM error (auth, quota).
``32``   aborted on schema/integrity failure.
``33``   resume requested but target directory is corrupt.

No secret ever appears in the run dir — :mod:`experiment_log` redaction
applies to every log write, and the API key is never serialised.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import random
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sibling scripts importable when run as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import experiment_log  # noqa: E402
import fitness as fitness_mod  # noqa: E402
import inject_guards  # noqa: E402
import journal as journal_mod  # noqa: E402 (M16 wiring)
import lint_ai_tokens  # noqa: E402
import llm_client  # noqa: E402
import telemetry_aar  # noqa: E402 (M16 wiring)

REPO_ROOT = _HERE.parent
PROMPTS = REPO_ROOT / "prompts"
TYPES_HEADER = REPO_ROOT / "src" / "types.h"
ABI_HEADER = REPO_ROOT / "src" / "ai_abi.h"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_LOOP_ABORTED_COMPILE_CAP = 30
EXIT_LOOP_ABORTED_LLM = 31
EXIT_LOOP_ABORTED_SCHEMA = 32
EXIT_RESUME_CORRUPT = 33

CHECKPOINT_SCHEMA_VERSION = "m10.v1"


# ------------------------------------------------------------------------
# Per-generation record
# ------------------------------------------------------------------------


@dataclass
class GenSummary:
    """Compact per-generation record held in memory and serialised into
    checkpoints. One entry per attempted generation, regardless of
    whether the challenger was accepted."""

    generation: int
    status: str                   # see STATUS_* below
    reject_reason: str | None = None
    mean: float | None = None
    stdev: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    wins_a: int | None = None
    wins_b: int | None = None
    draws: int | None = None
    invalid: int | None = None
    wall_seconds: float = 0.0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    model: str = ""
    candidate_sha256: str | None = None
    accepted: bool = False
    # Relative path (from run_dir) to the accepted candidate.injected.cpp,
    # only set when accepted=True.
    champion_source_path: str | None = None


STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"        # compiled + evaluated but not better
STATUS_LLM_FAILED = "llm_failed"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_LINT_FAILED = "lint_failed"
STATUS_INJECT_FAILED = "inject_failed"
STATUS_COMPILE_FAILED = "compile_failed"
STATUS_EVAL_FAILED = "eval_failed"

# Statuses that count toward --max-compile-failures.
COUNTS_AS_FAILURE = frozenset({
    STATUS_LLM_FAILED,
    STATUS_PARSE_FAILED,
    STATUS_LINT_FAILED,
    STATUS_INJECT_FAILED,
    STATUS_COMPILE_FAILED,
    STATUS_EVAL_FAILED,
})


# ------------------------------------------------------------------------
# Loop state (in-memory + persisted)
# ------------------------------------------------------------------------


@dataclass
class LoopConfig:
    team_letter: str
    opponent_path: str            # absolute path
    model: str
    client_kind: str              # "anthropic" or "mock"
    generations: int
    n_matches: int
    workers: int
    accept_margin: float
    max_compile_failures: int
    checkpoint_every: int
    seed_base_root: int
    seed_ai_path: str             # absolute path; initial champion
    prompt_template_path: str
    mock_response_paths: list[str] = field(default_factory=list)
    recent_fitness_window: int = 5
    # M16: 2x2 ablation flags. Default ON for both so normal runs get the
    # full feedback loop; Track-B / Track-C ablations flip these.
    aar_enabled: bool = True
    journal_enabled: bool = True
    # Which seed to replay for the per-generation AAR capture. The loop
    # always replays the first seed of the generation (seed_base_root +
    # gen * n_matches), so the AAR is reproducible bit-for-bit on resume.


@dataclass
class LoopState:
    run_id: str
    wall_start_iso: str
    generation: int                # next gen number to run
    champion_fitness: dict[str, Any] | None  # FitnessResult serialised, or None pre-bootstrap
    champion_source_rel: str       # path relative to run_dir
    champion_generation: int       # the gen that produced the champion (-1 for seed)
    compile_failures: int
    tokens_input: int
    tokens_output: int
    config: LoopConfig
    history: list[GenSummary] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "wall_start_iso": self.wall_start_iso,
            "generation": self.generation,
            "champion_fitness": self.champion_fitness,
            "champion_source_rel": self.champion_source_rel,
            "champion_generation": self.champion_generation,
            "compile_failures": self.compile_failures,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "config": asdict(self.config),
            "history": [asdict(g) for g in self.history],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> LoopState:
        cfg = LoopConfig(**payload["config"])
        history = [GenSummary(**g) for g in payload.get("history", [])]
        return cls(
            run_id=payload["run_id"],
            wall_start_iso=payload["wall_start_iso"],
            generation=payload["generation"],
            champion_fitness=payload.get("champion_fitness"),
            champion_source_rel=payload["champion_source_rel"],
            champion_generation=payload["champion_generation"],
            compile_failures=payload["compile_failures"],
            tokens_input=payload.get("tokens_input", 0),
            tokens_output=payload.get("tokens_output", 0),
            config=cfg,
            history=history,
        )


# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{ts}-{suffix}"


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to ``path`` by renaming a tmp file in the same dir.

    Uses ``os.replace`` which is atomic on POSIX and Windows provided
    the tmp file lives on the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_prompt(
    template_path: Path,
    *,
    team_letter: str,
    namespace: str,
    opponent_name: str,
    opponent_source: Path,
    recent_fitness_note: str,
    aar_block: str = "(disabled)",
    prior_lessons_block: str = "(disabled)",
) -> str:
    """Substitute the M7.5 template + append recent-fitness + AAR/journal.

    The template itself has no slot for "recent fitness", so we append a
    short section after the rendered template. This keeps the template
    authoritative for the prompt skeleton while letting the loop inject
    context that evolve_once doesn't need.

    ``{AAR}`` and ``{PRIOR_LESSONS}`` (M16) are substituted if present
    in the template; otherwise the blocks are appended at the end so
    older templates keep working. Each defaults to ``(disabled)`` when
    the ablation flag is off — the exact sentinel string is part of
    the prompt contract so tests can assert ablation took effect.
    """
    template = template_path.read_text()
    base = (template
            .replace("{TEAM_LETTER}", team_letter)
            .replace("{NAMESPACE}", namespace)
            .replace("{OPPONENT_NAME}", opponent_name)
            .replace("{OPPONENT_SOURCE}", opponent_source.read_text())
            .replace("{TYPES_HEADER}", TYPES_HEADER.read_text())
            .replace("{ABI_HEADER}", ABI_HEADER.read_text()))
    substituted_aar = "{AAR}" in base
    substituted_lessons = "{PRIOR_LESSONS}" in base
    base = base.replace("{AAR}", aar_block).replace("{PRIOR_LESSONS}", prior_lessons_block)
    if recent_fitness_note:
        base += "\n\n# Recent fitness\n\n" + recent_fitness_note + "\n"
    # Append fallback sections if the template didn't carry the slots.
    if not substituted_lessons:
        base += "\n\n# Prior lessons (journal)\n\n" + prior_lessons_block + "\n"
    if not substituted_aar:
        base += "\n\n# After-action report (previous generation)\n\n" + aar_block + "\n"
    return base


def _recent_fitness_note(history: list[GenSummary], window: int) -> str:
    """One-line-per-gen summary of the last ``window`` accepted OR
    evaluated attempts. Format kept compact and LLM-parseable."""
    lines: list[str] = []
    for g in history[-window:]:
        if g.mean is None:
            lines.append(f"- gen {g.generation}: {g.status}")
        else:
            tag = "accepted" if g.accepted else "rejected"
            lines.append(
                f"- gen {g.generation}: mean={g.mean:+.3f} "
                f"ci=[{g.ci_low:+.3f},{g.ci_high:+.3f}] ({tag})"
                if g.ci_low is not None and g.ci_high is not None else
                f"- gen {g.generation}: mean={g.mean:+.3f} ({tag})"
            )
    if not lines:
        return ("No prior generations yet; this is the first attempt. "
                "Write the strongest opening you can.")
    return "\n".join(lines)


# ------------------------------------------------------------------------
# Client construction
# ------------------------------------------------------------------------


def _build_client(
    kind: str,
    *,
    model: str | None,
    mock_response_paths: list[str],
    mock_cursor: int,
) -> tuple[llm_client.LLMClient, int]:
    """Return (client, next_mock_cursor).

    MockClient is initialised with *all remaining* responses starting
    from ``mock_cursor``. This means each generation constructs a fresh
    MockClient with the correct single queued response, which keeps
    resume correct (cursor is stored in state.json implicitly via
    ``history`` length).
    """
    if kind == "mock":
        if not mock_response_paths:
            raise SystemExit("--client=mock requires --mock-response-dir with at least one *.md")
        if mock_cursor >= len(mock_response_paths):
            raise SystemExit(
                f"mock response cursor {mock_cursor} exceeds queue of "
                f"{len(mock_response_paths)} responses"
            )
        text = Path(mock_response_paths[mock_cursor]).read_text()
        client = llm_client.MockClient(
            responses=[llm_client.LLMResponse(text=text, model="mock")],
            model="mock",
        )
        return client, mock_cursor + 1
    # anthropic
    return (
        llm_client.AnthropicClient(
            model=model,
            system=(
                "You are a careful systems-C++ engineer. Follow all constraints. "
                "Respond with exactly one fenced cpp block containing the entire "
                "translation unit and no prose outside the block."
            ),
        ),
        mock_cursor,
    )


# ------------------------------------------------------------------------
# M16: AAR + journal plumbing
# ------------------------------------------------------------------------

JOURNAL_FILENAME = "journal.jsonl"


def _journal_path(run_dir: Path) -> Path:
    return run_dir / JOURNAL_FILENAME


def _aar_paths(gen_dir: Path) -> tuple[Path, Path, Path]:
    """Return (trace, markdown, structured_json) paths for this gen."""
    return (gen_dir / "aar_trace.jsonl",
            gen_dir / "aar.md",
            gen_dir / "aar.json")


def _capture_aar(
    *,
    injected_path: Path,
    opponent_path: Path,
    team_letter: str,
    seed: int,
    gen_dir: Path,
    logger: logging.Logger,
    max_ticks: int = 600,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Run one recorded match and render the AAR.

    Returns the structured AAR dict on success, or ``None`` on any
    failure (engine non-outcome exit, trace missing/invalid, metric
    compute failure). Failures are logged but never raised — AAR is
    best-effort so it never blocks the evolutionary loop.
    """
    # Compile via fitness._compile (reused) against the correct team layout.
    try:
        if team_letter == "A":
            ta, tb = injected_path, opponent_path
        else:
            ta, tb = opponent_path, injected_path
        work_dir = gen_dir / "aar_build"
        work_dir.mkdir(parents=True, exist_ok=True)
        compiler = fitness_mod._find_compiler()  # noqa: SLF001 - reuse
        if not compiler:
            logger.warning("aar-compile-skipped gen=%s no-compiler", gen_dir.name)
            return None
        binary = fitness_mod._compile(ta, tb, work_dir, compiler)  # noqa: SLF001
    except Exception as exc:  # pragma: no cover - compile path exercised in M10 tests
        logger.warning("aar-compile-failed gen=%s err=%s", gen_dir.name, exc)
        return None

    trace_path, md_path, json_path = _aar_paths(gen_dir)
    try:
        import subprocess
        proc = subprocess.run(
            [str(binary),
             "--seed", str(seed),
             "--max-ticks", str(max_ticks),
             "--record", str(trace_path),
             "--record-actions"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        # Outcome exit codes (0/1/2) are all valid terminations.
        if proc.returncode not in (0, 1, 2):
            logger.warning("aar-engine-nonoutcome gen=%s rc=%d stderr=%r",
                           gen_dir.name, proc.returncode, proc.stderr[:200])
            return None
    except Exception as exc:
        logger.warning("aar-engine-failed gen=%s err=%s", gen_dir.name, exc)
        return None

    try:
        report = telemetry_aar.render_aar(trace_path, perspective=team_letter, fmt="both")
    except Exception as exc:
        logger.warning("aar-render-failed gen=%s err=%s", gen_dir.name, exc)
        return None

    md_path.write_text(report.markdown)
    json_path.write_text(json.dumps(report.structured, sort_keys=True, indent=2) + "\n")
    return dict(report.structured)


def _deterministic_journal_entry(
    *,
    state: LoopState,
    summary: GenSummary,
    aar_metrics: dict[str, Any] | None,
    parent_generation: int | None,
    timestamp_utc: str,
) -> dict[str, Any]:
    """Build a rule-based journal entry grounded in the AAR.

    This is the M16 baseline writer: deterministic, never hallucinates,
    always passes the M15c metric-grounding check by construction. The
    M17 LLM-authored writer will replace this path for Track-C.
    """
    status = summary.status
    ok = status == STATUS_ACCEPTED or status == STATUS_REJECTED
    fitness = summary.mean
    parent_mean = None
    for prev in reversed(state.history):
        if prev.generation != summary.generation and prev.mean is not None:
            parent_mean = prev.mean
            break
    fitness_delta = (fitness - parent_mean) if (fitness is not None and parent_mean is not None) else None

    if not ok:
        verdict = "stalled"
        hypothesis = f"candidate failed in stage: {status}"
        tags: list[str] = [status]
        advice = "fix upstream failure before iterating on strategy"
        mech_expected = ""
        mech_observed = summary.reject_reason or ""
        outcome_summary = f"gen {summary.generation} did not evaluate: {status}"
        cited: dict[str, Any] = {}
        status_out = status
        fitness_out: float | None = None
        fitness_delta_out: float | None = None
    else:
        if summary.accepted:
            verdict = "confirmed"
        elif fitness_delta is not None and fitness_delta > 0:
            verdict = "partial"
        else:
            verdict = "rejected"
        hypothesis = ("accept-if-better candidate; measure combat metrics "
                      "and compare to prior champion")
        tags_set: list[str] = ["accept_if_better"]
        if aar_metrics:
            ff = aar_metrics.get("focus_fire_redundancy")
            if isinstance(ff, (int, float)) and ff > 0.3:
                tags_set.append("focus_fire")
            cd = aar_metrics.get("cooldown_utilization_us")
            if isinstance(cd, (int, float)) and cd < 0.3:
                tags_set.append("low_cooldown_uptime")
            mpd = aar_metrics.get("mean_pairwise_distance_us")
            if isinstance(mpd, (int, float)) and mpd < 40:
                tags_set.append("tight_formation")
            elif isinstance(mpd, (int, float)) and mpd > 120:
                tags_set.append("loose_formation")
        tags = tags_set[:6]
        advice = ("carry forward" if summary.accepted else
                  "try a different mechanism next generation")
        mech_expected = "mean score improves over champion"
        ci_s = ""
        if summary.ci_low is not None and summary.ci_high is not None:
            ci_s = f" ci=[{summary.ci_low:+.3f},{summary.ci_high:+.3f}]"
        mech_observed = f"mean={fitness:+.3f}{ci_s}"
        outcome_summary = (
            f"{'accepted' if summary.accepted else 'rejected'}: "
            f"a={summary.wins_a} b={summary.wins_b} draws={summary.draws} "
            f"invalid={summary.invalid}"
        )[:240]
        # Cite a small, deterministic subset of AAR numbers.
        cited = {}
        if aar_metrics:
            for k in ("outcome", "ticks", "shots_fired_us", "shots_hit_us",
                      "focus_fire_redundancy", "cooldown_utilization_us",
                      "mean_pairwise_distance_us"):
                if k in aar_metrics:
                    cited[k] = aar_metrics[k]
        status_out = "ok"
        fitness_out = fitness
        fitness_delta_out = fitness_delta

    entry: dict[str, Any] = {
        "schema_version": journal_mod.JOURNAL_SCHEMA_VERSION,
        "generation": summary.generation,
        "timestamp_utc": timestamp_utc,
        "parent_generation": parent_generation,
        "track": "A",  # tracks B/C are introduced in M19; default to "A".
        "model": state.config.model,
        "seed": state.config.seed_base_root,
        "status": status_out,
        "fitness": fitness_out,
        "fitness_delta": fitness_delta_out,
        "outcome_summary": outcome_summary,
        "hypothesis_tested": hypothesis,
        "mechanism_expected": mech_expected,
        "mechanism_observed": mech_observed,
        "verdict": verdict,
        "tactic_tags": tags,
        "advice_to_future_self": advice,
        "aar_metrics_cited": cited,
        "validation": {
            "schema_valid": True,
            "metrics_match_aar": True,
            "rewrites": 0,
        },
    }
    return entry


def _build_context_blocks(
    *,
    run_dir: Path,
    state: LoopState,
    logger: logging.Logger,
) -> tuple[str, str]:
    """Return (aar_block, prior_lessons_block) for the *next* prompt.

    - AAR block: markdown of the most recent generation's aar.md if it
      exists; otherwise ``(none — no prior generation yet)``. Disabled
      ablation short-circuits to the sentinel ``(disabled)``.
    - Prior-lessons block: recall() from journal.jsonl rendered via
      journal.render_for_prompt, with planned_tags drawn from the most
      recent entry's tactic_tags (self-continuation bias; reviewers
      can swap this in M17).
    """
    aar_block = "(disabled)" if not state.config.aar_enabled else "(none — no prior generation yet)"
    lessons_block = "(disabled)" if not state.config.journal_enabled else "(none — first generation in this lineage.)"

    if state.config.aar_enabled:
        # Last generation that produced an AAR sidecar.
        for g in reversed(state.history):
            md = run_dir / "gens" / f"{g.generation:04d}" / "aar.md"
            if md.is_file():
                aar_block = md.read_text().rstrip() + "\n"
                break

    if state.config.journal_enabled:
        jp = _journal_path(run_dir)
        if jp.is_file():
            try:
                entries = journal_mod.read_entries(jp)
                planned: list[str] = []
                if entries:
                    planned = list(entries[-1].get("tactic_tags") or [])
                picked = journal_mod.recall(jp, planned_tags=planned)
                lessons_block = journal_mod.render_for_prompt(picked).rstrip() + "\n"
            except Exception as exc:
                logger.warning("journal-recall-failed err=%s", exc)

    return aar_block, lessons_block


# ------------------------------------------------------------------------
# One generation
# ------------------------------------------------------------------------


def _run_generation(
    *,
    state: LoopState,
    run_dir: Path,
    logger: logging.Logger,
    log: experiment_log.ExperimentLog,
    mock_cursor: int,
) -> tuple[GenSummary, int]:
    """Execute one full generation and return (summary, next_mock_cursor)."""
    gen = state.generation
    cfg = state.config
    gen_dir = run_dir / "gens" / f"{gen:04d}"
    gen_dir.mkdir(parents=True, exist_ok=True)

    team_letter = cfg.team_letter
    namespace = "TeamA" if team_letter == "A" else "TeamB"
    opponent_path = Path(cfg.opponent_path)
    opponent_name = opponent_path.stem
    model = cfg.model
    t0 = time.monotonic()

    summary = GenSummary(generation=gen, status=STATUS_ACCEPTED, model=model)
    log.write("gen_start", generation=gen, team_letter=team_letter,
              opponent=str(opponent_path), model=model)

    # ---- Prompt -----------------------------------------------------
    note = _recent_fitness_note(state.history, cfg.recent_fitness_window)
    aar_block, lessons_block = _build_context_blocks(
        run_dir=run_dir, state=state, logger=logger,
    )
    try:
        prompt_text = _render_prompt(
            Path(cfg.prompt_template_path),
            team_letter=team_letter,
            namespace=namespace,
            opponent_name=opponent_name,
            opponent_source=opponent_path,
            recent_fitness_note=note,
            aar_block=aar_block,
            prior_lessons_block=lessons_block,
        )
    except OSError as exc:
        logger.error("prompt-render-failed err=%s", exc)
        summary.status = STATUS_PARSE_FAILED
        summary.reject_reason = f"prompt render: {exc}"
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds)
        return summary, mock_cursor

    (gen_dir / "prompt.md").write_text(prompt_text)

    # ---- LLM --------------------------------------------------------
    try:
        client, mock_cursor = _build_client(
            cfg.client_kind,
            model=model,
            mock_response_paths=cfg.mock_response_paths,
            mock_cursor=mock_cursor,
        )
    except SystemExit as exc:
        logger.error("client-build-failed err=%s", exc)
        summary.status = STATUS_LLM_FAILED
        summary.reject_reason = f"client build: {exc}"
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds)
        return summary, mock_cursor

    summary.model = client.model
    try:
        response = client.generate(prompt_text, max_tokens=4096)
    except llm_client.LLMError as exc:
        # Surface the (already-redacted) error; the ExperimentLog will
        # re-apply redaction on the stored form.
        msg = llm_client.redact_secrets(str(exc))
        logger.error("llm-failed err=%s", msg)
        summary.status = STATUS_LLM_FAILED
        summary.reject_reason = msg
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds, error=msg)
        return summary, mock_cursor

    (gen_dir / "response.md").write_text(response.text)
    summary.llm_input_tokens = response.prompt_tokens
    summary.llm_output_tokens = response.completion_tokens
    state.tokens_input += response.prompt_tokens
    state.tokens_output += response.completion_tokens
    log.write("llm_response", generation=gen,
              response_chars=len(response.text),
              prompt_tokens=response.prompt_tokens,
              completion_tokens=response.completion_tokens,
              stop_reason=response.metadata.get("stop_reason", ""))

    # ---- Parse ------------------------------------------------------
    cpp = llm_client.extract_cpp_block(response.text)
    if cpp is None:
        logger.error("no-cpp-block generation=%d", gen)
        summary.status = STATUS_PARSE_FAILED
        summary.reject_reason = "no fenced cpp block"
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds)
        return summary, mock_cursor

    candidate_path = gen_dir / "candidate.cpp"
    candidate_path.write_text(cpp)
    summary.candidate_sha256 = _sha256_file(candidate_path)

    # ---- Lint -------------------------------------------------------
    violations = lint_ai_tokens.scan_file(candidate_path)
    if violations:
        logger.error("lint-failed generation=%d n=%d", gen, len(violations))
        summary.status = STATUS_LINT_FAILED
        summary.reject_reason = f"{len(violations)} banned-token violations"
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds,
                  violations=[{"line": ln, "token": tok, "reason": r}
                              for ln, tok, r in violations[:10]])
        return summary, mock_cursor

    # ---- Inject -----------------------------------------------------
    injected_path = gen_dir / "candidate.injected.cpp"
    try:
        injected_path.write_text(inject_guards.inject(candidate_path.read_text()))
    except (inject_guards.InjectorError, ValueError) as exc:
        logger.error("inject-failed generation=%d err=%s", gen, exc)
        summary.status = STATUS_INJECT_FAILED
        summary.reject_reason = str(exc)
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds, error=str(exc))
        return summary, mock_cursor

    # ---- Evaluate ---------------------------------------------------
    # The per-generation seed base is deterministic in (seed_base_root,
    # generation) so a resumed run produces identical fitness samples.
    seed_base = cfg.seed_base_root + gen * cfg.n_matches
    if team_letter == "A":
        ta_src, tb_src = injected_path, opponent_path
    else:
        ta_src, tb_src = opponent_path, injected_path

    try:
        result = fitness_mod.evaluate_fitness(
            ta_src, tb_src,
            n_matches=cfg.n_matches,
            seed_base=seed_base,
            workers=cfg.workers,
        )
    except fitness_mod.CompileError as exc:
        logger.error("compile-failed generation=%d err=%s", gen, exc)
        summary.status = STATUS_COMPILE_FAILED
        summary.reject_reason = str(exc)[:512]
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds,
                  error=str(exc)[:512])
        return summary, mock_cursor
    except Exception as exc:
        logger.error("eval-failed generation=%d err=%s", gen, exc)
        summary.status = STATUS_EVAL_FAILED
        summary.reject_reason = f"{type(exc).__name__}: {exc}"[:512]
        summary.wall_seconds = time.monotonic() - t0
        log.write("gen_end", generation=gen, status=summary.status,
                  wall_seconds=summary.wall_seconds, error=str(exc)[:512])
        return summary, mock_cursor

    # Always normalise the challenger's mean relative to team A: when the
    # evolving side is B, evaluate_fitness's "mean" is from A's
    # perspective so invert it so "higher = better for the evolving
    # team" holds everywhere.
    challenger_mean = result.mean if team_letter == "A" else -result.mean
    if result.ci_low is not None and result.ci_high is not None:
        if team_letter == "A":
            ci_low, ci_high = result.ci_low, result.ci_high
        else:
            ci_low, ci_high = -result.ci_high, -result.ci_low
    else:
        ci_low = ci_high = None

    # Persist the full FitnessResult for debugging.
    (gen_dir / "fitness.json").write_text(
        json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True) + "\n"
    )

    summary.mean = challenger_mean
    summary.stdev = result.stdev
    summary.ci_low = ci_low
    summary.ci_high = ci_high
    summary.wins_a = result.wins_a
    summary.wins_b = result.wins_b
    summary.draws = result.draws
    summary.invalid = result.invalid

    # ---- Accept / reject -------------------------------------------
    champ_mean = _champion_mean(state)
    threshold = champ_mean + cfg.accept_margin
    accept = challenger_mean > threshold

    summary.wall_seconds = time.monotonic() - t0

    if accept:
        summary.status = STATUS_ACCEPTED
        summary.accepted = True
        # Promote: copy injected source to champions/best.cpp and stash
        # path in summary.
        champions_dir = run_dir / "champions"
        champions_dir.mkdir(parents=True, exist_ok=True)
        best_cpp = champions_dir / "best.cpp"
        shutil.copyfile(injected_path, best_cpp)
        # Also snapshot per-generation champion for history.
        snapshot = champions_dir / f"gen_{gen:04d}.cpp"
        shutil.copyfile(injected_path, snapshot)
        summary.champion_source_path = str(snapshot.relative_to(run_dir))
        # Update loop state.
        state.champion_fitness = dataclasses.asdict(result)
        state.champion_source_rel = str(snapshot.relative_to(run_dir))
        state.champion_generation = gen
    else:
        summary.status = STATUS_REJECTED
        summary.reject_reason = (
            f"challenger mean {challenger_mean:+.3f} "
            f"<= threshold {threshold:+.3f} (champion {champ_mean:+.3f} "
            f"+ margin {cfg.accept_margin:+.3f})"
        )

    log.write(
        "gen_end",
        generation=gen,
        status=summary.status,
        accepted=summary.accepted,
        mean=summary.mean,
        ci_low=summary.ci_low,
        ci_high=summary.ci_high,
        wins_a=summary.wins_a,
        wins_b=summary.wins_b,
        draws=summary.draws,
        invalid=summary.invalid,
        wall_seconds=summary.wall_seconds,
    )

    # ---- AAR capture (M16) -----------------------------------------
    # Best-effort: one recorded match at this gen's base seed. Writes
    # gen_dir/aar.md + aar.json; a failure leaves no sidecars so the
    # next generation's `{AAR}` slot stays at its default sentinel.
    if cfg.aar_enabled:
        _capture_aar(
            injected_path=injected_path,
            opponent_path=opponent_path,
            team_letter=team_letter,
            seed=seed_base,
            gen_dir=gen_dir,
            logger=logger,
        )
    return summary, mock_cursor


def _champion_mean(state: LoopState) -> float:
    """Return champion.mean on the evolving team's scale, or -inf for a
    bootstrap champion with no FitnessResult (so *any* scored challenger
    wins gen 0)."""
    if state.champion_fitness is None:
        return float("-inf")
    raw = state.champion_fitness.get("mean", 0.0)
    return float(raw) if state.config.team_letter == "A" else -float(raw)


# ------------------------------------------------------------------------
# Checkpoint / plot
# ------------------------------------------------------------------------


def _write_checkpoint(run_dir: Path, state: LoopState, log: experiment_log.ExperimentLog) -> Path:
    ck_dir = run_dir / "checkpoints"
    ck_dir.mkdir(parents=True, exist_ok=True)
    env = experiment_log.build_environment_snapshot(
        team_a_src=None, team_b_src=Path(state.config.opponent_path),
        extra={"llm_model": state.config.model, "client": state.config.client_kind},
    )
    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": state.run_id,
        "wall_start_iso": state.wall_start_iso,
        "wall_checkpoint_iso": _iso_now(),
        "generations_completed": state.generation,
        "champion": {
            "path": state.champion_source_rel,
            "generation_accepted": state.champion_generation,
            "fitness": state.champion_fitness,
        },
        "opponent": {
            "path": state.config.opponent_path,
            "sha256": _sha256_file(Path(state.config.opponent_path)),
        },
        "config": asdict(state.config),
        "history": [asdict(g) for g in state.history],
        "tokens_total": {
            "input": state.tokens_input,
            "output": state.tokens_output,
        },
        "environment": env,
        "compile_failures": state.compile_failures,
    }
    ck_path = ck_dir / f"{state.generation:04d}.json"
    _atomic_write_json(ck_path, payload)
    latest = ck_dir / "latest.json"
    # latest.json is a regular file (not a symlink) so cross-machine
    # scp/rsync always copies it.
    _atomic_write_json(latest, payload)
    log.write("checkpoint", path=str(ck_path.relative_to(run_dir)),
              generation=state.generation)
    return ck_path


def _write_state(run_dir: Path, state: LoopState) -> None:
    _atomic_write_json(run_dir / "state.json", state.to_json())


def _write_fitness_plot(run_dir: Path, state: LoopState, logger: logging.Logger) -> Path | None:
    """Regenerate ``plots/fitness.png``. Silently returns None if
    matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless — Spark, CI, Docker.
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib-missing; skipping plot")
        return None

    gens = [g.generation for g in state.history]
    means = [g.mean for g in state.history]
    lows = [g.ci_low for g in state.history]
    highs = [g.ci_high for g in state.history]
    accepted = [g.accepted for g in state.history]

    # Champion trace: the accepted mean, carried forward across gens.
    champ_trace: list[float | None] = []
    running = state.champion_fitness
    running_mean: float | None = None
    for g in state.history:
        if g.accepted and g.mean is not None:
            running_mean = g.mean
        champ_trace.append(running_mean)
    _ = running  # silence unused — kept for future rollback logic

    fig, ax = plt.subplots(figsize=(8, 4.5))
    # Challenger points: green if accepted, red if rejected/failed.
    acc_gens = [g for g, a in zip(gens, accepted, strict=True) if a]
    acc_means = [m for m, a in zip(means, accepted, strict=True)
                 if a and m is not None]
    rej_gens = [g for g, a, m in zip(gens, accepted, means, strict=True)
                if not a and m is not None]
    rej_means = [m for a, m in zip(accepted, means, strict=True)
                 if not a and m is not None]

    # Error bars for challengers where CI is defined.
    ci_gens, ci_mids, ci_errs = [], [], []
    for g, m, lo, hi in zip(gens, means, lows, highs, strict=True):
        if m is not None and lo is not None and hi is not None:
            ci_gens.append(g)
            ci_mids.append(m)
            ci_errs.append([m - lo, hi - m])
    if ci_gens:
        errs = list(zip(*ci_errs, strict=True)) if ci_errs else ([], [])
        ax.errorbar(
            ci_gens, ci_mids,
            yerr=[list(errs[0]), list(errs[1])],
            fmt="none", ecolor="#cccccc", alpha=0.8, capsize=2, zorder=1,
        )
    ax.scatter(acc_gens, acc_means, c="#2a9d8f", s=40,
               label="accepted", zorder=3)
    ax.scatter(rej_gens, rej_means, c="#e76f51", s=28, marker="x",
               label="rejected", zorder=3)

    # Champion step line.
    if any(v is not None for v in champ_trace):
        champ_gens = [g for g, v in zip(gens, champ_trace, strict=True)
                      if v is not None]
        champ_vals = [v for v in champ_trace if v is not None]
        ax.step(champ_gens, champ_vals, where="post", color="#264653",
                linewidth=2, label="champion", zorder=2)

    ax.axhline(0.0, color="#999999", linewidth=0.75, linestyle=":")
    ax.set_xlabel("generation")
    ax.set_ylabel(f"mean score (team {state.config.team_letter} perspective)")
    ax.set_ylim(-1.05, 1.05)
    ax.set_title(f"SwarmEvolve — run {state.run_id}")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    plots_dir = run_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    out = plots_dir / "fitness.png"
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


# ------------------------------------------------------------------------
# Bootstrap (seed) evaluation
# ------------------------------------------------------------------------


def _bootstrap_champion(
    *,
    state: LoopState,
    run_dir: Path,
    log: experiment_log.ExperimentLog,
    logger: logging.Logger,
) -> None:
    """Evaluate the seed AI against the opponent once so the first
    challenger has a fitness target. Skipped if seed == opponent (mean
    is trivially 0 by symmetry) — in that case champion_fitness is left
    None and any scored challenger wins gen 0."""
    seed_src = Path(state.config.seed_ai_path)
    opponent_src = Path(state.config.opponent_path)
    if _sha256_file(seed_src) == _sha256_file(opponent_src):
        logger.info("bootstrap: seed == opponent; skipping initial fitness")
        # Still copy the seed into champions/ so best.cpp always exists.
        champs = run_dir / "champions"
        champs.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed_src, champs / "best.cpp")
        shutil.copyfile(seed_src, champs / "gen_seed.cpp")
        state.champion_source_rel = "champions/gen_seed.cpp"
        log.write("bootstrap", status="skipped_symmetric",
                  seed_path=str(seed_src))
        return

    logger.info("bootstrap: evaluating seed ai vs opponent")
    log.write("bootstrap", status="evaluating",
              seed_path=str(seed_src), opponent_path=str(opponent_src))
    if state.config.team_letter == "A":
        ta, tb = seed_src, opponent_src
    else:
        ta, tb = opponent_src, seed_src
    result = fitness_mod.evaluate_fitness(
        ta, tb,
        n_matches=state.config.n_matches,
        seed_base=state.config.seed_base_root,
        workers=state.config.workers,
    )
    # Stash full result (on A-perspective scale, as evaluate_fitness returns it).
    state.champion_fitness = dataclasses.asdict(result)

    champs = run_dir / "champions"
    champs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed_src, champs / "best.cpp")
    shutil.copyfile(seed_src, champs / "gen_seed.cpp")
    state.champion_source_rel = "champions/gen_seed.cpp"
    log.write("bootstrap_done",
              mean=result.mean, ci_low=result.ci_low, ci_high=result.ci_high)


# ------------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------------


def run_loop(state: LoopState, run_dir: Path, logger: logging.Logger) -> int:
    """Run the generation loop from ``state.generation`` through
    ``config.generations``. Idempotent-by-resume: state/checkpoint files
    are atomically written so interruption is recoverable."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with experiment_log.ExperimentLog(run_dir) as log:
        # First-time setup: write experiment_start + bootstrap.
        is_fresh_start = state.generation == 0 and not state.history
        if is_fresh_start:
            log.write_start(
                experiment_type="evolve",
                team_a_src=(state.config.seed_ai_path if state.config.team_letter == "A"
                            else state.config.opponent_path),
                team_b_src=(state.config.opponent_path if state.config.team_letter == "A"
                            else state.config.seed_ai_path),
                config=asdict(state.config),
                extra_env={"llm_model": state.config.model,
                           "client": state.config.client_kind,
                           "run_id": state.run_id},
            )
            _bootstrap_champion(state=state, run_dir=run_dir, log=log, logger=logger)
        else:
            log.write("resume",
                      generation=state.generation,
                      history_len=len(state.history),
                      compile_failures=state.compile_failures,
                      run_id=state.run_id)

        _write_state(run_dir, state)

        # Mock cursor = number of mock responses already consumed =
        # number of generations that *called the LLM*. A regenerated
        # mock client always starts from the head of its own queue of
        # one, so cursor is advanced per gen that reached LLM stage.
        mock_cursor = sum(
            1 for g in state.history
            if g.status not in (STATUS_LLM_FAILED,)
            or g.reject_reason is None   # llm_failed with None reason = client_build
        )
        # Simpler: cursor = history length, since every *attempted* gen
        # advances the mock (including llm_failed, parse_failed,
        # etc.). This matches _build_client's per-gen consumption.
        mock_cursor = len(state.history)

        exit_code = EXIT_OK
        while state.generation < state.config.generations:
            try:
                summary, mock_cursor = _run_generation(
                    state=state, run_dir=run_dir, logger=logger,
                    log=log, mock_cursor=mock_cursor,
                )
            except KeyboardInterrupt:
                logger.warning("interrupted at generation=%d", state.generation)
                log.write("interrupted", generation=state.generation)
                _write_state(run_dir, state)
                _write_checkpoint(run_dir, state, log)
                return EXIT_OK

            state.history.append(summary)
            if summary.status in COUNTS_AS_FAILURE:
                state.compile_failures += 1

            # ---- Journal append (M16) -----------------------------------
            # Deterministic, rule-based writer. Always grounded in the
            # AAR that _run_generation just emitted (if any); stall
            # generations get verdict=stalled with null fitness to match
            # NEXT_PHASE_PLAN §2 Q14.
            if state.config.journal_enabled:
                aar_metrics: dict[str, Any] | None = None
                if state.config.aar_enabled:
                    _, _, aar_json = _aar_paths(run_dir / "gens" / f"{summary.generation:04d}")
                    if aar_json.is_file():
                        try:
                            aar_metrics = json.loads(aar_json.read_text())
                        except Exception as exc:
                            logger.warning("aar-json-reread-failed gen=%d err=%s",
                                           summary.generation, exc)
                parent_gen: int | None = None
                for prev in reversed(state.history[:-1]):
                    if prev.status in (STATUS_ACCEPTED, STATUS_REJECTED):
                        parent_gen = prev.generation
                        break
                entry = _deterministic_journal_entry(
                    state=state,
                    summary=summary,
                    aar_metrics=aar_metrics,
                    parent_generation=parent_gen,
                    timestamp_utc=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                )
                vr = journal_mod.append_entry(_journal_path(run_dir), entry, aar_metrics)
                log.write(
                    "journal_append",
                    generation=summary.generation,
                    ok=vr.ok,
                    written=vr.written,
                    metrics_match_aar=vr.metrics_match_aar,
                    schema_valid=vr.schema_valid,
                    errors=list(vr.errors),
                )

            state.generation += 1
            _write_state(run_dir, state)

            if (state.generation % state.config.checkpoint_every == 0
                    or state.generation == state.config.generations):
                _write_checkpoint(run_dir, state, log)
                _write_fitness_plot(run_dir, state, logger)

            if state.compile_failures >= state.config.max_compile_failures:
                logger.error(
                    "aborting: compile_failures=%d >= max=%d",
                    state.compile_failures, state.config.max_compile_failures,
                )
                log.write("loop_aborted",
                          reason="max_compile_failures",
                          failures=state.compile_failures)
                exit_code = EXIT_LOOP_ABORTED_COMPILE_CAP
                # Final checkpoint + plot before bailing.
                _write_checkpoint(run_dir, state, log)
                _write_fitness_plot(run_dir, state, logger)
                break

        log.write("loop_done",
                  generations_completed=state.generation,
                  compile_failures=state.compile_failures,
                  exit_code=exit_code)
    return exit_code


# ------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------


def _configure_logging(verbosity: int) -> logging.Logger:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    return logging.getLogger("swarmevolve.evolve")


def _default_run_dir() -> Path:
    rid = _new_run_id()
    return REPO_ROOT / "data" / "experiments" / rid


def _collect_mock_responses(path: str | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if p.is_file():
        return [str(p.resolve())]
    if p.is_dir():
        files = sorted(p.glob("*.md"))
        return [str(f.resolve()) for f in files]
    raise SystemExit(f"--mock-response-dir path not found: {path}")


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolve", description=__doc__)
    parser.add_argument("--opponent", default=None,
                        help="path to frozen opponent AI source")
    parser.add_argument("--as-team", choices=("A", "B"), default="A")
    parser.add_argument("--generations", type=int, default=50)
    parser.add_argument("--n-matches", type=int, default=20)
    parser.add_argument("--workers", type=int, default=None,
                        help="parallel fitness workers (default: min(n_matches, nproc))")
    parser.add_argument("--client", choices=("anthropic", "mock"), default="anthropic")
    parser.add_argument("--mock-response-dir", default=None,
                        help="when --client=mock: directory of *.md (or single file) "
                             "providing LLM responses in sorted order")
    parser.add_argument("--model", default=None,
                        help="model id (default $ANTHROPIC_MODEL)")
    parser.add_argument("--seed", type=int, default=None,
                        help="root seed (default: derived from time)")
    parser.add_argument("--accept-margin", type=float, default=0.0)
    parser.add_argument("--max-compile-failures", type=int, default=5)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--resume", default=None,
                        help="resume a previous run directory")
    parser.add_argument("--seed-ai", default=None,
                        help="initial champion AI (default: --opponent)")
    parser.add_argument("--prompt", default=str(PROMPTS / "evolve_ai.md"))
    # M16 2x2 ablation flags. Paired --aar/--no-aar + --journal/--no-journal.
    aar_group = parser.add_mutually_exclusive_group()
    aar_group.add_argument("--aar", dest="aar", action="store_true",
                           help="render per-gen After-Action Report into the "
                                "next prompt (default on)")
    aar_group.add_argument("--no-aar", dest="aar", action="store_false",
                           help="disable AAR capture + injection (ablation)")
    parser.set_defaults(aar=True)
    journal_group = parser.add_mutually_exclusive_group()
    journal_group.add_argument("--journal", dest="journal", action="store_true",
                               help="append + recall a learning journal (default on)")
    journal_group.add_argument("--no-journal", dest="journal", action="store_false",
                               help="disable journal append + recall (ablation)")
    parser.set_defaults(journal=True)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def _load_resume(run_dir: Path) -> LoopState:
    state_path = run_dir / "state.json"
    if state_path.is_file():
        try:
            return LoopState.from_json(json.loads(state_path.read_text()))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SystemExit(
                f"resume: state.json corrupt at {state_path}: {exc}"
            ) from exc
    # Fallback: reconstruct from latest checkpoint (non-default path;
    # used if someone deletes state.json but keeps checkpoints).
    latest = run_dir / "checkpoints" / "latest.json"
    if not latest.is_file():
        raise SystemExit(f"resume: neither state.json nor checkpoints/latest.json at {run_dir}")
    payload = json.loads(latest.read_text())
    cfg = LoopConfig(**payload["config"])
    history = [GenSummary(**g) for g in payload.get("history", [])]
    tokens = payload.get("tokens_total", {"input": 0, "output": 0})
    return LoopState(
        run_id=payload["run_id"],
        wall_start_iso=payload["wall_start_iso"],
        generation=payload["generations_completed"],
        champion_fitness=(payload.get("champion") or {}).get("fitness"),
        champion_source_rel=(payload.get("champion") or {}).get("path") or "champions/best.cpp",
        champion_generation=(payload.get("champion") or {}).get("generation_accepted", -1),
        compile_failures=payload.get("compile_failures", 0),
        tokens_input=tokens.get("input", 0),
        tokens_output=tokens.get("output", 0),
        config=cfg,
        history=history,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    logger = _configure_logging(args.verbose)

    # Resume path: everything comes from the on-disk state.
    if args.resume:
        run_dir = Path(args.resume).resolve()
        if not run_dir.is_dir():
            logger.error("resume: directory not found: %s", run_dir)
            return EXIT_RESUME_CORRUPT
        try:
            state = _load_resume(run_dir)
        except SystemExit as exc:
            logger.error("%s", exc)
            return EXIT_RESUME_CORRUPT
        # The user may still override the generation budget on resume.
        if args.generations != parser.get_default("generations"):
            state.config.generations = args.generations
        logger.info("resuming run=%s at generation=%d", state.run_id, state.generation)
        return run_loop(state, run_dir, logger)

    # Fresh run.
    if not args.opponent:
        logger.error("--opponent is required for a fresh run")
        return EXIT_INVALID_INPUT
    opponent_path = Path(args.opponent).resolve()
    if not opponent_path.is_file():
        logger.error("opponent not found: %s", opponent_path)
        return EXIT_INVALID_INPUT
    seed_ai_path = Path(args.seed_ai).resolve() if args.seed_ai else opponent_path
    if not seed_ai_path.is_file():
        logger.error("seed-ai not found: %s", seed_ai_path)
        return EXIT_INVALID_INPUT
    prompt_path = Path(args.prompt).resolve()
    if not prompt_path.is_file():
        logger.error("prompt template not found: %s", prompt_path)
        return EXIT_INVALID_INPUT

    mock_paths = _collect_mock_responses(args.mock_response_dir) if args.client == "mock" else []
    if args.client == "mock" and not mock_paths:
        logger.error("--client=mock requires --mock-response-dir")
        return EXIT_INVALID_INPUT

    seed_base_root = args.seed if args.seed is not None else int(time.time()) & 0x7FFFFFFF
    workers = args.workers or min(args.n_matches, os.cpu_count() or 1)
    model = args.model or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"

    cfg = LoopConfig(
        team_letter=args.as_team,
        opponent_path=str(opponent_path),
        model=model,
        client_kind=args.client,
        generations=args.generations,
        n_matches=args.n_matches,
        workers=workers,
        accept_margin=args.accept_margin,
        max_compile_failures=args.max_compile_failures,
        checkpoint_every=args.checkpoint_every,
        seed_base_root=seed_base_root,
        seed_ai_path=str(seed_ai_path),
        prompt_template_path=str(prompt_path),
        mock_response_paths=mock_paths,
        aar_enabled=args.aar,
        journal_enabled=args.journal,
    )

    run_dir = Path(args.out_dir).resolve() if args.out_dir else _default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    state = LoopState(
        run_id=_new_run_id() if not args.out_dir else run_dir.name,
        wall_start_iso=_iso_now(),
        generation=0,
        champion_fitness=None,
        champion_source_rel="champions/gen_seed.cpp",
        champion_generation=-1,
        compile_failures=0,
        tokens_input=0,
        tokens_output=0,
        config=cfg,
    )
    _rng = random.Random(seed_base_root)  # reserved for future jitter/exploration
    del _rng
    logger.info("starting run=%s out=%s generations=%d client=%s model=%s",
                state.run_id, run_dir, cfg.generations, cfg.client_kind, cfg.model)
    return run_loop(state, run_dir, logger)


if __name__ == "__main__":
    sys.exit(main())
