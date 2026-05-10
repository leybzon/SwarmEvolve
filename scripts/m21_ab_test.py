#!/usr/bin/env python3
"""M21 A/B/C Test Harness - Compare reflection quality across 4 conditions.

Runs:
- Baseline: current evolve.py prompt, single LLM
- Condition A: enhanced structured prompt, single LLM
- Condition B: dual-LLM (Opus planner + Haiku coder)
- Condition C: enhanced prompt + strict journal validation

Each condition runs 3 seeds × 30 generations = 90 generations per condition
Total: 4 conditions × 90 = 360 generations

Outputs m21_results.csv with reflection scores and fitness per lineage.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent

_LOG = logging.getLogger("swarmevolve.m21_ab_test")


@dataclass
class Condition:
    """Test condition configuration."""

    name: str
    description: str
    script: str  # Which script to run
    extra_args: list[str]  # Additional args beyond common


# Define test conditions
CONDITIONS = [
    Condition(
        name="baseline",
        description="Current evolve.py with default prompt",
        script="evolve.py",
        extra_args=[],
    ),
    Condition(
        name="enhanced_prompt",
        description="Enhanced structured prompt (OODA loop)",
        script="evolve.py",
        extra_args=["--prompt", str(REPO_ROOT / "prompts" / "evolve_ai_v2_structured.md")],
    ),
    Condition(
        name="dual_llm",
        description="Dual-LLM (Opus planner + Haiku coder)",
        script="evolve_dual.py",
        extra_args=[
            "--planner-model",
            "claude-opus-4-7",
            "--coder-model",
            "claude-haiku-4-5",
        ],
    ),
    Condition(
        name="enhanced_strict",
        description="Enhanced prompt + strict journal validation",
        script="evolve.py",
        extra_args=[
            "--prompt",
            str(REPO_ROOT / "prompts" / "evolve_ai_v2_structured.md"),
            "--strict-reflection",
        ],
    ),
]


def run_lineage(
    *,
    condition: Condition,
    seed: int,
    opponent: Path,
    generations: int,
    n_matches: int,
    out_dir: Path,
) -> dict:
    """Run one lineage (condition × seed)."""
    lineage_dir = out_dir / condition.name / f"seed{seed}"
    lineage_dir.mkdir(parents=True, exist_ok=True)

    _LOG.info(
        "lineage-start condition=%s seed=%d generations=%d",
        condition.name,
        seed,
        generations,
    )

    # Build command
    script_path = _HERE / condition.script
    cmd = [
        sys.executable,
        str(script_path),
        "--opponent",
        str(opponent),
        "--as-team",
        "A",
        "--generations",
        str(generations),
        "--n-matches",
        str(n_matches),
        "--seed",
        str(seed),
        "--out-dir",
        str(lineage_dir),
        *condition.extra_args,
    ]

    # Run
    start_time = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600 * 4,  # 4 hour timeout
        )
        success = result.returncode == 0
        error = result.stderr if not success else None
    except subprocess.TimeoutExpired:
        success = False
        error = "timeout after 4 hours"
    except Exception as e:
        success = False
        error = str(e)

    end_time = datetime.now()
    wall_seconds = (end_time - start_time).total_seconds()

    # Read journal to get reflection scores (placeholder - would use M17 scorer)
    journal_path = lineage_dir / "journal.jsonl"
    reflection_scores = []
    if journal_path.exists():
        try:
            with journal_path.open() as f:
                for line in f:
                    json.loads(line)
                    # TODO: Score reflection with M17 rubric scorer
                    # For now, use placeholder
                    reflection_scores.append(3.0)  # Placeholder
        except Exception as e:
            _LOG.warning("failed to read journal: %s", e)

    median_reflection = (
        sorted(reflection_scores)[len(reflection_scores) // 2] if reflection_scores else None
    )

    # Read final fitness
    final_fitness = None
    champion_path = lineage_dir / "champion.cpp"
    if champion_path.exists():
        # TODO: Evaluate final champion
        final_fitness = 0.5  # Placeholder

    _LOG.info(
        "lineage-complete condition=%s seed=%d success=%s wall_s=%.1f",
        condition.name,
        seed,
        success,
        wall_seconds,
    )

    return {
        "condition": condition.name,
        "seed": seed,
        "success": success,
        "error": error,
        "wall_seconds": wall_seconds,
        "generations_completed": len(reflection_scores),
        "median_reflection_score": median_reflection,
        "final_fitness": final_fitness,
    }


def run_ab_test(
    *,
    conditions: list[str],
    seeds: list[int],
    opponent: Path,
    generations: int,
    n_matches: int,
    out_dir: Path,
    parallel: bool = False,
) -> list[dict]:
    """Run full A/B/C test across all conditions and seeds."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filter conditions
    conds = [c for c in CONDITIONS if c.name in conditions]

    results = []
    for cond in conds:
        for seed in seeds:
            result = run_lineage(
                condition=cond,
                seed=seed,
                opponent=opponent,
                generations=generations,
                n_matches=n_matches,
                out_dir=out_dir,
            )
            results.append(result)

    # Write results CSV
    results_csv = out_dir / "m21_results.csv"
    with results_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "condition",
                "seed",
                "success",
                "error",
                "wall_seconds",
                "generations_completed",
                "median_reflection_score",
                "final_fitness",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    _LOG.info("results written to %s", results_csv)

    # Generate summary report
    _generate_report(results, out_dir / "m21_report.md")

    return results


def _generate_report(results: list[dict], report_path: Path) -> None:
    """Generate M21 analysis report."""
    # Group by condition
    by_condition = {}
    for r in results:
        cond = r["condition"]
        if cond not in by_condition:
            by_condition[cond] = []
        by_condition[cond].append(r)

    # Compute stats
    report_lines = [
        "# M21 A/B/C Test Results",
        "",
        f"**Date:** {datetime.now().isoformat()}",
        "",
        "## Summary by Condition",
        "",
    ]

    for cond_name, cond_results in by_condition.items():
        successful = [r for r in cond_results if r["success"]]
        reflection_scores = [
            r["median_reflection_score"]
            for r in successful
            if r["median_reflection_score"] is not None
        ]

        median_reflection = (
            sorted(reflection_scores)[len(reflection_scores) // 2] if reflection_scores else None
        )

        report_lines.append(f"### {cond_name}")
        report_lines.append(f"- Lineages: {len(cond_results)}")
        report_lines.append(f"- Successful: {len(successful)} / {len(cond_results)}")
        report_lines.append(
            f"- Median reflection score: {median_reflection:.2f}"
            if median_reflection
            else "- Median reflection score: N/A"
        )
        report_lines.append("")

    # Decision
    report_lines.append("## Decision")
    report_lines.append("")
    best_cond = max(
        by_condition.items(),
        key=lambda x: (
            sorted([r["median_reflection_score"] or 0 for r in x[1] if r["success"]])[
                len([r for r in x[1] if r["success"]]) // 2
            ]
            if [r for r in x[1] if r["success"]]
            else 0
        ),
    )
    report_lines.append(f"**Best condition:** {best_cond[0]}")
    report_lines.append("")

    report_path.write_text("\n".join(report_lines))
    _LOG.info("report written to %s", report_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M21 A/B/C test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--conditions",
        default="baseline,enhanced_prompt,dual_llm,enhanced_strict",
        help="Comma-separated condition names",
    )
    parser.add_argument(
        "--seeds",
        default="1,2,3",
        help="Comma-separated seeds",
    )
    parser.add_argument(
        "--opponent",
        type=Path,
        default=REPO_ROOT / "src" / "baselines" / "pursuit_v1.cpp",
        help="Opponent AI",
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=30,
        help="Generations per lineage",
    )
    parser.add_argument(
        "--n-matches",
        type=int,
        default=10,
        help="Matches per generation",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "data" / "runs" / "m21_ab_test",
        help="Output directory",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run lineages in parallel (NOT IMPLEMENTED)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    conditions = [c.strip() for c in args.conditions.split(",")]
    seeds = [int(s.strip()) for s in args.seeds.split(",")]

    _LOG.info(
        "m21-ab-test-start conditions=%s seeds=%s generations=%d",
        conditions,
        seeds,
        args.generations,
    )

    results = run_ab_test(
        conditions=conditions,
        seeds=seeds,
        opponent=args.opponent,
        generations=args.generations,
        n_matches=args.n_matches,
        out_dir=args.out_dir,
        parallel=args.parallel,
    )

    successful = sum(1 for r in results if r["success"])
    _LOG.info(
        "m21-ab-test-complete total=%d successful=%d",
        len(results),
        successful,
    )

    return 0 if successful == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
