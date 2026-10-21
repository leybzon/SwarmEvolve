#!/usr/bin/env python3
"""Co-evolution: both teams evolve competitively (Alternating Evolution).

Based on CO_EVOLUTION_PROPOSAL.md Option 1:
- Round N (even): Team A evolves against champion_b
- Round N (odd): Team B evolves against champion_a
- Each team has independent acceptance criteria
- Both champions tracked separately in journal
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
import tactic_spec as tspec  # noqa: E402
import telemetry_aar  # noqa: E402

REPO_ROOT = _HERE.parent

_LOG = logging.getLogger("swarmevolve.evolve_coevolve")


def evolve_coevolve(
    *,
    init_champion_a: Path,
    init_champion_b: Path,
    planner_model: str,
    coder_model: str,
    rounds: int,
    n_matches: int,
    seed: int,
    out_dir: Path,
    strict_reflection: bool = False,
    acceptance_mode: str = "relative",
) -> int:
    """Run co-evolutionary loop with alternating team evolution.

    Args:
        init_champion_a: Path to initial Team A champion
        init_champion_b: Path to initial Team B champion
        planner_model: Model for tactical planning
        coder_model: Model for implementation
        rounds: Number of rounds (each round evolves one team)
        n_matches: Matches per round for fitness evaluation
        seed: Base seed for RNG
        out_dir: Output directory for run artifacts
        strict_reflection: Enable enhanced journal validation
        acceptance_mode: "absolute" (>0.0) or "relative" (>champion)

    Returns:
        Exit code (0 = success)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    journal_path = out_dir / "journal.jsonl"

    # Build clients
    planner_client = llm_client.AnthropicClient(model=planner_model)
    coder_client = llm_client.AnthropicClient(model=coder_model)

    # Initialize champions
    if not init_champion_a.exists():
        _LOG.error("init-champion-a file not found: %s", init_champion_a)
        return 1
    if not init_champion_b.exists():
        _LOG.error("init-champion-b file not found: %s", init_champion_b)
        return 1

    champion_a = init_champion_a.read_text(encoding="utf-8")
    champion_b = init_champion_b.read_text(encoding="utf-8")
    champion_a_fitness = None  # Will be measured in round 0
    champion_b_fitness = None  # Will be measured in round 1

    _LOG.info("coevolve-start rounds=%d planner=%s coder=%s", rounds, planner_model, coder_model)
    _LOG.info("init-champion-a: %s", init_champion_a)
    _LOG.info("init-champion-b: %s", init_champion_b)

    for round_num in range(rounds):
        # Determine whose turn to evolve
        evolving_team = "A" if round_num % 2 == 0 else "B"
        static_team = "B" if evolving_team == "A" else "A"

        round_dir = out_dir / f"round_{round_num:04d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        _LOG.info("round round=%d evolving_team=%s", round_num, evolving_team)

        # Set opponent based on whose turn
        if evolving_team == "A":
            opponent_source = champion_b
            opponent_name = f"champion_b_r{round_num}"
            namespace = "TeamA"
        else:
            opponent_source = champion_a
            opponent_name = f"champion_a_r{round_num}"
            namespace = "TeamB"
        champion_code = champion_a if evolving_team == "A" else champion_b

        # Champion and candidate play the same seeds this round.
        seed_base = seed + round_num * 1000

        # Initialize variables
        aar_metrics = None

        # Re-measure the evolving team's champion against the current
        # opponent before generating a candidate. The opponent may have
        # changed since the champion was last scored, so the stored value
        # is stale; the trace of this run is also the AAR the planner sees.
        opponent_path = round_dir / f"opponent_{static_team}.cpp"
        champion_path = round_dir / f"champion_{evolving_team}.cpp"
        trace_path = round_dir / "trace_sample.jsonl"
        try:
            _write_guarded(opponent_path, opponent_source)
            _write_guarded(champion_path, champion_code)
            champion_result = _evaluate(
                champion_path,
                opponent_path,
                evolving_team=evolving_team,
                n_matches=n_matches,
                seed_base=seed_base,
                result_path=round_dir / "fitness_champion.json",
                trace_path=trace_path,
            )
        except Exception as e:
            _LOG.warning("champion-eval-failed round=%d err=%s", round_num, e)
            _write_stall_journal_entry(
                journal_path=journal_path,
                round_num=round_num,
                evolving_team=evolving_team,
                reason=f"champion eval failed: {e}",
                model=f"{planner_model}+{coder_model}",
                seed=seed,
            )
            continue
        champion_fitness = _team_score(champion_result.mean, evolving_team)
        if evolving_team == "A":
            champion_a_fitness = champion_fitness
        else:
            champion_b_fitness = champion_fitness

        # Special handling for round 0/1: skip LLM, evaluate init-champions
        skip_llm = (round_num == 0 and evolving_team == "A") or (
            round_num == 1 and evolving_team == "B"
        )

        if skip_llm:
            _LOG.info("init-round: evaluating init-champion for Team %s", evolving_team)
            candidate_path = round_dir / "candidate.cpp"
            current_code = champion_code
            candidate_path.write_text(current_code)

            # Create minimal metadata
            (round_dir / "planner_response.md").write_text(
                f"# Round {round_num}: Init Champion Evaluation (Team {evolving_team})\n\n"
                f"Using provided init-champion from: {init_champion_a if evolving_team == 'A' else init_champion_b}\n\n"
                "No planner call - directly evaluating champion code.\n"
            )
            (round_dir / "coder_response.md").write_text(
                f"# Round {round_num}: Init Champion Evaluation (Team {evolving_team})\n\n"
                "No coder call - using champion code as-is.\n"
            )

            # Create minimal tactic spec
            minimal_tactic_spec = tspec.TacticSpec(
                key_metrics=[],
                why_we_failed="N/A - evaluating provided init-champion",
                what_enemy_exploited="N/A",
                constraints_violated="N/A",
                tactic_name=f"Init Champion Team {evolving_team}",
                mechanism="Evaluating provided champion code directly (no LLM generation)",
                why_this_counters_failure="N/A - baseline evaluation",
                expected_changes=[],
                message_protocol="Inherited from init-champion",
                memory_layout="Inherited from init-champion",
                special_cases="None",
            )
            (round_dir / "tactic_spec.json").write_text(
                json.dumps(minimal_tactic_spec.to_dict(), indent=2) + "\n"
            )

            # Create mock result
            from types import SimpleNamespace

            result = SimpleNamespace(
                cpp_code=current_code,
                tactic_spec=minimal_tactic_spec,
                total_prompt_tokens=0,
                total_completion_tokens=0,
            )
            # Lint (init champions are linted like any candidate)
            violations = lint_ai_tokens.scan_file(candidate_path)
            if violations:
                _LOG.warning("lint-failed round=%d n_violations=%d", round_num, len(violations))
                _write_stall_journal_entry(
                    journal_path=journal_path,
                    round_num=round_num,
                    evolving_team=evolving_team,
                    reason=f"{len(violations)} lint violations",
                    model=f"{planner_model}+{coder_model}",
                    seed=seed,
                )
                continue

            # The candidate is the champion itself: reuse its measurement.
            fitness_result = champion_result

        else:
            # Normal path: Dual-LLM generation
            # Recall prior lessons from journal (only for this team)
            prior_lessons = "(none)"
            if journal_path.exists():
                entries = journal_mod.read_entries(journal_path)
                # Filter to this team's rounds
                team_entries = [e for e in entries if e.get("track") == evolving_team]
                if team_entries:
                    # Get recent lessons for this team
                    recent = team_entries[-3:]  # Last 3 rounds
                    prior_lessons = _format_lessons(recent)

            # AAR of the champion's match against the current opponent
            aar_markdown = "(none - first generation)"
            try:
                aar_report = telemetry_aar.render_aar(
                    trace_path,
                    perspective=evolving_team,
                    fmt="both",
                )
                aar_markdown = aar_report.markdown
                aar_metrics = aar_report.structured
            except Exception as e:
                _LOG.warning("aar-failed round=%d err=%s", round_num, e)

            prev_round = round_num - 2  # Last time this team evolved

            # Get context for iteration-aware prompting
            last_entry = None
            if journal_path.exists() and round_num > 0:
                entries = journal_mod.read_entries(journal_path)
                team_entries = [e for e in entries if e.get("track") == evolving_team]
                if team_entries:
                    last_entry = team_entries[-1]

            # Dual-LLM generation
            try:
                result = dual_llm.dual_llm_generate(
                    planner_client=planner_client,
                    coder_client=coder_client,
                    team_letter=evolving_team,
                    namespace=namespace,
                    opponent_name=opponent_name,
                    opponent_source=opponent_source,
                    parent_source=champion_code,
                    aar_markdown=aar_markdown,
                    prior_lessons=prior_lessons,
                    champion_fitness=champion_fitness,
                    last_generation=prev_round if prev_round >= 0 else None,
                    last_hypothesis=last_entry.get("hypothesis_tested") if last_entry else None,
                    last_fitness=last_entry.get("fitness") if last_entry else None,
                )
            except dual_llm.DualLLMError as e:
                _LOG.error("dual-llm-failed round=%d err=%s", round_num, e)
                _write_stall_journal_entry(
                    journal_path=journal_path,
                    round_num=round_num,
                    evolving_team=evolving_team,
                    reason=str(e),
                    model=f"{planner_model}+{coder_model}",
                    seed=seed,
                )
                continue

            # Save artifacts
            tactic_spec_path = round_dir / "tactic_spec.json"
            tactic_spec_path.write_text(json.dumps(result.tactic_spec.to_dict(), indent=2) + "\n")
            (round_dir / "planner_response.md").write_text(result.planner_response.text)
            (round_dir / "coder_response.md").write_text(result.coder_response.text)

            candidate_path = round_dir / "candidate.cpp"
            candidate_path.write_text(result.cpp_code)

            # Lint
            violations = lint_ai_tokens.scan_file(candidate_path)
            if violations:
                _LOG.warning("lint-failed round=%d n_violations=%d", round_num, len(violations))
                _write_stall_journal_entry(
                    journal_path=journal_path,
                    round_num=round_num,
                    evolving_team=evolving_team,
                    reason=f"{len(violations)} lint violations",
                    model=f"{planner_model}+{coder_model}",
                    seed=seed,
                )
                continue

            # Inject guards
            injected_path = round_dir / "candidate.injected.cpp"
            try:
                _write_guarded(injected_path, candidate_path.read_text(encoding="utf-8"))
            except Exception as e:
                _LOG.warning("inject-failed round=%d err=%s", round_num, e)
                _write_stall_journal_entry(
                    journal_path=journal_path,
                    round_num=round_num,
                    evolving_team=evolving_team,
                    reason=f"inject failed: {e}",
                    model=f"{planner_model}+{coder_model}",
                    seed=seed,
                )
                continue

            # Evaluate the candidate on the champion's seeds
            try:
                fitness_result = _evaluate(
                    injected_path,
                    opponent_path,
                    evolving_team=evolving_team,
                    n_matches=n_matches,
                    seed_base=seed_base,
                    result_path=round_dir / "fitness_candidate.json",
                )
            except Exception as e:
                _LOG.warning("fitness-failed round=%d err=%s", round_num, e)
                _write_stall_journal_entry(
                    journal_path=journal_path,
                    round_num=round_num,
                    evolving_team=evolving_team,
                    reason=f"fitness eval failed: {e}",
                    model=f"{planner_model}+{coder_model}",
                    seed=seed,
                )
                continue

        team_fitness = _team_score(fitness_result.mean, evolving_team)

        # Accept candidate based on acceptance mode
        if acceptance_mode == "relative":
            # Accept if better than the freshly measured champion (with small
            # epsilon for noise)
            accepted = team_fitness > (champion_fitness - 0.05)
            _LOG.info(
                "acceptance-check mode=relative champion=%.3f candidate=%.3f team=%s accepted=%s",
                champion_fitness,
                team_fitness,
                evolving_team,
                accepted,
            )
        else:
            accepted = team_fitness > 0.0
            _LOG.info(
                "acceptance-check mode=absolute candidate=%.3f team=%s accepted=%s",
                team_fitness,
                evolving_team,
                accepted,
            )

        # Update champion if accepted
        if accepted:
            if evolving_team == "A":
                champion_a = result.cpp_code
                champion_a_fitness = team_fitness
                (out_dir / "champion_a.cpp").write_text(champion_a)
            else:
                champion_b = result.cpp_code
                champion_b_fitness = team_fitness
                (out_dir / "champion_b.cpp").write_text(champion_b)

        # Write journal entry
        _write_journal_entry(
            journal_path=journal_path,
            round_num=round_num,
            evolving_team=evolving_team,
            fitness=team_fitness,
            fitness_result=fitness_result,
            aar_metrics=aar_metrics,
            tactic_spec=result.tactic_spec,
            accepted=accepted,
            model=f"{planner_model}+{coder_model}",
            seed=seed,
            strict_reflection=strict_reflection,
        )

        _LOG.info(
            "round-complete round=%d team=%s fitness=%.3f accepted=%s tokens=%d",
            round_num,
            evolving_team,
            team_fitness,
            accepted,
            result.total_prompt_tokens + result.total_completion_tokens,
        )

    _LOG.info("coevolve-complete rounds=%d", rounds)
    _LOG.info("final-champion-a-fitness=%.3f", champion_a_fitness or -1.0)
    _LOG.info("final-champion-b-fitness=%.3f", champion_b_fitness or -1.0)
    return 0


def _team_score(mean_a: float, team: str) -> float:
    """Convert a Team-A-perspective fitness mean to team's perspective."""
    return mean_a if team == "A" else -mean_a


def _write_guarded(path: Path, source: str) -> None:
    """Write source with loop guards injected (idempotent)."""
    path.write_text(inject_guards.inject(source), encoding="utf-8")


def _evaluate(
    program_path: Path,
    opponent_path: Path,
    *,
    evolving_team: str,
    n_matches: int,
    seed_base: int,
    result_path: Path,
    trace_path: Path | None = None,
) -> fitness_mod.FitnessResult:
    """Play program_path (on evolving_team's side) against opponent_path.

    Crashes and timeouts count as losses for the program under test. The
    full result, including per-match rows, is written to result_path.
    With trace_path the first match also records a schema-v2 trace.
    """
    if evolving_team == "A":
        team_a_src, team_b_src = program_path, opponent_path
    else:
        team_a_src, team_b_src = opponent_path, program_path
    result = fitness_mod.evaluate_fitness(
        team_a_src,
        team_b_src,
        n_matches=n_matches,
        seed_base=seed_base,
        under_test=evolving_team,
        record_trace_for_match_idx=0 if trace_path is not None else None,
        trace_output_path=trace_path,
    )
    result_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return result


def _format_lessons(entries: list[dict]) -> str:
    """Format journal entries as lessons learned."""
    if not entries:
        return "(none)"

    lines = []
    for e in entries:
        gen = e.get("generation", "?")
        fitness = e.get("fitness", 0.0)
        verdict = e.get("verdict", "unknown")
        hypothesis = e.get("hypothesis_tested", "unknown")
        lines.append(f"Gen {gen}: {verdict} (fitness={fitness:.3f}) - {hypothesis}")

    return "\n".join(lines)


def _slugify_tag(s: str) -> str:
    """Slugify a tag: lowercase, alphanum+underscore only, max 50 chars."""
    import re

    slug = re.sub(r"[^a-z0-9_]", "", s.lower().replace(" ", "_").replace("-", "_"))
    return slug[:50]


def _write_stall_journal_entry(
    journal_path: Path,
    round_num: int,
    evolving_team: str,
    reason: str,
    model: str,
    seed: int,
) -> None:
    """Write a stall entry when round fails."""
    entry = {
        "generation": round_num,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parent_generation": round_num - 2
        if round_num >= 2
        else None,  # Last time this team evolved
        "track": evolving_team,
        "model": model,
        "seed": seed,
        "status": "stalled",
        "fitness": None,
        "fitness_delta": None,
        "hypothesis_tested": f"round failed: {reason}",
        "mechanism_expected": "",
        "mechanism_observed": reason,
        "verdict": "stalled",
        "outcome_summary": f"stalled at round {round_num} (team {evolving_team})",
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
    round_num: int,
    evolving_team: str,
    fitness: float,
    fitness_result,
    aar_metrics: dict | None,
    tactic_spec,
    accepted: bool,
    model: str,
    seed: int,
    strict_reflection: bool,
) -> None:
    """Write journal entry for co-evolution round."""
    verdict = "confirmed" if accepted else "rejected"

    # Extract predicted changes from tactic spec
    predicted_metrics = {
        change.metric: change.target_value for change in tactic_spec.expected_changes
    }

    entry = {
        "generation": round_num,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parent_generation": round_num - 2 if round_num >= 2 else None,
        "track": evolving_team,
        "model": model,
        "seed": seed,
        "status": "ok",
        "fitness": fitness,
        "fitness_delta": None,  # TODO: compute from parent
        "hypothesis_tested": f"{tactic_spec.tactic_name}: {tactic_spec.why_this_counters_failure[:150]}",
        "mechanism_expected": tactic_spec.mechanism[:400],
        "mechanism_observed": tactic_spec.why_this_counters_failure[:400],
        "verdict": verdict,
        "outcome_summary": f"{'accepted' if accepted else 'rejected'}: fitness={fitness:.3f} (team {evolving_team})",
        "advice_to_future_self": f"predicted metrics: {predicted_metrics}",
        "tactic_tags": [_slugify_tag(m) for m in tactic_spec.key_metrics[:6]]
        if tactic_spec.key_metrics
        else ["coevolve"],
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
        _LOG.warning("journal-validation-failed round=%d errors=%s", round_num, result.errors)
        # Write anyway but mark validation failure
        entry["validation"]["schema_valid"] = result.schema_valid
        entry["validation"]["metrics_match_aar"] = result.metrics_match_aar
        journal_mod._write_line(journal_path, entry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Co-evolution: both teams evolve competitively (Alternating Evolution)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--init-champion-a", required=True, type=Path, help="Initial champion for Team A (.cpp)"
    )
    parser.add_argument(
        "--init-champion-b", required=True, type=Path, help="Initial champion for Team B (.cpp)"
    )
    parser.add_argument(
        "--planner-model", default="claude-sonnet-4-20250514", help="Model for planner LLM"
    )
    parser.add_argument("--coder-model", default="claude-haiku-4-5", help="Model for coder LLM")
    parser.add_argument(
        "--rounds", type=int, default=10, help="Number of rounds (each round evolves one team)"
    )
    parser.add_argument("--n-matches", type=int, default=10, help="Matches per round")
    parser.add_argument("--seed", type=int, default=42, help="Base seed")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    parser.add_argument(
        "--strict-reflection", action="store_true", help="Enable enhanced journal validation"
    )
    parser.add_argument(
        "--acceptance-mode",
        choices=["absolute", "relative"],
        default="relative",
        help="Acceptance criterion: absolute (>0.0) vs relative (>champion)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    return evolve_coevolve(
        init_champion_a=args.init_champion_a,
        init_champion_b=args.init_champion_b,
        planner_model=args.planner_model,
        coder_model=args.coder_model,
        rounds=args.rounds,
        n_matches=args.n_matches,
        seed=args.seed,
        out_dir=args.out_dir,
        strict_reflection=args.strict_reflection,
        acceptance_mode=args.acceptance_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
