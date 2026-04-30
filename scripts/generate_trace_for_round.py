#!/usr/bin/env python3
"""Generate a single trace file for a specific round from M25 co-evolution experiment."""

import argparse
import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

import fitness as fitness_mod


def main():
    parser = argparse.ArgumentParser(description="Generate trace for a specific M25 round")
    parser.add_argument("--round", type=int, required=True, help="Round number (e.g., 1, 13, 31, 41)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for M25 experiment")
    parser.add_argument("--trace-output", type=Path, required=True, help="Path for output trace file")
    args = parser.parse_args()

    round_dir = args.out_dir / f"round_{args.round:04d}"
    if not round_dir.exists():
        print(f"ERROR: Round directory not found: {round_dir}")
        return 1

    # Find the candidate and opponent files
    candidate_injected = round_dir / "candidate.injected.cpp"
    opponent_a = round_dir / "opponent_A.cpp"

    if not candidate_injected.exists():
        print(f"ERROR: candidate.injected.cpp not found in {round_dir}")
        return 1
    if not opponent_a.exists():
        print(f"ERROR: opponent_A.cpp not found in {round_dir}")
        return 1

    print(f"Generating trace for Round {args.round}...")
    print(f"  Team B: {candidate_injected}")
    print(f"  Team A: {opponent_a}")
    print(f"  Seed: {args.seed}")
    print(f"  Output: {args.trace_output}")

    # Run match with trace recording
    result = fitness_mod.evaluate_fitness(
        candidate_path=candidate_injected,
        opponent_path=opponent_a,
        candidate_team="B",  # Round numbers determine which team evolved
        n_matches=1,
        seed_base=args.seed,
        record_trace_for_match_idx=0,
        trace_output_path=args.trace_output,
    )

    print(f"✓ Match completed: fitness={result.mean:+.3f} (Team B perspective)")
    print(f"✓ Trace saved to: {args.trace_output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
