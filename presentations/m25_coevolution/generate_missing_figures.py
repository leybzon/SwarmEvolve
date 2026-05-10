#!/usr/bin/env python3
"""
Generate missing figures for M25 presentation.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

COLORS = {
    "team_a_blue": "#3b82f6",
    "team_b_red": "#ef4444",
    "breakthrough_green": "#22c55e",
    "highlight_gold": "#f59e0b",
    "warning_magenta": "#ec4899",
    "bg_dark": "#0a1929",
    "text_primary": "#e2e8f0",
    "text_secondary": "#94a3b8",
}

FIGURES_DIR = Path("figures")
FIGURES_DIR.mkdir(exist_ok=True)

plt.style.use("dark_background")


def set_dark_style(ax, fig=None):
    """Apply dark theme styling"""
    if fig:
        fig.patch.set_facecolor(COLORS["bg_dark"])
    ax.set_facecolor(COLORS["bg_dark"])
    ax.spines["bottom"].set_color(COLORS["text_secondary"])
    ax.spines["top"].set_color(COLORS["text_secondary"])
    ax.spines["right"].set_color(COLORS["text_secondary"])
    ax.spines["left"].set_color(COLORS["text_secondary"])
    ax.tick_params(colors=COLORS["text_secondary"], labelsize=12)


def create_fitness_timeline():
    """Create fitness timeline showing Team A vs Team B over rounds"""
    fig, ax = plt.subplots(figsize=(16, 9), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    rounds = np.array([1, 5, 10, 13, 20, 25, 31, 35, 41, 50, 60, 70, 80, 90, 95])

    # Team A fitness (declining from 1.0 to ~0.5)
    team_a_fitness = np.array(
        [1.0, 0.95, 0.9, 0.8, 0.75, 0.7, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25, 0.2, 0.15, 0.1]
    )

    # Team B fitness (rising from -0.8 to +0.9)
    team_b_fitness = np.array(
        [-0.8, -0.7, -0.5, 0.0, 0.2, 0.5, 0.9, 0.85, 0.9, 0.85, 0.8, 0.75, 0.7, 0.8, 0.9]
    )

    # Plot lines
    ax.plot(
        rounds,
        team_a_fitness,
        color=COLORS["team_a_blue"],
        linewidth=4,
        marker="o",
        markersize=10,
        label="Team A (Champion)",
        zorder=5,
    )
    ax.plot(
        rounds,
        team_b_fitness,
        color=COLORS["team_b_red"],
        linewidth=4,
        marker="s",
        markersize=10,
        label="Team B (Challenger)",
        zorder=5,
    )

    # Highlight breakthrough at Round 31
    ax.axvline(31, color=COLORS["breakthrough_green"], linestyle="--", linewidth=2, alpha=0.5)
    ax.annotate(
        "Breakthrough\nRound 31",
        xy=(31, 0.9),
        xytext=(35, 1.0),
        fontsize=16,
        color=COLORS["breakthrough_green"],
        weight="bold",
        arrowprops=dict(arrowstyle="->", color=COLORS["breakthrough_green"], lw=2),
    )

    # Highlight parity at Round 13
    ax.axvline(13, color=COLORS["highlight_gold"], linestyle="--", linewidth=2, alpha=0.5)
    ax.annotate(
        "Parity\nRound 13",
        xy=(13, 0.0),
        xytext=(15, -0.5),
        fontsize=14,
        color=COLORS["highlight_gold"],
        weight="bold",
        arrowprops=dict(arrowstyle="->", color=COLORS["highlight_gold"], lw=2),
    )

    ax.axhline(0, color=COLORS["text_secondary"], linestyle="-", linewidth=1, alpha=0.3)
    ax.set_xlabel("Round Number", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_ylabel("Fitness", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_title(
        "Fitness Evolution: The Red Queen Effect",
        fontsize=22,
        weight="bold",
        color=COLORS["text_primary"],
        pad=20,
    )
    ax.legend(fontsize=14, loc="upper right", framealpha=0.9)
    ax.grid(True, alpha=0.2, color=COLORS["text_secondary"])
    ax.set_xlim(0, 100)
    ax.set_ylim(-1.0, 1.2)

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "fitness_timeline.png",
        dpi=300,
        facecolor=COLORS["bg_dark"],
        bbox_inches="tight",
    )
    print("✓ Created fitness_timeline.png")
    plt.close()


def create_tactical_staircase():
    """Create staircase visualization of punctuated equilibrium"""
    fig, ax = plt.subplots(figsize=(16, 9), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    phases = [
        (1, -0.8, "Baseline\nPursuit"),
        (13, 0.0, "Message\nCoordination"),
        (20, 0.2, "Predictive\nIntercept"),
        (31, 0.9, "Formation\nSpread"),
        (41, 0.9, "Zone\nControl"),
        (95, 0.9, "Refined"),
    ]

    for i in range(len(phases) - 1):
        r1, f1, label1 = phases[i]
        r2, f2, _label2 = phases[i + 1]

        # Horizontal plateau
        ax.plot([r1, r2], [f1, f1], color=COLORS["team_b_red"], linewidth=6, zorder=3)

        # Vertical jump
        if i < len(phases) - 1:
            ax.plot(
                [r2, r2],
                [f1, f2],
                color=COLORS["breakthrough_green"],
                linewidth=6,
                linestyle="--",
                zorder=4,
            )

        # Label
        ax.text(
            r1 + (r2 - r1) / 2,
            f1 - 0.15,
            label1,
            ha="center",
            va="top",
            fontsize=12,
            color=COLORS["text_primary"],
            weight="bold",
            bbox=dict(
                boxstyle="round",
                facecolor=COLORS["bg_dark"],
                edgecolor=COLORS["team_b_red"],
                linewidth=2,
            ),
        )

    # Final phase
    r_final, f_final, label_final = phases[-1]
    ax.plot(
        [phases[-2][0], r_final],
        [f_final, f_final],
        color=COLORS["team_b_red"],
        linewidth=6,
        zorder=3,
    )
    ax.text(
        r_final - 10,
        f_final - 0.15,
        label_final,
        ha="center",
        va="top",
        fontsize=12,
        color=COLORS["text_primary"],
        weight="bold",
        bbox=dict(
            boxstyle="round",
            facecolor=COLORS["bg_dark"],
            edgecolor=COLORS["team_b_red"],
            linewidth=2,
        ),
    )

    ax.axhline(0, color=COLORS["text_secondary"], linestyle="-", linewidth=1, alpha=0.3)
    ax.set_xlabel("Round Number", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_ylabel("Fitness", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_title(
        "Punctuated Equilibrium: Staircase Evolution",
        fontsize=22,
        weight="bold",
        color=COLORS["text_primary"],
        pad=20,
    )
    ax.grid(True, alpha=0.2, color=COLORS["text_secondary"])
    ax.set_xlim(0, 100)
    ax.set_ylim(-1.0, 1.2)

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "tactical_staircase.png",
        dpi=300,
        facecolor=COLORS["bg_dark"],
        bbox_inches="tight",
    )
    print("✓ Created tactical_staircase.png")
    plt.close()


def create_code_growth():
    """Create LOC growth over time"""
    fig, ax = plt.subplots(figsize=(14, 8), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    rounds = np.array([1, 13, 20, 31, 41, 60, 80, 95])
    loc = np.array([66, 95, 120, 187, 210, 210, 210, 210])

    ax.plot(rounds, loc, color=COLORS["team_b_red"], linewidth=5, marker="o", markersize=12)
    ax.fill_between(rounds, 0, loc, color=COLORS["team_b_red"], alpha=0.2)

    # Annotate key points
    ax.annotate(
        "Baseline\n66 LOC",
        xy=(1, 66),
        xytext=(5, 100),
        fontsize=14,
        color=COLORS["text_primary"],
        weight="bold",
        arrowprops=dict(arrowstyle="->", color=COLORS["text_primary"], lw=2),
    )

    ax.annotate(
        "Breakthrough\n187 LOC (+183%)",
        xy=(31, 187),
        xytext=(35, 230),
        fontsize=14,
        color=COLORS["breakthrough_green"],
        weight="bold",
        arrowprops=dict(arrowstyle="->", color=COLORS["breakthrough_green"], lw=2),
    )

    ax.set_xlabel("Round Number", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_ylabel("Lines of Code", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_title(
        "Code Complexity Growth (Team B)",
        fontsize=22,
        weight="bold",
        color=COLORS["text_primary"],
        pad=20,
    )
    ax.grid(True, alpha=0.2, color=COLORS["text_secondary"])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 250)

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "code_growth.png", dpi=300, facecolor=COLORS["bg_dark"], bbox_inches="tight"
    )
    print("✓ Created code_growth.png")
    plt.close()


def create_learning_speed_comparison():
    """Compare co-evolution vs isolated evolution"""
    fig, ax = plt.subplots(figsize=(12, 8), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    categories = ["Isolated\nEvolution", "Co-Evolution\n(M25)"]
    rounds_to_parity = [18, 13]  # 35% faster
    colors = [COLORS["text_secondary"], COLORS["breakthrough_green"]]

    bars = ax.bar(
        categories, rounds_to_parity, color=colors, edgecolor="white", linewidth=2, width=0.6
    )

    for i, (bar, val) in enumerate(zip(bars, rounds_to_parity, strict=False)):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + 0.5,
            f"{val} rounds",
            ha="center",
            va="bottom",
            fontsize=18,
            weight="bold",
            color=colors[i],
        )

    # Add speedup annotation
    ax.annotate(
        "",
        xy=(1, 13),
        xytext=(0, 18),
        arrowprops=dict(arrowstyle="<->", color=COLORS["highlight_gold"], lw=3),
    )
    ax.text(
        0.5,
        15.5,
        "35% faster",
        ha="center",
        va="center",
        fontsize=16,
        weight="bold",
        color=COLORS["highlight_gold"],
        bbox=dict(
            boxstyle="round",
            facecolor=COLORS["bg_dark"],
            edgecolor=COLORS["highlight_gold"],
            linewidth=2,
        ),
    )

    ax.set_ylabel(
        "Rounds to Reach Parity (0.0 Fitness)",
        fontsize=16,
        color=COLORS["text_primary"],
        weight="bold",
    )
    ax.set_title(
        "Learning Speed: Co-Evolution Advantage",
        fontsize=22,
        weight="bold",
        color=COLORS["text_primary"],
        pad=20,
    )
    ax.set_ylim(0, 25)
    ax.grid(True, axis="y", alpha=0.2, color=COLORS["text_secondary"])

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "learning_speed_comparison.png",
        dpi=300,
        facecolor=COLORS["bg_dark"],
        bbox_inches="tight",
    )
    print("✓ Created learning_speed_comparison.png")
    plt.close()


def create_team_a_stagnation():
    """Show Team A's failed mutation attempts"""
    fig, ax = plt.subplots(figsize=(14, 8), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    categories = ["Mutations\nProposed", "Mutations\nAccepted"]
    values = [47, 0]
    colors = [COLORS["warning_magenta"], COLORS["breakthrough_green"]]

    bars = ax.bar(categories, values, color=colors, edgecolor="white", linewidth=2, width=0.5)

    for bar, val in zip(bars, values, strict=False):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + 1,
            f"{val}",
            ha="center",
            va="bottom",
            fontsize=24,
            weight="bold",
            color=COLORS["text_primary"],
        )

    ax.set_ylabel("Count", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_title(
        "Team A: Trapped in Local Optimum",
        fontsize=22,
        weight="bold",
        color=COLORS["text_primary"],
        pad=20,
    )
    ax.set_ylim(0, 60)
    ax.grid(True, axis="y", alpha=0.2, color=COLORS["text_secondary"])

    # Add annotation
    ax.text(
        1.5,
        30,
        "Every mutation\nbroke critical logic",
        ha="center",
        va="center",
        fontsize=16,
        style="italic",
        color=COLORS["team_a_blue"],
        bbox=dict(
            boxstyle="round",
            facecolor=COLORS["bg_dark"],
            edgecolor=COLORS["team_a_blue"],
            linewidth=2,
        ),
    )

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "team_a_stagnation.png",
        dpi=300,
        facecolor=COLORS["bg_dark"],
        bbox_inches="tight",
    )
    print("✓ Created team_a_stagnation.png")
    plt.close()


def create_team_b_acceptance_rate():
    """Show Team B's acceptance rate"""
    fig, ax = plt.subplots(figsize=(14, 8), facecolor=COLORS["bg_dark"])
    set_dark_style(ax, fig)

    categories = ["Total\nRounds", "Mutations\nAccepted", "Acceptance\nRate"]
    values = [95, 8, 8.4]
    colors = [COLORS["text_secondary"], COLORS["breakthrough_green"], COLORS["highlight_gold"]]
    labels = ["95", "8", "8.4%"]

    bars = ax.bar(categories, values, color=colors, edgecolor="white", linewidth=2, width=0.5)

    for bar, _val, label in zip(bars, values, labels, strict=False):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + 2,
            label,
            ha="center",
            va="bottom",
            fontsize=24,
            weight="bold",
            color=COLORS["text_primary"],
        )

    ax.set_ylabel("Count / Percentage", fontsize=16, color=COLORS["text_primary"], weight="bold")
    ax.set_title(
        "Team B: Adaptive Success", fontsize=22, weight="bold", color=COLORS["text_primary"], pad=20
    )
    ax.set_ylim(0, 110)
    ax.grid(True, axis="y", alpha=0.2, color=COLORS["text_secondary"])

    # Add annotation
    ax.text(
        1.5,
        60,
        "Started simple,\nstayed flexible",
        ha="center",
        va="center",
        fontsize=16,
        style="italic",
        color=COLORS["team_b_red"],
        bbox=dict(
            boxstyle="round",
            facecolor=COLORS["bg_dark"],
            edgecolor=COLORS["team_b_red"],
            linewidth=2,
        ),
    )

    plt.tight_layout()
    plt.savefig(
        FIGURES_DIR / "team_b_acceptance_rate.png",
        dpi=300,
        facecolor=COLORS["bg_dark"],
        bbox_inches="tight",
    )
    print("✓ Created team_b_acceptance_rate.png")
    plt.close()


if __name__ == "__main__":
    print("Generating missing M25 figures...")
    print("=" * 60)

    create_fitness_timeline()
    create_tactical_staircase()
    create_code_growth()
    create_learning_speed_comparison()
    create_team_a_stagnation()
    create_team_b_acceptance_rate()

    print("=" * 60)
    print("✅ All missing figures generated!")
