#!/usr/bin/env python3
"""Single-shot live LLM → AI → match pipeline.

This is a thin, explicit driver meant to verify the Anthropic adapter
end-to-end before the full M10 evolutionary loop is built. It:

1. Renders ``prompts/evolve_ai.md`` with the opponent's source, the
   types/ABI headers, and the requested team letter.
2. Calls the configured LLM client (default: ``AnthropicClient``).
3. Extracts the first fenced ``cpp`` block.
4. Runs the banned-token linter against the generated source; rejects
   early on violation (with a redacted diagnostic).
5. Writes the candidate source to ``<out-dir>/candidate.cpp``.
6. Runs ``scripts/inject_guards.py`` on the candidate in-place (written
   as ``candidate.injected.cpp``). Goto-based loops are rejected.
7. Hands both files to ``scripts/orchestrator.py run``: the generated
   AI is Team A, the opponent is Team B (or vice versa, controlled by
   ``--as-team``).
8. Writes ``<out-dir>/evolve_once.json`` summarizing the pipeline and
   copies the orchestrator's ``results.json`` alongside.

No secrets touch the command line or any persisted file — the API key
stays in the process environment and is never echoed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sibling scripts importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import inject_guards  # noqa: E402
import lint_ai_tokens  # noqa: E402
import llm_client  # noqa: E402

REPO_ROOT = _HERE.parent
PROMPTS = REPO_ROOT / "prompts"
BASELINES = REPO_ROOT / "src" / "baselines"
TYPES_HEADER = REPO_ROOT / "src" / "types.h"
ABI_HEADER = REPO_ROOT / "src" / "ai_abi.h"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_LLM_FAILED = 20
EXIT_PARSE_FAILED = 21
EXIT_LINT_FAILED = 22
EXIT_INJECT_FAILED = 23
EXIT_ORCHESTRATOR_FAILED = 24


@dataclass
class EvolveOnceSummary:
    status: str
    team_letter: str
    namespace: str
    opponent: str
    model: str
    seed: int
    prompt_path: str
    prompt_chars: int
    response_chars: int
    prompt_tokens: int
    completion_tokens: int
    response_metadata: dict[str, str] = field(default_factory=dict)
    candidate_path: str | None = None
    candidate_injected_path: str | None = None
    lint_violations: list[dict[str, Any]] = field(default_factory=list)
    orchestrator_exit: int | None = None
    orchestrator_results_path: str | None = None
    match_outcome: str | None = None
    match_ticks: int | None = None
    wall_ms: int = 0


# ------------------------------------------------------------------------
# Prompt rendering
# ------------------------------------------------------------------------


def _render_prompt(
    template_path: Path,
    *,
    team_letter: str,
    namespace: str,
    opponent_name: str,
    opponent_source: Path,
) -> str:
    template = template_path.read_text()
    return (
        template.replace("{TEAM_LETTER}", team_letter)
        .replace("{NAMESPACE}", namespace)
        .replace("{OPPONENT_NAME}", opponent_name)
        .replace("{OPPONENT_SOURCE}", opponent_source.read_text())
        .replace("{TYPES_HEADER}", TYPES_HEADER.read_text())
        .replace("{ABI_HEADER}", ABI_HEADER.read_text())
    )


# ------------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------------


def _lint_source(path: Path) -> list[tuple[int, str, str]]:
    """Run the banned-token linter against a single file and return
    violations (line, token, reason)."""
    return lint_ai_tokens.scan_file(path)


def _inject_in_place(src: Path, dest: Path) -> None:
    """Inject loop guards; raises InjectorError on reject (e.g. goto)."""
    text = src.read_text()
    injected = inject_guards.inject(text)
    dest.write_text(injected)


# ------------------------------------------------------------------------
# Main pipeline
# ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evolve_once")
    parser.add_argument(
        "--opponent",
        default=str(BASELINES / "pursuit_v1.cpp"),
        help="path to opponent AI source (default: pursuit_v1.cpp)",
    )
    parser.add_argument(
        "--as-team", choices=("A", "B"), default="A", help="which team the generated AI plays as"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-ticks", type=int, default=1000)
    parser.add_argument("--drones-a", type=int, default=10)
    parser.add_argument("--drones-b", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--out-dir", default=None, help="output directory (default: data/runs/evolve_once/<ts>/)"
    )
    parser.add_argument(
        "--prompt", default=str(PROMPTS / "evolve_ai.md"), help="prompt template path"
    )
    parser.add_argument(
        "--model", default=None, help="override model id (default: $ANTHROPIC_MODEL)"
    )
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--client", choices=("anthropic", "mock"), default="anthropic")
    parser.add_argument(
        "--mock-response-path",
        default=None,
        help="when --client=mock, read LLM response text from this file",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="build the prompt and exit without calling the LLM"
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    logger = _configure_logging(args.verbose)
    t0 = time.monotonic()

    opponent_path = Path(args.opponent).resolve()
    if not opponent_path.is_file():
        logger.error("missing-opponent path=%s", opponent_path)
        return EXIT_INVALID_INPUT

    prompt_path = Path(args.prompt).resolve()
    if not prompt_path.is_file():
        logger.error("missing-prompt path=%s", prompt_path)
        return EXIT_INVALID_INPUT

    team_letter = args.as_team
    namespace = "TeamA" if team_letter == "A" else "TeamB"
    opponent_name = opponent_path.stem

    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("out-dir path=%s", out_dir)

    prompt_text = _render_prompt(
        prompt_path,
        team_letter=team_letter,
        namespace=namespace,
        opponent_name=opponent_name,
        opponent_source=opponent_path,
    )
    (out_dir / "prompt.md").write_text(prompt_text)
    logger.info("prompt-rendered chars=%d", len(prompt_text))

    if args.dry_run:
        logger.info("dry-run; stopping before LLM call")
        _write_summary(
            out_dir,
            EvolveOnceSummary(
                status="dry_run",
                team_letter=team_letter,
                namespace=namespace,
                opponent=str(opponent_path),
                model=args.model or os.environ.get("ANTHROPIC_MODEL", ""),
                seed=args.seed,
                prompt_path=str(prompt_path),
                prompt_chars=len(prompt_text),
                response_chars=0,
                prompt_tokens=0,
                completion_tokens=0,
                wall_ms=int((time.monotonic() - t0) * 1000),
            ),
        )
        return EXIT_OK

    # ---- LLM --------------------------------------------------------
    client = _build_client(args)
    logger.info("llm-call model=%s max_tokens=%d", client.model, args.max_tokens)
    try:
        response = client.generate(prompt_text, max_tokens=args.max_tokens)
    except llm_client.LLMError as exc:
        logger.error("llm-failed err=%s", llm_client.redact_secrets(str(exc)))
        _write_summary(
            out_dir,
            EvolveOnceSummary(
                status="llm_failed",
                team_letter=team_letter,
                namespace=namespace,
                opponent=str(opponent_path),
                model=getattr(client, "model", "?"),
                seed=args.seed,
                prompt_path=str(prompt_path),
                prompt_chars=len(prompt_text),
                response_chars=0,
                prompt_tokens=0,
                completion_tokens=0,
                wall_ms=int((time.monotonic() - t0) * 1000),
            ),
        )
        return EXIT_LLM_FAILED

    (out_dir / "response.md").write_text(response.text)
    logger.info(
        "llm-response chars=%d in_tokens=%d out_tokens=%d stop=%s",
        len(response.text),
        response.prompt_tokens,
        response.completion_tokens,
        response.metadata.get("stop_reason", ""),
    )

    # ---- Parse ------------------------------------------------------
    cpp = llm_client.extract_cpp_block(response.text)
    if cpp is None:
        logger.error("no-cpp-block response_chars=%d", len(response.text))
        _write_summary(
            out_dir,
            EvolveOnceSummary(
                status="parse_failed",
                team_letter=team_letter,
                namespace=namespace,
                opponent=str(opponent_path),
                model=client.model,
                seed=args.seed,
                prompt_path=str(prompt_path),
                prompt_chars=len(prompt_text),
                response_chars=len(response.text),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                response_metadata=dict(response.metadata),
                wall_ms=int((time.monotonic() - t0) * 1000),
            ),
        )
        return EXIT_PARSE_FAILED

    candidate_path = out_dir / "candidate.cpp"
    candidate_path.write_text(cpp)
    logger.info("candidate-written path=%s chars=%d", candidate_path, len(cpp))

    # ---- Lint -------------------------------------------------------
    violations = _lint_source(candidate_path)
    if violations:
        logger.error("lint-failed n=%d", len(violations))
        _write_summary(
            out_dir,
            EvolveOnceSummary(
                status="lint_failed",
                team_letter=team_letter,
                namespace=namespace,
                opponent=str(opponent_path),
                model=client.model,
                seed=args.seed,
                prompt_path=str(prompt_path),
                prompt_chars=len(prompt_text),
                response_chars=len(response.text),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                response_metadata=dict(response.metadata),
                candidate_path=str(candidate_path),
                lint_violations=[
                    {"line": line, "token": tok, "reason": reason}
                    for (line, tok, reason) in violations
                ],
                wall_ms=int((time.monotonic() - t0) * 1000),
            ),
        )
        return EXIT_LINT_FAILED

    # ---- Inject -----------------------------------------------------
    injected_path = out_dir / "candidate.injected.cpp"
    try:
        _inject_in_place(candidate_path, injected_path)
    except (inject_guards.InjectorError, ValueError) as exc:
        rc = getattr(exc, "exit_code", 4)
        logger.error("inject-failed rc=%d msg=%s", rc, str(exc))
        _write_summary(
            out_dir,
            EvolveOnceSummary(
                status="inject_failed",
                team_letter=team_letter,
                namespace=namespace,
                opponent=str(opponent_path),
                model=client.model,
                seed=args.seed,
                prompt_path=str(prompt_path),
                prompt_chars=len(prompt_text),
                response_chars=len(response.text),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                response_metadata=dict(response.metadata),
                candidate_path=str(candidate_path),
                wall_ms=int((time.monotonic() - t0) * 1000),
            ),
        )
        return EXIT_INJECT_FAILED

    logger.info("guards-injected path=%s", injected_path)

    # ---- Match via orchestrator ------------------------------------
    match_dir = out_dir / "match"
    if team_letter == "A":
        team_a_src = injected_path
        team_b_src = opponent_path
    else:
        team_a_src = opponent_path
        team_b_src = injected_path
    cmd = [
        sys.executable,
        str(_HERE / "orchestrator.py"),
        "run",
        "--team-a",
        str(team_a_src),
        "--team-b",
        str(team_b_src),
        "--seed",
        str(args.seed),
        "--max-ticks",
        str(args.max_ticks),
        "--drones-a",
        str(args.drones_a),
        "--drones-b",
        str(args.drones_b),
        "--timeout",
        str(args.timeout),
        "--out-dir",
        str(match_dir),
    ]
    logger.info("orchestrator-start")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    logger.info("orchestrator-done rc=%d", proc.returncode)

    match_results_path = match_dir / "results.json"
    match_outcome: str | None = None
    match_ticks: int | None = None
    if match_results_path.is_file():
        try:
            payload = json.loads(match_results_path.read_text())
            if payload.get("match"):
                match_outcome = payload["match"].get("outcome")
                match_ticks = payload["match"].get("ticks")
        except json.JSONDecodeError:
            pass

    status = "ok" if proc.returncode == 0 else "orchestrator_failed"
    summary = EvolveOnceSummary(
        status=status,
        team_letter=team_letter,
        namespace=namespace,
        opponent=str(opponent_path),
        model=client.model,
        seed=args.seed,
        prompt_path=str(prompt_path),
        prompt_chars=len(prompt_text),
        response_chars=len(response.text),
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        response_metadata=dict(response.metadata),
        candidate_path=str(candidate_path),
        candidate_injected_path=str(injected_path),
        orchestrator_exit=proc.returncode,
        orchestrator_results_path=str(match_results_path) if match_results_path.is_file() else None,
        match_outcome=match_outcome,
        match_ticks=match_ticks,
        wall_ms=int((time.monotonic() - t0) * 1000),
    )
    _write_summary(out_dir, summary)
    return EXIT_OK if proc.returncode == 0 else EXIT_ORCHESTRATOR_FAILED


# ------------------------------------------------------------------------
# Helpers
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
    return logging.getLogger("swarmevolve.evolve_once")


def _default_out_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "data" / "runs" / "evolve_once" / stamp


def _build_client(args: argparse.Namespace) -> llm_client.LLMClient:
    if args.client == "mock":
        if not args.mock_response_path:
            raise SystemExit("--client=mock requires --mock-response-path")
        text = Path(args.mock_response_path).read_text()
        return llm_client.MockClient(
            responses=[llm_client.LLMResponse(text=text, model="mock")],
            model="mock",
        )
    # Default: anthropic
    return llm_client.AnthropicClient(
        model=args.model,
        system=(
            "You are a careful systems-C++ engineer. Follow all constraints. "
            "Respond with exactly one fenced cpp block containing the entire "
            "translation unit and no prose outside the block."
        ),
    )


def _write_summary(out_dir: Path, summary: EvolveOnceSummary) -> Path:
    dest = out_dir / "evolve_once.json"
    dest.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n")
    return dest


if __name__ == "__main__":
    sys.exit(main())
