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
import round_records  # noqa: E402

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
    inherit: str = "parent",
    feedback: str = "aar",
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
        inherit: "parent" shows the current champion to planner and coder;
            "none" does not
        feedback: "aar" shows the planner the AAR of the champion's recorded
            match, the last 3 journal lessons and the champion's numbers;
            "none" shows none of them. The AAR is recorded either way, for
            grounding.

    Returns:
        Exit code (0 = success)
    """
    if inherit not in dual_llm.INHERIT_MODES:
        raise ValueError(f"inherit must be one of {dual_llm.INHERIT_MODES}, got {inherit!r}")
    if feedback not in dual_llm.FEEDBACK_MODES:
        raise ValueError(f"feedback must be one of {dual_llm.FEEDBACK_MODES}, got {feedback!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    journal_path = out_dir / "journal.jsonl"
    model = f"{planner_model}+{coder_model}"

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

    _LOG.info(
        "evolve-dual-start generations=%d planner=%s coder=%s inherit=%s feedback=%s",
        generations,
        planner_model,
        coder_model,
        inherit,
        feedback,
    )

    for gen in range(generations):
        gen_dir = out_dir / f"gen_{gen:04d}"
        gen_dir.mkdir(parents=True, exist_ok=True)

        _LOG.info("generation gen=%d", gen)

        # Champion and candidate play the same seeds this generation.
        seed_base = seed + gen * 1000
        summary: dict = {
            "generation": gen,
            "as_team": as_team,
            "inherit": inherit,
            "feedback": feedback,
            "seed_base": seed_base,
            "n_matches": n_matches,
            "aar_seed": round_records.aar_seed(seed, gen),
            "llm_round": False,
            "status": "ok",
            "stall_reason": None,
            "champion_fitness": None,
            "candidate_fitness": None,
            "accepted": None,
            "planner_attempts": 0,
            "similarity": None,
            "grounding": None,
        }

        def stall(
            reason: str, gen: int = gen, gen_dir: Path = gen_dir, summary: dict = summary
        ) -> None:
            """Record a failed generation in its summary and the journal."""
            _LOG.warning("gen-stalled gen=%d reason=%s", gen, reason)
            summary["status"] = "stalled"
            summary["stall_reason"] = reason
            round_records.write_summary(gen_dir, summary)
            _write_stall_journal_entry(
                journal_path=journal_path,
                generation=gen,
                reason=reason,
                model=model,
                seed=seed,
            )

        # Initialize variables that may be set conditionally
        aar_metrics = None
        aar_report = None

        guarded_opponent = gen_dir / "opponent.cpp"
        champion_path = gen_dir / "champion.cpp"
        try:
            _write_guarded(guarded_opponent, opponent_source)
            _write_guarded(champion_path, current_champion)
        except Exception as e:
            stall(f"inject failed for champion or opponent: {e}")
            continue

        # AAR: one recorded match of the champion against the opponent on
        # this generation's fixed AAR seed.
        try:
            aar_report = round_records.record_aar(
                champion_path,
                guarded_opponent,
                team=as_team,
                seed=summary["aar_seed"],
                round_dir=gen_dir,
            )
            aar_metrics = aar_report.structured
        except Exception as e:
            if feedback == "aar":
                stall(f"aar failed: {e}")
                continue
            _LOG.warning("aar-failed gen=%d err=%s", gen, e)

        # Measure the current champion on this generation's seeds.
        try:
            champion_result = _evaluate(
                champion_path,
                guarded_opponent,
                as_team=as_team,
                n_matches=n_matches,
                seed_base=seed_base,
                result_path=gen_dir / "fitness_champion.json",
            )
        except Exception as e:
            stall(f"champion eval failed: {e}")
            continue
        champion_fitness = _team_score(champion_result.mean, as_team)
        summary["champion_fitness"] = champion_fitness

        # Special handling for gen 0 with init-champion: skip LLM, just evaluate
        skip_llm = gen == 0 and init_champion

        if skip_llm:
            _LOG.info("gen-0-init-champion: using provided champion directly (no LLM call)")
            candidate_path = gen_dir / "candidate.cpp"
            candidate_path.write_text(current_champion)

            # Create minimal metadata files for consistency
            (gen_dir / "planner_response.md").write_text(
                "# Gen 0: Init Champion Evaluation\n\n"
                f"Using provided init-champion from: {init_champion}\n\n"
                "No planner call - directly evaluating champion code.\n"
            )
            (gen_dir / "coder_response.md").write_text(
                "# Gen 0: Init Champion Evaluation\n\nNo coder call - using champion code as-is.\n"
            )

            # Create minimal tactic spec for journal
            import tactic_spec as tspec

            minimal_tactic_spec = tspec.TacticSpec(
                key_metrics=[],
                why_we_failed="N/A - evaluating provided init-champion",
                what_enemy_exploited="N/A",
                constraints_violated="N/A",
                tactic_name=f"Init Champion from {init_champion.stem}",
                mechanism="Evaluating provided champion code directly (no LLM generation)",
                why_this_counters_failure="N/A - baseline evaluation",
                expected_changes=[],
                message_protocol="Inherited from init-champion",
                memory_layout="Inherited from init-champion",
                special_cases="None",
            )
            (gen_dir / "tactic_spec.json").write_text(
                json.dumps(minimal_tactic_spec.to_dict(), indent=2) + "\n"
            )

            # Create mock result for consistency with normal path
            from types import SimpleNamespace

            result = SimpleNamespace(
                cpp_code=current_champion,
                tactic_spec=minimal_tactic_spec,
                total_prompt_tokens=0,
                total_completion_tokens=0,
            )

            # Lint (the init champion is linted like any candidate)
            violations = lint_ai_tokens.scan_file(candidate_path)
            if violations:
                stall(f"{len(violations)} lint violations")
                continue

            # The candidate is the champion itself: reuse its measurement.
            fitness_result = champion_result

        else:
            # Normal path: Dual-LLM generation
            summary["llm_round"] = True
            prior_lessons = None
            last_entry = None
            if feedback == "aar" and journal_path.exists():
                # The last 3 journal entries
                entries = journal_mod.read_entries(journal_path)
                prior_lessons = round_records.format_lessons(entries)
                if entries:
                    last_entry = entries[-1]

            # Dual-LLM generation
            saver = round_records.PromptSaver(gen_dir)
            try:
                result = dual_llm.dual_llm_generate(
                    planner_client=planner_client,
                    coder_client=coder_client,
                    team_letter=as_team,
                    namespace=namespace,
                    opponent_name=opponent_name,
                    opponent_source=opponent_source,
                    parent_source=current_champion,
                    inherit=inherit,
                    feedback=feedback,
                    aar_markdown=aar_report.markdown if aar_report is not None else None,
                    prior_lessons=prior_lessons,
                    champion_fitness=champion_fitness,
                    last_generation=gen - 1 if gen > 0 else None,
                    last_hypothesis=last_entry.get("hypothesis_tested") if last_entry else None,
                    last_fitness=last_entry.get("fitness") if last_entry else None,
                    on_prompt=saver,
                )
            except dual_llm.DualLLMError as e:
                summary["planner_attempts"] = saver.planner_attempts
                stall(str(e))
                continue
            summary["planner_attempts"] = saver.planner_attempts

            # Save tactic spec
            tactic_spec_path = gen_dir / "tactic_spec.json"
            tactic_spec_path.write_text(json.dumps(result.tactic_spec.to_dict(), indent=2) + "\n")

            # Save responses
            (gen_dir / "planner_response.md").write_text(result.planner_response.text)
            (gen_dir / "coder_response.md").write_text(result.coder_response.text)

            # Save candidate
            candidate_path = gen_dir / "candidate.cpp"
            candidate_path.write_text(result.cpp_code)

            summary["similarity"] = round_records.similarity(current_champion, result.cpp_code)
            if aar_metrics is not None:
                summary["grounding"] = round_records.grounding(result.tactic_spec, aar_metrics)
            _LOG.info(
                "similarity gen=%d line=%.3f token=%.3f",
                gen,
                summary["similarity"]["line_ratio"],
                summary["similarity"]["token_ratio"],
            )

            # Lint
            violations = lint_ai_tokens.scan_file(candidate_path)
            if violations:
                stall(f"{len(violations)} lint violations")
                continue

            # Inject guards
            injected_path = gen_dir / "candidate.injected.cpp"
            try:
                _write_guarded(injected_path, candidate_path.read_text(encoding="utf-8"))
            except Exception as e:
                stall(f"inject failed: {e}")
                continue

            # Evaluate the candidate on the champion's seeds
            try:
                fitness_result = _evaluate(
                    injected_path,
                    guarded_opponent,
                    as_team=as_team,
                    n_matches=n_matches,
                    seed_base=seed_base,
                    result_path=gen_dir / "fitness_candidate.json",
                )
            except Exception as e:
                stall(f"fitness eval failed: {e}")
                continue

        team_fitness = _team_score(fitness_result.mean, as_team)

        # Accept candidate based on acceptance mode
        if acceptance_mode == "relative":
            # Accept if better than the champion on the same seeds (with
            # small epsilon for noise)
            accepted = team_fitness > (champion_fitness - 0.05)
            _LOG.info(
                "acceptance-check mode=relative champion=%.3f candidate=%.3f accepted=%s",
                champion_fitness,
                team_fitness,
                accepted,
            )
        else:
            # M22 behavior: absolute threshold
            accepted = team_fitness > 0.0
            _LOG.info(
                "acceptance-check mode=absolute candidate=%.3f accepted=%s",
                team_fitness,
                accepted,
            )

        if accepted:
            current_champion = result.cpp_code
            (out_dir / "champion.cpp").write_text(current_champion)

        # Write journal entry
        _write_journal_entry(
            journal_path=journal_path,
            generation=gen,
            fitness=team_fitness,
            aar_metrics=aar_metrics,
            tactic_spec=result.tactic_spec,
            accepted=accepted,
            model=model,
            seed=seed,
            strict_reflection=strict_reflection,
        )
        summary["candidate_fitness"] = team_fitness
        summary["accepted"] = accepted
        round_records.write_summary(gen_dir, summary)

        _LOG.info(
            "gen-complete gen=%d fitness=%.3f accepted=%s tokens=%d",
            gen,
            team_fitness,
            accepted,
            result.total_prompt_tokens + result.total_completion_tokens,
        )

    _LOG.info("evolve-dual-complete generations=%d", generations)
    return 0


def _team_score(mean_a: float, team: str) -> float:
    """Convert a Team-A-perspective fitness mean to ``team``'s perspective."""
    return mean_a if team == "A" else -mean_a


def _write_guarded(path: Path, source: str) -> None:
    """Write ``source`` with loop guards injected (idempotent)."""
    path.write_text(inject_guards.inject(source), encoding="utf-8")


def _evaluate(
    program_path: Path,
    opponent_path: Path,
    *,
    as_team: str,
    n_matches: int,
    seed_base: int,
    result_path: Path,
) -> fitness_mod.FitnessResult:
    """Play ``program_path`` (on ``as_team``'s side) against ``opponent_path``.

    Crashes and timeouts count as losses for the program under test. The
    full result, including per-match rows, is written to ``result_path``.
    """
    if as_team == "A":
        team_a_src, team_b_src = program_path, opponent_path
    else:
        team_a_src, team_b_src = opponent_path, program_path
    result = fitness_mod.evaluate_fitness(
        team_a_src,
        team_b_src,
        n_matches=n_matches,
        seed_base=seed_base,
        under_test=as_team,
    )
    result_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return result


def _slugify_tag(s: str) -> str:
    """Slugify a tag: lowercase, alphanum+underscore only, max 50 chars."""
    import re

    slug = re.sub(r"[^a-z0-9_]", "", s.lower().replace(" ", "_").replace("-", "_"))
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
    fitness: float,
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
        change.metric: change.target_value for change in tactic_spec.expected_changes
    }

    entry = {
        "generation": generation,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parent_generation": generation - 1 if generation > 0 else None,
        "track": "A",
        "model": model,
        "seed": seed,
        "status": "ok",
        "fitness": fitness,
        "fitness_delta": None,  # TODO: compute from parent
        "hypothesis_tested": f"{tactic_spec.tactic_name}: {tactic_spec.why_this_counters_failure[:150]}",
        "mechanism_expected": tactic_spec.mechanism[:400],  # Journal schema limit ~400
        "mechanism_observed": tactic_spec.why_this_counters_failure[:400],
        "verdict": verdict,
        "outcome_summary": f"{'accepted' if accepted else 'rejected'}: mean={fitness:.3f}",
        "advice_to_future_self": f"predicted metrics: {predicted_metrics}",
        "tactic_tags": [_slugify_tag(m) for m in tactic_spec.key_metrics[:6]]
        if tactic_spec.key_metrics
        else ["dual_llm"],
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
    parser.add_argument("--opponent", required=True, type=Path, help="Opponent AI source (.cpp)")
    parser.add_argument(
        "--as-team", choices=("A", "B"), default="A", help="Which team slot (default: A)"
    )
    parser.add_argument("--planner-model", default="claude-opus-4-7", help="Model for planner LLM")
    parser.add_argument("--coder-model", default="claude-haiku-4-5", help="Model for coder LLM")
    parser.add_argument("--generations", type=int, default=10, help="Number of generations")
    parser.add_argument("--n-matches", type=int, default=10, help="Matches per generation")
    parser.add_argument("--seed", type=int, default=42, help="Base seed")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    parser.add_argument(
        "--strict-reflection", action="store_true", help="Enable enhanced journal validation"
    )
    parser.add_argument(
        "--init-champion",
        type=Path,
        default=None,
        help="Path to initial champion C++ file (default: stationary_v1)",
    )
    parser.add_argument(
        "--acceptance-mode",
        choices=["absolute", "relative"],
        default="absolute",
        help="Acceptance criterion: absolute (>0.0) vs relative (>champion)",
    )
    parser.add_argument(
        "--inherit",
        choices=dual_llm.INHERIT_MODES,
        default="parent",
        help="parent: prompts carry the current champion; none: they do not",
    )
    parser.add_argument(
        "--feedback",
        choices=dual_llm.FEEDBACK_MODES,
        default="aar",
        help="aar: planner sees the AAR, last 3 lessons and champion numbers; none: nothing",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

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
        inherit=args.inherit,
        feedback=args.feedback,
    )


if __name__ == "__main__":
    sys.exit(main())
