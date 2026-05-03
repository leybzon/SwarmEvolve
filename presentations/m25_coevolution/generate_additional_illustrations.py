#!/usr/bin/env python3
"""
Generate additional illustrations for M25 presentation that are more artistic in nature.
These use programmatic drawing to create scientific illustrations.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.patheffects as path_effects
import numpy as np
from pathlib import Path

# Color scheme
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

FIGURES_DIR = Path('figures')
FIGURES_DIR.mkdir(exist_ok=True)

plt.style.use('dark_background')

def set_dark_style(ax):
    """Apply dark theme styling"""
    ax.set_facecolor(COLORS['bg_dark'])
    ax.spines['bottom'].set_color(COLORS['text_secondary'])
    ax.spines['top'].set_color(COLORS['text_secondary'])
    ax.spines['right'].set_color(COLORS['text_secondary'])
    ax.spines['left'].set_color(COLORS['text_secondary'])
    ax.tick_params(colors=COLORS['text_secondary'])


# ============================================================================
# 1. DARWIN'S FINCHES: Scientific illustration
# ============================================================================

def create_darwins_finches():
    """Create Darwin's finches with different beak shapes"""
    fig, ax = plt.subplots(figsize=(10, 8), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    finches = [
        {'name': 'Ground Finch', 'y': 0.75, 'beak_type': 'thick', 'food': '🌰 Seeds'},
        {'name': 'Tree Finch', 'y': 0.5, 'beak_type': 'medium', 'food': '🐛 Insects'},
        {'name': 'Warbler Finch', 'y': 0.25, 'beak_type': 'thin', 'food': '🌸 Nectar'},
    ]

    for i, finch in enumerate(finches):
        x_center = 0.3
        y = finch['y']

        # Bird body (ellipse)
        body = patches.Ellipse((x_center, y), 0.12, 0.08, angle=0,
                                facecolor=COLORS['text_secondary'], edgecolor='white', linewidth=1.5)
        ax.add_patch(body)

        # Head (circle)
        head = plt.Circle((x_center - 0.06, y + 0.02), 0.04,
                          facecolor=COLORS['text_secondary'], edgecolor='white', linewidth=1.5)
        ax.add_patch(head)

        # Beak (triangle, different sizes)
        if finch['beak_type'] == 'thick':
            beak_length = 0.06
            beak_height = 0.03
            color = COLORS['highlight_gold']
        elif finch['beak_type'] == 'medium':
            beak_length = 0.05
            beak_height = 0.02
            color = COLORS['highlight_gold']
        else:  # thin
            beak_length = 0.07
            beak_height = 0.012
            color = COLORS['highlight_gold']

        beak_x = x_center - 0.10
        beak = patches.Polygon([
            (beak_x, y + 0.02),
            (beak_x - beak_length, y + 0.02 + beak_height/2),
            (beak_x - beak_length, y + 0.02 - beak_height/2)
        ], facecolor=color, edgecolor='white', linewidth=1)
        ax.add_patch(beak)

        # Wing line
        ax.plot([x_center - 0.03, x_center + 0.03], [y, y - 0.03],
                color='white', linewidth=1.5, alpha=0.7)

        # Labels
        ax.text(0.55, y + 0.02, finch['name'], fontsize=16, weight='bold',
                color=COLORS['text_primary'], va='center')
        ax.text(0.55, y - 0.04, f"Food: {finch['food']}", fontsize=12,
                color=COLORS['text_secondary'], va='center', style='italic')

        # Arrow to food specialization
        ax.annotate('', xy=(0.85, y), xytext=(0.75, y),
                    arrowprops=dict(arrowstyle='->', lw=2, color=COLORS['breakthrough_green']))

    # Title and description
    ax.text(0.5, 0.95, "Darwin's Finches: Beak Adaptation", ha='center',
            fontsize=18, weight='bold', color=COLORS['text_primary'])
    ax.text(0.5, 0.05, 'Natural Selection: Form Follows Function',
            ha='center', fontsize=14, color=COLORS['breakthrough_green'], style='italic')

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'darwins_finches.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created darwins_finches.png")
    plt.close()


# ============================================================================
# 2. CODE PHYLOGENY: Tree structure with code snippets
# ============================================================================

def create_code_phylogeny():
    """Create phylogenetic tree with code evolution branches"""
    fig, ax = plt.subplots(figsize=(12, 10), facecolor=COLORS['bg_dark'])
    set_dark_style(ax)

    # Tree structure
    tree_data = [
        # (x, y, label, code_snippet, color, is_breakthrough)
        (0.5, 0.05, 'pursuit_v1', 'chase_closest()', COLORS['text_secondary'], False),
        (0.3, 0.25, 'Msg Coord', 'broadcast_target()', COLORS['team_b_red'], False),
        (0.5, 0.40, 'Predictive', 'aim_ahead(dt)', COLORS['team_b_red'], False),
        (0.35, 0.60, 'Formation', 'min_spacing=80', COLORS['breakthrough_green'], True),
        (0.65, 0.60, 'Kiting', 'retreat_when(close)', COLORS['team_b_red'], False),
        (0.5, 0.80, 'Zone Control', 'coverage=60%', COLORS['team_b_red'], False),
    ]

    # Draw branches (connections)
    connections = [
        (0, 1), (0, 2), (2, 3), (2, 4), (3, 5), (4, 5)
    ]

    for i, j in connections:
        x1, y1 = tree_data[i][0], tree_data[i][1]
        x2, y2 = tree_data[j][0], tree_data[j][1]
        color = tree_data[j][4]
        linewidth = 4 if tree_data[j][5] else 2
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=linewidth, alpha=0.7)

    # Draw nodes
    for x, y, label, code, color, is_breakthrough in tree_data:
        size = 0.08 if is_breakthrough else 0.05
        marker = '*' if is_breakthrough else 'o'
        markersize = 500 if is_breakthrough else 200

        ax.scatter(x, y, s=markersize, c=color, marker=marker,
                   edgecolors='white', linewidths=2, zorder=10)

        # Label
        ax.text(x, y - 0.08, label, ha='center', fontsize=11, weight='bold',
                color=COLORS['text_primary'])

        # Code snippet
        text = ax.text(x, y - 0.12, f'`{code}`', ha='center', fontsize=9,
                       color=COLORS['highlight_gold'], family='monospace',
                       bbox=dict(boxstyle='round', facecolor='black', alpha=0.7))

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_title('Code Evolution: Phylogenetic Tree\nFrom pursuit_v1 to Zone Control',
                 fontsize=18, weight='bold', pad=20, color=COLORS['text_primary'])

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'code_phylogeny.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created code_phylogeny.png")
    plt.close()


# ============================================================================
# 3. SCIENTIST PORTRAITS: Historical figures
# ============================================================================

def create_scientist_portraits():
    """Create 5 scientist portrait grid with names and contributions"""
    fig, axes = plt.subplots(1, 5, figsize=(18, 4), facecolor=COLORS['bg_dark'])

    scientists = [
        ('Darwin', '1809-1882', 'Natural\nSelection', COLORS['breakthrough_green']),
        ('Mendel', '1822-1884', 'Genetics', COLORS['team_a_blue']),
        ('Linnaeus', '1707-1778', 'Taxonomy', COLORS['team_a_blue']),
        ('Gould', '1941-2002', 'Punctuated\nEquilibrium', COLORS['highlight_gold']),
        ('Van Valen', '1935-2010', 'Red Queen\nHypothesis', COLORS['warning_magenta']),
    ]

    for ax, (name, years, contribution, color) in zip(axes, scientists):
        set_dark_style(ax)

        # Portrait silhouette (circle with initials)
        circle = plt.Circle((0.5, 0.6), 0.25, facecolor=color, alpha=0.3,
                            edgecolor=color, linewidth=3)
        ax.add_patch(circle)

        # Initials
        initials = ''.join([word[0] for word in name.split()])
        ax.text(0.5, 0.6, initials, ha='center', va='center',
                fontsize=36, weight='bold', color=color)

        # Name
        ax.text(0.5, 0.25, name, ha='center', va='center',
                fontsize=14, weight='bold', color=COLORS['text_primary'])

        # Years
        ax.text(0.5, 0.15, years, ha='center', va='center',
                fontsize=10, color=COLORS['text_secondary'])

        # Contribution
        ax.text(0.5, 0.05, contribution, ha='center', va='center',
                fontsize=9, color=color, style='italic')

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')

    plt.suptitle('Standing on the Shoulders of Giants', fontsize=20, weight='bold',
                 color=COLORS['text_primary'], y=0.98)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / 'scientist_portraits.png', dpi=300,
                facecolor=COLORS['bg_dark'], bbox_inches='tight')
    print("✓ Created scientist_portraits.png")
    plt.close()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("Generating additional M25 illustrations...")
    print("=" * 60)

    create_darwins_finches()
    create_code_phylogeny()
    create_scientist_portraits()

    print("=" * 60)
    print("✅ Additional illustrations generated!")
