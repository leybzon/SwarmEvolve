#!/usr/bin/env python3
"""Dual-LLM evolutionary driver - wrapper over evolve.py core logic.

This is a simplified driver that uses the dual-LLM architecture (planner +
coder) instead of the single-LLM flow. It reuses most of evolve.py's
infrastructure but replaces the LLM generation step.

For M21 A/B/C testing only. Production use should integrate into evolve.py
after validation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sibling scripts importable
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import dual_llm  # noqa: E402
import fitness as fitness_mod  # noqa: E402
import inject_guards  # noqa: E402
import journal as journal_mod  # noqa: E402
import lint_ai_tokens  # noqa: E402
import llm_client  # noqa: E402
import telemetry_aar  # noqa: E402

REPO_ROOT = _HERE.parent

_LOG = logging.getLogger("swarmevolve.evolve_dual")


def evolve_dual_llm(
    *,
    opponent_path: Path,
    as_team: str,
    planner_model: str,
    coder_model: str,
    generations: int,
    n_matches: int,
    seed: int,
    out_dir: Path,
    strict_reflection: bool = False,
    init_champion: Path | None = None,
    acceptance_mode: str = "absolute",
) -> int:
    """Run evolutionary loop with dual-LLM architecture.

    Args:
        opponent_path: Path to opponent AI source
        as_team: "A" or "B"
        planner_model: Model for tactical planning (e.g., "claude-opus-4-7")
        coder_model: Model for implementation (e.g., "claude-haiku-4-5")
        generations: Number of generations to run
        n_matches: Matches per generation for fitness evaluation
        seed: Base seed for RNG
        out_dir: Output directory for run artifacts
        strict_reflection: Enable enhanced journal validation
        init_champion: Optional path to initial champion C++ source (default: stationary_v1)
        acceptance_mode: "absolute" (>0.0) or "relative" (>champion)

    Returns:
        Exit code (0 = success)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    journal_path = out_dir / "journal.jsonl"

    # Build clients
    planner_client = llm_client.AnthropicClient(model=planner_model)
    coder_client = llm_client.AnthropicClient(model=coder_model)

    namespace = f"Team{as_team}"
    opponent_source = opponent_path.read_text(encoding="utf-8")
    opponent_name = opponent_path.stem

    # Initialize champion
    if init_champion:
        if not init_champion.exists():
            _LOG.error("init-champion file not found: %s", init_champion)
            return 1
        current_champion = init_champion.read_text(encoding="utf-8")
        _LOG.info("init-champion loaded from %s", init_champion)
    else:
        baseline_path = REPO_ROOT / "src" / "baselines" / "stationary_v1.cpp"
        current_champion = baseline_path.read_text(encoding="utf-8")
        _LOG.info("init-champion using stationary baseline")

    _LOG.info("evolve-dual-start generations=%d planner=%s coder=%s",
              generations, planner_model, coder_model)

    for gen in range(generations):
        gen_dir = out_dir / f"gen_{gen:04d}"
        gen_dir.mkdir(parents=True, exist_ok=True)

        _LOG.info("generation gen=%d", gen)

        # Recall prior lessons from journal
        prior_lessons = "(none)"
        if journal_path.exists():
            entries = journal_mod.read_entries(journal_path)
            recalled = journal_mod.recall(journal_path, recency_k=3, max_entries=5)
            if recalled:
                prior_lessons = journal_mod.render_for_prompt(recalled, max_tokens=1500)

        # Get AAR from previous generation (if exists)
        aar_markdown = "(none - first generation)"
        aar_metrics = None
        if gen > 0:
            prev_gen_dir = out_dir / f"gen_{gen-1:04d}"
            prev_trace = prev_gen_dir / "trace_sample.jsonl"
            if prev_trace.exists():
                try:
                    aar_report = telemetry_aar.render_aar(
                        prev_trace,
                        perspective=as_team,
                        fmt="both",
                    )
                    aar_markdown = aar_report.markdown
                    aar_metrics = aar_report.structured
                except Exception as e:
                    _LOG.warning("aar-failed gen=%d err=%s", gen - 1, e)

        # Get context for iteration-aware prompting
        champion_fitness = _get_champion_fitness(journal_path) if gen > 0 else None
        last_entry = None
        if journal_path.exists() and gen > 0:
            entries = journal_mod.read_entries(journal_path)
            if entries:
                last_entry = entries[-1]

        # Dual-LLM generation
        try:
            result = dual_llm.dual_llm_generate(
                planner_client=planner_client,
                coder_client=coder_client,
                team_letter=as_team,
                namespace=namespace,
                opponent_name=opponent_name,
                opponent_source=opponent_source,
                aar_markdown=aar_markdown,
                prior_lessons=prior_lessons,
                champion_fitness=champion_fitness,
                last_generation=gen - 1 if gen > 0 else None,
                last_hypothesis=last_entry.get('hypothesis_tested') if last_entry else None,
                last_fitness=last_entry.get('fitness') if last_entry else None,
            )
        except dual_llm.DualLLMError as e:
            _LOG.error("dual-llm-failed gen=%d err=%s", gen, e)
            # Write stall entry to journal
            _write_stall_journal_entry(
                journal_path=journal_path,
                generation=gen,
                reason=str(e),
                model=f"{planner_model}+{coder_model}",
                seed=seed,
            )
            continue

        # Save tactic spec
        tactic_spec_path = gen_dir / "tactic_spec.json"
        tactic_spec_path.write_text(
            json.dumps(result.tactic_spec.to_dict(), indent=2) + "\n"
        )

        # Save responses
        (gen_dir / "planner_response.md").write_text(result.planner_response.text)
        (gen_dir / "coder_response.md").write_text(result.coder_response.text)

        # Save candidate
        candidate_path = gen_dir / "candidate.cpp"
        candidate_path.write_text(result.cpp_code)

        # Lint
        violations = lint_ai_tokens.scan_file(candidate_path)
        if violations:
            _LOG.warning("lint-failed gen=%d n_violations=%d", gen, len(violations))
            _write_stall_journal_entry(
                journal_path=journal_path,
                generation=gen,
                reason=f"{len(violations)} lint violations",
                model=f"{planner_model}+{coder_model}",
                seed=seed,
            )
            continue

        # Inject guards
        injected_path = gen_dir / "candidate.injected.cpp"
        try:
            source_code = candidate_path.read_text(encoding="utf-8")
            injected_code = inject_guards.inject(source_code)
            injected_path.write_text(injected_code, encoding="utf-8")
        except Exception as e:
            _LOG.warning("inject-failed gen=%d err=%s", gen, e)
            _write_stall_journal_entry(
                journal_path=journal_path,
                generation=gen,
                reason=f"inject failed: {e}",
                model=f"{planner_model}+{coder_model}",
                seed=seed,
            )
            continue

        # Evaluate fitness
        try:
            # fitness.evaluate_fitness takes (team_a_src, team_b_src, ...)
            if as_team == "A":
                team_a_src = injected_path
                team_b_src = opponent_path
            else:
                team_a_src = opponent_path
                team_b_src = injected_path

            fitness_result = fitness_mod.evaluate_fitness(
                team_a_src,
                team_b_src,
                n_matches=n_matches,
                seed_base=seed + gen * 1000,
            )
        except Exception as e:
            _LOG.warning("fitness-failed gen=%d err=%s", gen, e)
            _write_stall_journal_entry(
                journal_path=journal_path,
                generation=gen,
                reason=f"fitness eval failed: {e}",
                model=f"{planner_model}+{coder_model}",
                seed=seed,
            )
            continue

        # Accept candidate based on acceptance mode
        if acceptance_mode == "relative":
            champion_fitness = _get_champion_fitness(journal_path)
            # Accept if better than current champion (with small epsilon for noise)
            accepted = fitness_result.mean > (champion_fitness - 0.05)
            _LOG.info("acceptance-check mode=relative champion=%.3f candidate=%.3f accepted=%s",
                     champion_fitness, fitness_result.mean, accepted)
        else:
            # M22 behavior: absolute threshold
            accepted = fitness_result.mean > 0.0
            _LOG.info("acceptance-check mode=absolute candidate=%.3f accepted=%s",
                     fitness_result.mean, accepted)

        if accepted:
            current_champion = result.cpp_code
            (out_dir / "champion.cpp").write_text(current_champion)

        # Write journal entry
        _write_journal_entry(
            journal_path=journal_path,
            generation=gen,
            fitness_result=fitness_result,
            aar_metrics=aar_metrics,
            tactic_spec=result.tactic_spec,
            accepted=accepted,
            model=f"{planner_model}+{coder_model}",
            seed=seed,
            strict_reflection=strict_reflection,
        )

        _LOG.info(
            "gen-complete gen=%d fitness=%.3f accepted=%s tokens=%d",
            gen,
            fitness_result.mean,
            accepted,
            result.total_prompt_tokens + result.total_completion_tokens,
        )

    _LOG.info("evolve-dual-complete generations=%d", generations)
    return 0


def _get_champion_fitness(journal_path: Path) -> float:
    """Get fitness of most recent accepted champion from journal.

    Returns:
        Fitness of last accepted entry, or -1.0 if no champion exists yet.
    """
    if not journal_path.exists():
        return -1.0

    entries = journal_mod.read_entries(journal_path)
    accepted_entries = [e for e in entries if e.get('verdict') == 'confirmed']

    if not accepted_entries:
        return -1.0

    # Return fitness of most recent accepted entry
    return accepted_entries[-1]['fitness']


def _slugify_tag(s: str) -> str:
    """Slugify a tag: lowercase, alphanum+underscore only, max 50 chars."""
    import re
    slug = re.sub(r'[^a-z0-9_]', '', s.lower().replace(' ', '_').replace('-', '_'))
    return slug[:50]  # More conservative limit


def _write_stall_journal_entry(
    journal_path: Path,
    generation: int,
    reason: str,
    model: str,
    seed: int,
) -> None:
    """Write a stall entry when generation fails."""
    entry = {
        "generation": generation,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parent_generation": generation - 1 if generation > 0 else None,
        "track": "A",
        "model": model,
        "seed": seed,
        "status": "stalled",
        "fitness": None,
        "fitness_delta": None,
        "hypothesis_tested": f"generation failed: {reason}",
        "mechanism_expected": "",
        "mechanism_observed": reason,
        "verdict": "stalled",
        "outcome_summary": f"stalled at generation {generation}",
        "advice_to_future_self": "fix failure before continuing",
        "tactic_tags": ["stalled", "generation_failure"],
        "aar_metrics_cited": {},
        "validation": {"schema_valid": True, "metrics_match_aar": True, "rewrites": 0},
    }
    entry = journal_mod.canonicalise_entry(entry)
    result = journal_mod.validate_against_aar(entry, aar=None, strict_reflection=False)
    if result.ok:
        journal_mod._write_line(journal_path, entry)


def _write_journal_entry(
    journal_path: Path,
    generation: int,
    fitness_result,
    aar_metrics: dict | None,
    tactic_spec,
    accepted: bool,
    model: str,
    seed: int,
    strict_reflection: bool,
) -> None:
    """Write journal entry with predicted vs actual metrics."""
    # Build entry grounded in tactic spec predictions
    verdict = "confirmed" if accepted else "rejected"

    # Extract predicted changes from tactic spec
    predicted_metrics = {
        change.metric: change.target_value
        for change in tactic_spec.expected_changes
    }

    entry = {
        "generation": generation,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parent_generation": generation - 1 if generation > 0 else None,
        "track": "A",
        "model": model,
        "seed": seed,
        "status": "ok",
        "fitness": fitness_result.mean,
        "fitness_delta": None,  # TODO: compute from parent
        "hypothesis_tested": f"{tactic_spec.tactic_name}: {tactic_spec.why_this_counters_failure[:150]}",
        "mechanism_expected": tactic_spec.mechanism[:400],  # Journal schema limit ~400
        "mechanism_observed": tactic_spec.why_this_counters_failure[:400],
        "verdict": verdict,
        "outcome_summary": f"{'accepted' if accepted else 'rejected'}: mean={fitness_result.mean:.3f}",
        "advice_to_future_self": f"predicted metrics: {predicted_metrics}",
        "tactic_tags": [_slugify_tag(m) for m in tactic_spec.key_metrics[:6]] if tactic_spec.key_metrics else ["dual_llm"],
        "aar_metrics_cited": aar_metrics or {},
        "validation": {"schema_valid": True, "metrics_match_aar": True, "rewrites": 0},
    }

    entry = journal_mod.canonicalise_entry(entry)
    result = journal_mod.validate_against_aar(
        entry,
        aar=aar_metrics,
        strict_reflection=strict_reflection,
    )

    if result.ok:
        journal_mod._write_line(journal_path, entry)
    else:
        _LOG.warning("journal-validation-failed gen=%d errors=%s", generation, result.errors)
        # Write anyway but mark validation failure
        entry["validation"]["schema_valid"] = result.schema_valid
        entry["validation"]["metrics_match_aar"] = result.metrics_match_aar
        journal_mod._write_line(journal_path, entry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dual-LLM evolutionary driver (M21)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--opponent", required=True, type=Path,
                        help="Opponent AI source (.cpp)")
    parser.add_argument("--as-team", choices=("A", "B"), default="A",
                        help="Which team slot (default: A)")
    parser.add_argument("--planner-model", default="claude-opus-4-7",
                        help="Model for planner LLM")
    parser.add_argument("--coder-model", default="claude-haiku-4-5",
                        help="Model for coder LLM")
    parser.add_argument("--generations", type=int, default=10,
                        help="Number of generations")
    parser.add_argument("--n-matches", type=int, default=10,
                        help="Matches per generation")
    parser.add_argument("--seed", type=int, default=42,
                        help="Base seed")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="Output directory")
    parser.add_argument("--strict-reflection", action="store_true",
                        help="Enable enhanced journal validation")
    parser.add_argument("--init-champion", type=Path, default=None,
                        help="Path to initial champion C++ file (default: stationary_v1)")
    parser.add_argument("--acceptance-mode", choices=["absolute", "relative"], default="absolute",
                        help="Acceptance criterion: absolute (>0.0) vs relative (>champion)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    return evolve_dual_llm(
        opponent_path=args.opponent,
        as_team=args.as_team,
        planner_model=args.planner_model,
        coder_model=args.coder_model,
        generations=args.generations,
        n_matches=args.n_matches,
        seed=args.seed,
        out_dir=args.out_dir,
        strict_reflection=args.strict_reflection,
        init_champion=args.init_champion,
        acceptance_mode=args.acceptance_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
