#!/usr/bin/env python3
"""
Generate all illustrations for the M25 co-evolution presentation.
Creates publication-quality diagrams using matplotlib, networkx, and other libraries.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import networkx as nx
from pathlib import Path

# Color scheme matching presentation
COLORS = {
    'team_a_blue': '#3b82f6',
    'team_b_red': '#ef4444',
    'breakthrough_green': '#22c55e',
    'highlight_gold': '#f59e0b',
    'warning_magenta': '#ec4899',
    'bg_dark': '#0a1929',
    'text_primary': '#e2e8f0',
    'text_secondary': '#94a3b8',
}

# Create figures directory
FIGURES_DIR = Path('figures')
FIGURES_DIR.mkdir(exist_ok=True)

plt.style.use('dark_background')

def set_dark_style(ax):
    """Apply dark theme styling to axis"""
    ax.set_facecolor(COLORS['bg_dark'])
    ax.spines['bottom'].set_color(COLORS['text_secondary'])
    ax.spines['top'].set_color(COLORS['text_secondary'])
    ax.spines['right'].set_color(COLORS['text_secondary'])
    ax.spines['left'].set_color(COLORS['text_secondary'])
    ax.tick_params(colors=COLORS['text_secondary'])
    ax.xaxis.label.set_color(COLORS['text_primary'])
    ax.yaxis.label.set_color(COLORS['text_primary'])
    ax.title.set_color(COLORS['text_primary'])


# ============================================================================
# 1. TITLE SLIDE: Phylogenetic Tree with Code Branches
# ============================================================================

def create_phylogenetic_tree():
    """Create evolutionary tree showing code evolution branches"""
    fig, ax = plt.subplots(figsize=(12, 8), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    # Define tree structure (x, y, label)
    root = (0.5, 0.1)
    branches = [
        # (start, end, label, color)
        ((0.5, 0.1), (0.5, 0.3), 'pursuit_v1\n66 LOC', COLORS['text_secondary']),
        ((0.5, 0.3), (0.3, 0.5), 'Message\nCoord', COLORS['team_b_red']),
        ((0.5, 0.3), (0.7, 0.5), 'Predictive\nIntercept', COLORS['team_b_red']),
        ((0.3, 0.5), (0.2, 0.7), 'Kiting', COLORS['team_b_red']),
        ((0.7, 0.5), (0.5, 0.75), 'Formation\nSpread', COLORS['breakthrough_green']),
        ((0.5, 0.75), (0.4, 0.9), 'Zone\nControl', COLORS['team_b_red']),
        ((0.5, 0.75), (0.6, 0.9), 'Adaptive\nSpacing', COLORS['team_b_red']),
    ]

    for (x1, y1), (x2, y2), label, color in branches:
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=3, alpha=0.8)
        # Add node circle
        circle = plt.Circle((x2, y2), 0.03, color=color, zorder=10)
        ax.add_patch(circle)
        # Add label
        ax.text(x2, y2 - 0.05, label, ha='center', va='top',
                fontsize=10, color=COLORS['text_primary'], weight='bold')

    # Root node
    root_circle = plt.Circle(root, 0.04, color=COLORS['highlight_gold'], zorder=10)
    ax.add_patch(root_circle)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_title('Code Evolution: From Baseline to Breakthrough',
                 fontsize=18, weight='bold', pad=20)

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'phylogenetic_tree.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created phylogenetic_tree.png")
    plt.close()


# ============================================================================
# 2. THREE PARADIGMS: Hand-coding | Vibe Coding | Evolution
# ============================================================================

def create_three_paradigms():
    """Create three-panel illustration of programming paradigms"""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), facecolor=COLORS['bg_dark'])

    # Panel 1: Hand-coding (person at desk)
    ax = axes[0]
    set_dark_style(ax)
    ax.text(0.5, 0.7, '👤', fontsize=80, ha='center', va='center')
    ax.text(0.5, 0.3, '⌨️', fontsize=60, ha='center', va='center')
    ax.text(0.5, 0.1, 'Hand-Coding', fontsize=16, ha='center',
            color=COLORS['team_a_blue'], weight='bold')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Panel 2: Vibe coding (glitchy AI)
    ax = axes[1]
    set_dark_style(ax)
    ax.text(0.5, 0.7, '🤖', fontsize=80, ha='center', va='center')
    ax.text(0.5, 0.4, '✨💫✨', fontsize=40, ha='center', va='center')
    ax.text(0.5, 0.1, 'Vibe Coding', fontsize=16, ha='center',
            color=COLORS['warning_magenta'], weight='bold')
    # Add warning badge
    rect = patches.Rectangle((0.35, 0.05), 0.3, 0.08,
                              facecolor=COLORS['warning_magenta'],
                              alpha=0.3, edgecolor=COLORS['warning_magenta'], linewidth=2)
    ax.add_patch(rect)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Panel 3: Evolution (petri dish with organisms)
    ax = axes[2]
    set_dark_style(ax)
    # Draw petri dish
    circle = plt.Circle((0.5, 0.5), 0.35, fill=False,
                        edgecolor=COLORS['breakthrough_green'], linewidth=3)
    ax.add_patch(circle)
    # Add organisms (dots competing)
    np.random.seed(42)
    for _ in range(15):
        x, y = 0.5 + 0.3 * (np.random.rand() - 0.5), 0.5 + 0.3 * (np.random.rand() - 0.5)
        color = COLORS['team_b_red'] if np.random.rand() > 0.5 else COLORS['team_a_blue']
        ax.plot(x, y, 'o', color=color, markersize=10, alpha=0.7)
    ax.text(0.5, 0.05, 'Evolution', fontsize=16, ha='center',
            color=COLORS['breakthrough_green'], weight='bold')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'three_paradigms.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created three_paradigms.png")
    plt.close()


# ============================================================================
# 3. ARENA DIAGRAM: Top-down view with trajectories
# ============================================================================

def create_arena_diagram():
    """Create arena diagram with drones and combat zones"""
    fig, ax = plt.subplots(figsize=(10, 10), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    # Arena bounds
    arena_size = 1000
    ax.set_xlim(0, arena_size)
    ax.set_ylim(0, arena_size)

    # Grid
    for i in range(0, arena_size + 1, 200):
        ax.axhline(i, color=COLORS['text_secondary'], alpha=0.2, linewidth=0.5)
        ax.axvline(i, color=COLORS['text_secondary'], alpha=0.2, linewidth=0.5)

    # Team A positions (blue, left side)
    np.random.seed(42)
    team_a_x = np.random.uniform(100, 400, 10)
    team_a_y = np.random.uniform(100, 900, 10)
    ax.scatter(team_a_x, team_a_y, color=COLORS['team_a_blue'],
               s=200, alpha=0.8, edgecolors='white', linewidths=2, label='Team A')

    # Team B positions (red, right side)
    team_b_x = np.random.uniform(600, 900, 10)
    team_b_y = np.random.uniform(100, 900, 10)
    ax.scatter(team_b_x, team_b_y, color=COLORS['team_b_red'],
               s=200, alpha=0.8, edgecolors='white', linewidths=2, label='Team B')

    # Combat range circles
    for x, y in zip(team_a_x[:3], team_a_y[:3]):
        circle = plt.Circle((x, y), 100, fill=False,
                            edgecolor=COLORS['team_a_blue'],
                            linewidth=1.5, linestyle='--', alpha=0.4)
        ax.add_patch(circle)

    # Trajectory arrows
    for i in range(3):
        ax.annotate('', xy=(team_b_x[i], team_b_y[i]),
                    xytext=(team_a_x[i], team_a_y[i]),
                    arrowprops=dict(arrowstyle='->', lw=2,
                                    color=COLORS['highlight_gold'], alpha=0.6))

    ax.set_xlabel('X Position (units)', fontsize=14, weight='bold')
    ax.set_ylabel('Y Position (units)', fontsize=14, weight='bold')
    ax.set_title('Combat Arena: 1000×1000 Units\n2 Teams × 10 Drones (Sample)',
                 fontsize=16, weight='bold', pad=20)
    ax.legend(fontsize=12, loc='upper right')
    ax.set_aspect('equal')

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'arena_diagram.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created arena_diagram.png")
    plt.close()


# ============================================================================
# 4. TACTICAL TIMELINE: Horizontal with fitness jumps
# ============================================================================

def create_tactical_timeline():
    """Create horizontal timeline with vertical fitness jumps"""
    fig, ax = plt.subplots(figsize=(14, 6), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    rounds = [1, 3, 13, 31, 41, 95]
    fitness = [-0.8, -0.4, 0.0, 0.9, 0.9, 0.9]
    tactics = ['Baseline', 'Message\nCoord', 'Predictive\nIntercept',
               'Formation\nSpread', 'Zone\nControl', 'Refined']
    colors = [COLORS['team_b_red'], COLORS['team_b_red'], COLORS['highlight_gold'],
              COLORS['breakthrough_green'], COLORS['team_b_red'], COLORS['team_b_red']]

    # Bars
    for i, (r, f, t, c) in enumerate(zip(rounds, fitness, tactics, colors)):
        bar_color = COLORS['breakthrough_green'] if i == 3 else c
        alpha = 1.0 if i == 3 else 0.7
        ax.bar(r, f + 1, width=8, bottom=-1, color=bar_color, alpha=alpha, edgecolor='white', linewidth=2)
        ax.text(r, f + 1.1, t, ha='center', va='bottom',
                fontsize=11, color=COLORS['text_primary'], weight='bold')
        ax.text(r, -1.2, f'R{r}\n{f:+.1f}', ha='center', va='top',
                fontsize=10, color=COLORS['text_secondary'])

    ax.axhline(0, color=COLORS['text_secondary'], linestyle='--', linewidth=1.5, alpha=0.5)
    ax.set_xlim(-5, 100)
    ax.set_ylim(-1.5, 1.2)
    ax.set_xlabel('Round Number', fontsize=14, weight='bold')
    ax.set_ylabel('Fitness', fontsize=14, weight='bold')
    ax.set_title('Tactical Evolution: Punctuated Equilibrium in Action',
                 fontsize=16, weight='bold', pad=20)
    ax.grid(axis='y', alpha=0.2)

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'tactical_timeline.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created tactical_timeline.png")
    plt.close()


# ============================================================================
# 5. LOC vs FITNESS: Scatter plot
# ============================================================================

def create_loc_fitness_scatter():
    """Create scatter plot showing LOC vs Fitness correlation"""
    fig, ax = plt.subplots(figsize=(10, 8), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    # Team A failed mutations (gray dots around 204 LOC)
    np.random.seed(42)
    team_a_loc = np.random.normal(204, 15, 47)
    team_a_fitness = np.random.uniform(-0.2, 0.3, 47)
    ax.scatter(team_a_loc, team_a_fitness, color='gray', s=100,
               alpha=0.4, label='Team A Failed Mutations (n=47)')

    # Team B successful evolution (red line)
    team_b_rounds = [1, 3, 13, 31, 41, 95]
    team_b_loc = [66, 85, 120, 187, 205, 210]
    team_b_fitness = [-0.8, -0.4, 0.0, 0.9, 0.9, 0.9]
    ax.plot(team_b_loc, team_b_fitness, color=COLORS['team_b_red'],
            linewidth=3, marker='o', markersize=12, label='Team B Evolution Path')

    # Highlight breakthrough
    ax.scatter([187], [0.9], color=COLORS['breakthrough_green'],
               s=400, marker='*', edgecolors='white', linewidths=2,
               label='R31 Breakthrough', zorder=10)

    ax.set_xlabel('Lines of Code (LOC)', fontsize=14, weight='bold')
    ax.set_ylabel('Fitness', fontsize=14, weight='bold')
    ax.set_title('Code Complexity vs Performance\nTeam A Trapped, Team B Adaptive',
                 fontsize=16, weight='bold', pad=20)
    ax.legend(fontsize=11, loc='lower right')
    ax.grid(alpha=0.2)
    ax.axhline(0, color=COLORS['text_secondary'], linestyle='--', linewidth=1, alpha=0.5)

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'loc_fitness_scatter.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created loc_fitness_scatter.png")
    plt.close()


# ============================================================================
# 6. PREDATOR-PREY OSCILLATION: Lotka-Volterra + Team A/B
# ============================================================================

def create_predator_prey_graph():
    """Create predator-prey oscillation overlaid with Team A/B fitness"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                     facecolor=COLORS['bg_dark'], sharex=True)

    # Lotka-Volterra oscillations
    t = np.linspace(0, 100, 500)
    prey = 50 + 30 * np.sin(0.1 * t)
    predator = 40 + 25 * np.sin(0.1 * t - np.pi/2)

    set_dark_style(ax1)
    ax1.plot(t, prey, color=COLORS['breakthrough_green'], linewidth=2.5, label='Prey (Hare)')
    ax1.plot(t, predator, color=COLORS['team_b_red'], linewidth=2.5, label='Predator (Lynx)')
    ax1.set_ylabel('Population', fontsize=14, weight='bold')
    ax1.set_title('Classic Predator-Prey Dynamics (Lotka-Volterra)',
                  fontsize=16, weight='bold', pad=15)
    ax1.legend(fontsize=12, loc='upper right')
    ax1.grid(alpha=0.2)

    # Team A/B fitness (mirroring predator-prey)
    rounds = np.linspace(0, 95, 100)
    team_a_fit = 1.0 - 0.3 * (rounds / 95)  # Declining
    team_b_fit = -0.8 + 1.7 * (rounds / 95)  # Rising

    set_dark_style(ax2)
    ax2.plot(rounds, team_a_fit, color=COLORS['team_a_blue'],
             linewidth=3, label='Team A (Champion)')
    ax2.plot(rounds, team_b_fit, color=COLORS['team_b_red'],
             linewidth=3, label='Team B (Challenger)')
    ax2.axhline(0, color=COLORS['text_secondary'], linestyle='--', linewidth=1, alpha=0.5)
    # Crossover point
    crossover_round = 31
    ax2.axvline(crossover_round, color=COLORS['breakthrough_green'],
                linestyle=':', linewidth=2, alpha=0.7)
    ax2.text(crossover_round + 2, 0.5, 'Fitness Reversal\n(Round 31)',
             fontsize=11, color=COLORS['breakthrough_green'], weight='bold')

    ax2.set_xlabel('Round Number', fontsize=14, weight='bold')
    ax2.set_ylabel('Fitness', fontsize=14, weight='bold')
    ax2.set_title('Co-Evolutionary Arms Race: Red Queen Effect',
                  fontsize=16, weight='bold', pad=15)
    ax2.legend(fontsize=12, loc='upper left')
    ax2.grid(alpha=0.2)

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'predator_prey_graph.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created predator_prey_graph.png")
    plt.close()


# ============================================================================
# 7. FUTURE APPLICATIONS: 4 quadrants
# ============================================================================

def create_future_applications():
    """Create 4-quadrant diagram for future applications"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 12), facecolor=COLORS['bg_dark'])

    apps = [
        ('Co-Evolving\nMicroservices', '🌐', COLORS['team_a_blue']),
        ('Immune System\nSoftware', '🛡️', COLORS['breakthrough_green']),
        ('Evolutionary\nDebugging', '🔧', COLORS['warning_magenta']),
        ('Symbiotic\nCodebases', '🤝', COLORS['highlight_gold']),
    ]

    for ax, (title, emoji, color) in zip(axes.flat, apps):
        set_dark_style(ax)
        ax.text(0.5, 0.6, emoji, fontsize=100, ha='center', va='center')
        ax.text(0.5, 0.2, title, fontsize=18, ha='center', va='center',
                color=color, weight='bold')
        # Border
        rect = patches.Rectangle((0.05, 0.05), 0.9, 0.9,
                                  fill=False, edgecolor=color, linewidth=3)
        ax.add_patch(rect)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')

    plt.suptitle('Future of Evolutionary Code', fontsize=20, weight='bold',
                 color=COLORS['text_primary'], y=0.98)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'future_applications.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created future_applications.png")
    plt.close()


# ============================================================================
# 8. EVOLUTION vs ENGINEERING: Finches vs CAD
# ============================================================================

def create_evolution_vs_engineering():
    """Create side-by-side comparison of evolution vs engineering"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8), facecolor=COLORS['bg_dark'])

    # Left: Evolution (Darwin's finches)
    set_dark_style(ax1)
    # Draw simplified finch beaks
    beak_types = [(0.3, 0.7, 'Ground Finch'), (0.5, 0.5, 'Tree Finch'), (0.7, 0.3, 'Warbler Finch')]
    for i, (x, y, label) in enumerate(beak_types):
        # Beak shape
        beak_length = 0.1 + i * 0.03
        ax1.plot([x - beak_length, x], [y, y], color=COLORS['highlight_gold'],
                 linewidth=10 - i * 2)
        # Head circle
        circle = plt.Circle((x - beak_length, y), 0.05, color=COLORS['text_secondary'])
        ax1.add_patch(circle)
        ax1.text(x - beak_length, y - 0.12, label, ha='center',
                 fontsize=11, color=COLORS['text_primary'])

    ax1.text(0.5, 0.9, 'Natural Selection', ha='center', fontsize=20,
             weight='bold', color=COLORS['breakthrough_green'])
    ax1.text(0.5, 0.05, 'Emergent • Adaptive • Unpredictable', ha='center',
             fontsize=12, color=COLORS['text_secondary'], style='italic')
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.axis('off')

    # Right: Engineering (CAD blueprint)
    set_dark_style(ax2)
    # Draw blueprint grid
    for i in np.linspace(0.1, 0.9, 9):
        ax2.axhline(i, color=COLORS['team_a_blue'], alpha=0.3, linewidth=0.5)
        ax2.axvline(i, color=COLORS['team_a_blue'], alpha=0.3, linewidth=0.5)

    # Draw circuit-like diagram
    ax2.plot([0.2, 0.4], [0.7, 0.7], color=COLORS['team_a_blue'], linewidth=3)
    ax2.plot([0.4, 0.4], [0.7, 0.5], color=COLORS['team_a_blue'], linewidth=3)
    ax2.plot([0.4, 0.6], [0.5, 0.5], color=COLORS['team_a_blue'], linewidth=3)
    ax2.plot([0.6, 0.6], [0.5, 0.3], color=COLORS['team_a_blue'], linewidth=3)
    ax2.plot([0.6, 0.8], [0.3, 0.3], color=COLORS['team_a_blue'], linewidth=3)

    # Add component boxes
    for x, y in [(0.2, 0.7), (0.4, 0.5), (0.6, 0.3), (0.8, 0.3)]:
        rect = patches.Rectangle((x - 0.04, y - 0.04), 0.08, 0.08,
                                  facecolor=COLORS['team_a_blue'], alpha=0.6)
        ax2.add_patch(rect)

    ax2.text(0.5, 0.9, 'Intelligent Design', ha='center', fontsize=20,
             weight='bold', color=COLORS['team_a_blue'])
    ax2.text(0.5, 0.05, 'Planned • Predictable • Optimized', ha='center',
             fontsize=12, color=COLORS['text_secondary'], style='italic')
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis('off')

    plt.suptitle('Evolution vs. Engineering', fontsize=22, weight='bold',
                 color=COLORS['text_primary'], y=0.98)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'evolution_vs_engineering.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created evolution_vs_engineering.png")
    plt.close()


# ============================================================================
# MAIN: Generate all illustrations
# ============================================================================

if __name__ == '__main__':
    print("Generating M25 presentation illustrations...")
    print("=" * 60)

    create_phylogenetic_tree()
    create_three_paradigms()
    create_arena_diagram()
    create_tactical_timeline()
    create_loc_fitness_scatter()
    create_predator_prey_graph()
    create_future_applications()
    create_evolution_vs_engineering()

    print("=" * 60)
    print(f"✅ All illustrations generated in {FIGURES_DIR}/")
    print("\nNext steps:")
    print("1. Review generated images")
    print("2. Run update script to replace placeholders in index.html")
