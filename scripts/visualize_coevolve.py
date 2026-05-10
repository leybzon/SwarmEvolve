#!/usr/bin/env python3
"""Generate visualizations for co-evolution experiments.

Creates publication-quality figures showing competitive evolution dynamics.
"""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def load_journal(journal_path: Path) -> tuple[list, list]:
    """Load journal and split by team."""
    with open(journal_path) as f:
        entries = [json.loads(line) for line in f]

    team_a = [e for e in entries if e["track"] == "A"]
    team_b = [e for e in entries if e["track"] == "B"]

    return team_a, team_b


def plot_arms_race_timeline(team_a: list, team_b: list, out_path: Path):
    """Figure 1: Arms Race Timeline - dual-line fitness evolution."""
    _fig, ax = plt.subplots(figsize=(12, 6))

    # Extract data
    a_rounds = [e["generation"] for e in team_a]
    a_fitness = [e["fitness"] for e in team_a]
    a_accepted = [e["generation"] for e in team_a if e["verdict"] == "confirmed"]
    a_accepted_fit = [e["fitness"] for e in team_a if e["verdict"] == "confirmed"]

    b_rounds = [e["generation"] for e in team_b]
    b_fitness = [e["fitness"] for e in team_b]
    b_accepted = [e["generation"] for e in team_b if e["verdict"] == "confirmed"]
    b_accepted_fit = [e["fitness"] for e in team_b if e["verdict"] == "confirmed"]

    # Plot all attempts (light lines)
    ax.plot(
        a_rounds,
        a_fitness,
        "o-",
        color="#4A90E2",
        alpha=0.3,
        linewidth=1,
        markersize=3,
        label="Team A attempts",
    )
    ax.plot(
        b_rounds,
        b_fitness,
        "o-",
        color="#E24A4A",
        alpha=0.3,
        linewidth=1,
        markersize=3,
        label="Team B attempts",
    )

    # Plot accepted champions (bold markers)
    ax.scatter(
        a_accepted,
        a_accepted_fit,
        s=100,
        marker="o",
        color="#4A90E2",
        edgecolor="black",
        linewidth=1.5,
        zorder=10,
        label="Team A accepted",
    )
    ax.scatter(
        b_accepted,
        b_accepted_fit,
        s=100,
        marker="o",
        color="#E24A4A",
        edgecolor="black",
        linewidth=1.5,
        zorder=10,
        label="Team B accepted",
    )

    # Annotate key breakthroughs
    if b_accepted and max(b_accepted_fit) > 0.5:
        breakthrough_idx = b_accepted_fit.index(max(b_accepted_fit))
        breakthrough_round = b_accepted[breakthrough_idx]
        breakthrough_fit = b_accepted_fit[breakthrough_idx]
        ax.annotate(
            f"Team B Breakthrough\nRound {breakthrough_round}\nFitness: {breakthrough_fit:+.2f}",
            xy=(breakthrough_round, breakthrough_fit),
            xytext=(breakthrough_round + 10, breakthrough_fit - 0.3),
            fontsize=10,
            bbox=dict(
                boxstyle="round,pad=0.5", facecolor="#FFE5E5", edgecolor="#E24A4A", linewidth=2
            ),
            arrowprops=dict(arrowstyle="->", color="#E24A4A", linewidth=2),
        )

    # Zero line
    ax.axhline(0, color="gray", linestyle="--", linewidth=1, alpha=0.5)

    # Styling
    ax.set_xlabel("Round Number", fontsize=12, fontweight="bold")
    ax.set_ylabel("Fitness (Team A Perspective)", fontsize=12, fontweight="bold")
    ax.set_title(
        "M25 Co-Evolution: Competitive Arms Race\n100 Rounds, Alternating Team Evolution",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.grid(True, alpha=0.3, linestyle=":", linewidth=0.5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
    ax.set_ylim(-1.1, 1.1)

    # Add phase annotations
    ax.axvspan(0, 10, alpha=0.1, color="blue", label="_Phase 1: Initial")
    ax.text(5, -1.05, "Phase 1:\nBaseline", ha="center", fontsize=8, style="italic", alpha=0.7)
    ax.axvspan(10, 35, alpha=0.1, color="yellow", label="_Phase 2: Evolution")
    ax.text(
        22.5,
        -1.05,
        "Phase 2:\nTeam B Evolution",
        ha="center",
        fontsize=8,
        style="italic",
        alpha=0.7,
    )
    ax.axvspan(35, 100, alpha=0.1, color="green", label="_Phase 3: Plateau")
    ax.text(67.5, -1.05, "Phase 3:\nPlateau", ha="center", fontsize=8, style="italic", alpha=0.7)

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def plot_champion_staircase(team_a: list, team_b: list, out_path: Path):
    """Figure 2: Champion Evolution Staircase - step plot of champion fitness."""
    _fig, ax = plt.subplots(figsize=(12, 6))

    # Extract accepted champions
    a_accepted = [(e["generation"], e["fitness"]) for e in team_a if e["verdict"] == "confirmed"]
    b_accepted = [(e["generation"], e["fitness"]) for e in team_b if e["verdict"] == "confirmed"]

    # Build staircase data
    max_round = max(team_a[-1]["generation"], team_b[-1]["generation"])

    def build_staircase(accepted_list, max_round):
        """Convert accepted list to staircase coordinates."""
        if not accepted_list:
            return [], []

        rounds = [0]
        fitness = [accepted_list[0][1]]  # First champion

        for i, (r, f) in enumerate(accepted_list):
            if i > 0:
                # Horizontal line from previous to this round
                rounds.append(r)
                fitness.append(fitness[-1])
            # Step up/down to new fitness
            rounds.append(r)
            fitness.append(f)

        # Extend to end
        rounds.append(max_round)
        fitness.append(fitness[-1])

        return rounds, fitness

    a_rounds, a_fit = build_staircase(a_accepted, max_round)
    b_rounds, b_fit = build_staircase(b_accepted, max_round)

    # Plot staircases
    ax.step(
        a_rounds,
        a_fit,
        where="post",
        linewidth=3,
        color="#4A90E2",
        label="Team A Champion",
        zorder=5,
    )
    ax.step(
        b_rounds,
        b_fit,
        where="post",
        linewidth=3,
        color="#E24A4A",
        label="Team B Champion",
        zorder=5,
    )

    # Mark transitions
    for r, f in a_accepted[1:]:  # Skip first
        ax.scatter(
            [r],
            [f],
            s=150,
            marker="D",
            color="#4A90E2",
            edgecolor="black",
            linewidth=1.5,
            zorder=10,
        )
    for r, f in b_accepted[1:]:
        ax.scatter(
            [r],
            [f],
            s=150,
            marker="D",
            color="#E24A4A",
            edgecolor="black",
            linewidth=1.5,
            zorder=10,
        )

    # Zero line
    ax.axhline(0, color="gray", linestyle="--", linewidth=1, alpha=0.5, label="Draw Parity")

    # Annotate Team B progression
    if len(b_accepted) > 1:
        for i, (r, f) in enumerate(b_accepted):
            if i == 0:
                label_text = f"Init: {f:+.2f}"
            elif i == len(b_accepted) - 1:
                label_text = f"Final: {f:+.2f}"
            else:
                label_text = f"{f:+.2f}"

            ax.annotate(
                label_text,
                xy=(r, f),
                xytext=(5, 10),
                textcoords="offset points",
                fontsize=8,
                bbox=dict(
                    boxstyle="round,pad=0.3", facecolor="white", edgecolor="#E24A4A", alpha=0.8
                ),
            )

    # Styling
    ax.set_xlabel("Round Number", fontsize=12, fontweight="bold")
    ax.set_ylabel("Champion Fitness", fontsize=12, fontweight="bold")
    ax.set_title(
        "M25 Co-Evolution: Champion Progression\nStep Function Shows Only Accepted Champions",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.grid(True, alpha=0.3, linestyle=":", linewidth=0.5)
    ax.legend(loc="best", fontsize=10, framealpha=0.9)
    ax.set_ylim(-1.1, 1.1)

    # Add improvement arrows for Team B
    if len(b_accepted) > 1:
        for i in range(len(b_accepted) - 1):
            r1, f1 = b_accepted[i]
            r2, f2 = b_accepted[i + 1]
            delta = f2 - f1
            if abs(delta) > 0.05:  # Only show significant changes
                (r1 + r2) / 2
                ax.annotate(
                    "",
                    xy=(r2, f2),
                    xytext=(r1, f1),
                    arrowprops=dict(arrowstyle="->", color="#E24A4A", linewidth=2, alpha=0.5),
                    zorder=1,
                )

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def plot_red_queen_effect(team_a: list, team_b: list, out_path: Path):
    """Figure 3: Red Queen Effect - relative fitness advantage over time."""
    _fig, ax = plt.subplots(figsize=(12, 6))

    # Build paired data (align by round)
    all_rounds = sorted(set([e["generation"] for e in team_a + team_b]))

    # Get champion fitness at each round
    a_champion_fitness = {}
    b_champion_fitness = {}

    current_a = None
    current_b = None

    for e in sorted(team_a + team_b, key=lambda x: x["generation"]):
        round_num = e["generation"]
        if e["track"] == "A" and e["verdict"] == "confirmed":
            current_a = e["fitness"]
        elif e["track"] == "B" and e["verdict"] == "confirmed":
            current_b = e["fitness"]

        if current_a is not None:
            a_champion_fitness[round_num] = current_a
        if current_b is not None:
            b_champion_fitness[round_num] = current_b

    # Calculate advantage (Team A - Team B) from Team A's perspective
    rounds = []
    advantage = []

    for r in all_rounds:
        if r in a_champion_fitness and r in b_champion_fitness:
            rounds.append(r)
            # Team A advantage = Team A fitness - (-Team B fitness)
            # Since Team B fitness is from their perspective (flipped)
            a_fit = a_champion_fitness[r]
            b_fit = b_champion_fitness[r]
            # Advantage = how much better is A than B?
            # If A=+1.0 and B=-0.8 (B loses), then advantage = 1.0 - (-0.8) = 1.8
            advantage.append(a_fit - b_fit)

    # Plot area chart
    ax.fill_between(
        rounds,
        advantage,
        0,
        where=np.array(advantage) >= 0,
        color="#4A90E2",
        alpha=0.3,
        label="Team A Advantage",
        interpolate=True,
    )
    ax.fill_between(
        rounds,
        advantage,
        0,
        where=np.array(advantage) < 0,
        color="#E24A4A",
        alpha=0.3,
        label="Team B Advantage",
        interpolate=True,
    )

    # Plot line
    ax.plot(rounds, advantage, color="black", linewidth=2, zorder=5)

    # Zero line (perfect balance)
    ax.axhline(
        0, color="gray", linestyle="--", linewidth=2, alpha=0.7, label="Perfect Balance (Draw)"
    )

    # Annotate key transitions
    if len(advantage) > 0:
        # Find where it crosses zero
        for i in range(len(advantage) - 1):
            if (advantage[i] > 0 and advantage[i + 1] < 0) or (
                advantage[i] < 0 and advantage[i + 1] > 0
            ):
                cross_round = rounds[i + 1]
                ax.axvline(cross_round, color="orange", linestyle=":", linewidth=2, alpha=0.7)
                ax.annotate(
                    f"Balance Shift\n(Round {cross_round})",
                    xy=(cross_round, 0),
                    xytext=(cross_round + 5, 0.5),
                    fontsize=9,
                    color="orange",
                    fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color="orange", linewidth=1.5),
                )

    # Styling
    ax.set_xlabel("Round Number", fontsize=12, fontweight="bold")
    ax.set_ylabel("Fitness Advantage (Team A - Team B)", fontsize=12, fontweight="bold")
    ax.set_title(
        "M25 Co-Evolution: Red Queen Effect\nCompetitive Balance Over Time",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.grid(True, alpha=0.3, linestyle=":", linewidth=0.5)
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)

    # Add contextual text
    if len(advantage) > 0:
        initial_adv = advantage[0]
        final_adv = advantage[-1]
        shift = final_adv - initial_adv

        textstr = f"Initial Advantage: {initial_adv:+.2f}\n"
        textstr += f"Final Advantage: {final_adv:+.2f}\n"
        textstr += f"Total Shift: {shift:+.2f}"

        props = dict(boxstyle="round", facecolor="wheat", alpha=0.8)
        ax.text(
            0.02,
            0.98,
            textstr,
            transform=ax.transAxes,
            fontsize=10,
            verticalalignment="top",
            bbox=props,
            family="monospace",
        )

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_coevolve.py <journal_path> [output_dir]")
        print("Example: python visualize_coevolve.py data/runs/m25_coevolve_100r/journal.jsonl")
        sys.exit(1)

    journal_path = Path(sys.argv[1])
    if not journal_path.exists():
        print(f"Error: Journal not found at {journal_path}")
        sys.exit(1)

    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else journal_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading journal: {journal_path}")
    team_a, team_b = load_journal(journal_path)
    print(f"  Team A: {len(team_a)} rounds")
    print(f"  Team B: {len(team_b)} rounds")
    print()

    print("Generating visualizations...")

    # Generate top 3 figures
    plot_arms_race_timeline(team_a, team_b, output_dir / "fig1_arms_race_timeline.png")
    plot_champion_staircase(team_a, team_b, output_dir / "fig2_champion_staircase.png")
    plot_red_queen_effect(team_a, team_b, output_dir / "fig3_red_queen_effect.png")

    print()
    print(f"✓ All visualizations saved to: {output_dir}")
    print()
    print("Generated files:")
    print("  1. fig1_arms_race_timeline.png  - Dual-line fitness evolution")
    print("  2. fig2_champion_staircase.png  - Champion progression steps")
    print("  3. fig3_red_queen_effect.png    - Competitive balance shifts")


if __name__ == "__main__":
    main()
