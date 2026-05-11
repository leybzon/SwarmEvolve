"""
Regenerate emergent behavior timeline with dark presentation theme colors.
Matches: --bg-dark: #0a0f1e, --accent-green: #22c55e, --accent-blue: #3b82f6,
         --accent-red: #ef4444, --accent-gold: #eab308, --accent-magenta: #c084fc
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

# Presentation theme colors
BG_DARK = "#0a0f1e"
BG_CARD = "#111827"
TEXT_PRIMARY = "#e2e8f0"
TEXT_SECONDARY = "#94a3b8"
ACCENT_BLUE = "#3b82f6"
ACCENT_GREEN = "#22c55e"
ACCENT_RED = "#ef4444"
ACCENT_GOLD = "#eab308"
ACCENT_MAGENTA = "#c084fc"
GRID_COLOR = "#1e293b"

# Data: Team B champion fitness at key rounds
rounds = [1, 3, 5, 7, 9, 13, 19, 25, 31, 35, 41, 47]
fitness = [-0.8, -0.4, -0.4, -0.3, -0.3, 0.0, 0.0, 0.0, 1.0, 0.8, 1.0, 0.8]

# Phase boundaries and labels
phases = [
    (0, 5, "Bootstrap", ACCENT_RED, 0.08),
    (5, 10, "Coordination", ACCENT_GOLD, 0.08),
    (10, 18, "Prediction", ACCENT_MAGENTA, 0.08),
    (18, 32, "Formation", ACCENT_GREEN, 0.10),
    (32, 50, "Zone Control", ACCENT_BLUE, 0.08),
]

# Key milestone annotations
milestones = [
    (1, -0.8, "Round 1\nBaseline pursuit\nNo coordination", ACCENT_RED, "right"),
    (7, -0.3, "Round 7\nMessage-coordinated\ntarget selection", ACCENT_GOLD, "right"),
    (13, 0.0, "Round 13\nPredictive intercept\nAnticipatory positioning", ACCENT_MAGENTA, "left"),
    (19, 0.0, "Round 19\nMessage-based\ntarget claiming", ACCENT_BLUE, "right"),
    (31, 1.0, "Round 31 ★\nFormation Spread\nmin_spacing = 80", ACCENT_GREEN, "left"),
    (41, 1.0, "Round 41\nZone Control\nSpatial distribution", ACCENT_BLUE, "right"),
]

fig, ax = plt.subplots(figsize=(14, 6.5))
fig.patch.set_facecolor(BG_DARK)
ax.set_facecolor(BG_DARK)

# Draw phase backgrounds
for start, end, label, color, alpha in phases:
    ax.axvspan(start, end, alpha=alpha, color=color, zorder=0)
    ax.text(
        (start + end) / 2,
        -1.55,
        label,
        ha="center",
        va="top",
        fontsize=9,
        fontweight="bold",
        color=color,
        bbox=dict(
            boxstyle="round,pad=0.3", facecolor=BG_CARD, edgecolor=color, linewidth=1.5, alpha=0.9
        ),
    )

# Fitness fill area
ax.fill_between(rounds, fitness, alpha=0.12, color=ACCENT_BLUE, zorder=1)

# Draw parity line
ax.axhline(y=0, color=TEXT_SECONDARY, linestyle="--", linewidth=0.8, alpha=0.4, zorder=2)
ax.text(
    49, 0.05, "Draw Parity", ha="right", va="bottom", fontsize=8, color=TEXT_SECONDARY, alpha=0.6
)

# Plot fitness line
ax.plot(
    rounds,
    fitness,
    "-o",
    color=ACCENT_BLUE,
    linewidth=2.5,
    markersize=8,
    markerfacecolor=ACCENT_BLUE,
    markeredgecolor="white",
    markeredgewidth=1.5,
    zorder=5,
)

# Annotate milestones
for r, f, text, color, ha_side in milestones:
    offset_x = -15 if ha_side == "left" else 15
    offset_y = 20 if f < 0.5 else -20
    ha = "right" if ha_side == "left" else "left"

    # Highlight Round 31 specially
    if r == 31:
        ax.plot(r, f, "o", color=ACCENT_GREEN, markersize=14, zorder=6, alpha=0.3)
        ax.plot(r, f, "o", color=ACCENT_GREEN, markersize=18, zorder=6, alpha=0.15)

    ax.annotate(
        text,
        xy=(r, f),
        xytext=(offset_x, offset_y),
        textcoords="offset points",
        ha=ha,
        va="center",
        fontsize=8,
        color=color,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.4", facecolor=BG_CARD, edgecolor=color, linewidth=1.2, alpha=0.92
        ),
        arrowprops=dict(arrowstyle="->", color=color, lw=1.2, connectionstyle="arc3,rad=0.2"),
    )

# Styling
ax.set_xlim(-1, 50)
ax.set_ylim(-1.7, 1.5)
ax.set_xlabel("Round Number", fontsize=12, color=TEXT_PRIMARY, fontweight="bold")
ax.set_ylabel("Fitness", fontsize=12, color=TEXT_PRIMARY, fontweight="bold")
ax.set_title(
    "Emergent Behavior Timeline — Team B Tactical Development",
    fontsize=15,
    color=TEXT_PRIMARY,
    fontweight="bold",
    pad=15,
)

ax.tick_params(colors=TEXT_SECONDARY, labelsize=10)
ax.spines["bottom"].set_color(GRID_COLOR)
ax.spines["left"].set_color(GRID_COLOR)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.grid(True, alpha=0.15, color=GRID_COLOR, linestyle="-")

# Legend
legend_elements = [
    plt.Line2D(
        [0],
        [0],
        marker="o",
        color=ACCENT_BLUE,
        linewidth=2,
        markerfacecolor=ACCENT_BLUE,
        markeredgecolor="white",
        markersize=8,
        label="Champion Fitness",
    ),
    plt.Line2D([0], [0], color=TEXT_SECONDARY, linestyle="--", linewidth=0.8, label="Draw Parity"),
    mpatches.Patch(facecolor=ACCENT_BLUE, alpha=0.12, label="Fitness Area"),
]
legend = ax.legend(
    handles=legend_elements,
    loc="lower right",
    fontsize=9,
    facecolor=BG_CARD,
    edgecolor=GRID_COLOR,
    labelcolor=TEXT_SECONDARY,
)

plt.tight_layout()
out_path = "/Users/yevgeniy.leybzon/Documents/DroneEvolution/presentations/m25_coevolution/figures/emergent_behavior_dark.png"
plt.savefig(out_path, dpi=200, facecolor=BG_DARK, bbox_inches="tight", pad_inches=0.2)
print(f"Saved: {out_path}")
