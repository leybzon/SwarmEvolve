#!/usr/bin/env python3
"""Visualize strategy evolution for co-evolution experiments.

Shows how tactical approaches changed over time, not just fitness.
"""

import json
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np


def load_journal(journal_path: Path) -> tuple[list, list]:
    """Load journal and split by team."""
    with open(journal_path) as f:
        entries = [json.loads(line) for line in f]

    team_a = [e for e in entries if e['track'] == 'A']
    team_b = [e for e in entries if e['track'] == 'B']

    return team_a, team_b


def extract_strategy_name(hypothesis: str) -> str:
    """Extract short strategy name from hypothesis."""
    # Take first part before colon
    parts = hypothesis.split(':')
    if len(parts) > 0:
        name = parts[0].strip()
        # Remove "Refined: " prefix if present
        name = re.sub(r'^Refined:\s*', '', name)
        return name
    return hypothesis[:40]


def count_code_lines(round_dir: Path) -> int:
    """Count lines of C++ code (excluding comments/blanks)."""
    candidate_file = round_dir / "candidate.cpp"
    if not candidate_file.exists():
        return 0

    code = candidate_file.read_text()
    lines = code.split('\n')

    # Count non-comment, non-blank lines
    count = 0
    in_multiline_comment = False
    for line in lines:
        stripped = line.strip()

        # Check for multiline comment start/end
        if '/*' in stripped:
            in_multiline_comment = True
        if '*/' in stripped:
            in_multiline_comment = False
            continue

        if in_multiline_comment:
            continue

        # Skip single-line comments and blank lines
        if stripped and not stripped.startswith('//'):
            count += 1

    return count


def plot_strategy_timeline(team_b: list, run_dir: Path, out_path: Path):
    """Figure 1: Strategy Timeline - visual flow of tactical evolution."""
    accepted = [e for e in team_b if e['verdict'] == 'confirmed']

    if len(accepted) < 2:
        print("Not enough accepted champions to show evolution")
        return

    fig, ax = plt.subplots(figsize=(14, 10))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, len(accepted) + 1)
    ax.axis('off')

    # Title
    fig.suptitle('M25 Co-Evolution: Team B Strategy Timeline\nTactical Innovation Path from Pursuit to Zone Control',
                 fontsize=16, fontweight='bold', y=0.98)

    # Define tactical categories and colors
    tactic_colors = {
        'baseline': '#E8E8E8',
        'coordination': '#AED6F1',
        'kiting': '#A9DFBF',
        'prediction': '#F9E79F',
        'formation': '#F5B7B1',
        'zone': '#D7BDE2',
    }

    def categorize_strategy(hypothesis: str, mechanism: str) -> str:
        """Categorize strategy by keywords."""
        text = (hypothesis + ' ' + mechanism).lower()
        if 'zone' in text or 'baiting' in text:
            return 'zone'
        if 'formation' in text or 'spread' in text:
            return 'formation'
        if 'predict' in text or 'intercept' in text:
            return 'prediction'
        if 'kite' in text or 'kiting' in text:
            return 'kiting'
        if 'message' in text or 'coordinat' in text:
            return 'coordination'
        return 'baseline'

    # Draw strategy boxes
    y_positions = []
    box_height = 0.8

    for i, entry in enumerate(accepted):
        y_pos = len(accepted) - i
        y_positions.append(y_pos)

        # Extract info
        round_num = entry['generation']
        fitness = entry['fitness']
        strategy = extract_strategy_name(entry['hypothesis_tested'])
        mechanism = entry.get('mechanism_expected', '')[:80]

        # Categorize
        category = categorize_strategy(entry['hypothesis_tested'], mechanism)
        color = tactic_colors.get(category, '#E8E8E8')

        # Draw box
        box = FancyBboxPatch(
            (0.5, y_pos - box_height/2),
            8.5,
            box_height,
            boxstyle="round,pad=0.1",
            facecolor=color,
            edgecolor='black',
            linewidth=2 if fitness > 0.5 else 1,
            zorder=2
        )
        ax.add_patch(box)

        # Round number and fitness (left side)
        ax.text(0.7, y_pos, f'R{round_num}',
                fontsize=11, fontweight='bold', va='center')

        fitness_color = '#27AE60' if fitness > 0 else '#E74C3C' if fitness < -0.5 else '#F39C12'
        ax.text(1.5, y_pos, f'{fitness:+.2f}',
                fontsize=11, fontweight='bold', color=fitness_color, va='center')

        # Strategy name (center)
        ax.text(2.5, y_pos, strategy,
                fontsize=10, fontweight='bold', va='center', ha='left')

        # Mechanism summary (right side, smaller)
        mechanism_short = mechanism[:60] + '...' if len(mechanism) > 60 else mechanism
        ax.text(5.5, y_pos - 0.15, mechanism_short,
                fontsize=7, va='top', ha='left', style='italic', color='#555555')

        # Add innovation markers
        if i > 0:
            prev_cat = categorize_strategy(accepted[i-1]['hypothesis_tested'],
                                          accepted[i-1].get('mechanism_expected', ''))
            if category != prev_cat:
                # Major innovation
                ax.text(9.2, y_pos, '⚡ NEW',
                       fontsize=9, fontweight='bold', color='#E74C3C',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='#FFEBEE',
                                edgecolor='#E74C3C', linewidth=1.5))

        # Highlight breakthrough
        if fitness > 0.5 and (i == 0 or accepted[i-1]['fitness'] < 0.5):
            ax.text(0.2, y_pos, '★',
                   fontsize=20, color='gold', va='center', ha='center')

    # Draw arrows between strategies
    for i in range(len(y_positions) - 1):
        y_from = y_positions[i]
        y_to = y_positions[i + 1]

        # Determine arrow style based on fitness change
        fitness_from = accepted[len(accepted) - 1 - i]['fitness']
        fitness_to = accepted[len(accepted) - 2 - i]['fitness']

        if fitness_to > fitness_from + 0.3:
            # Major improvement
            arrow_color = '#27AE60'
            arrow_width = 3
            arrow_style = '->'
        elif fitness_to > fitness_from:
            # Minor improvement
            arrow_color = '#52BE80'
            arrow_width = 2
            arrow_style = '->'
        else:
            # Refinement or lateral move
            arrow_color = '#808080'
            arrow_width = 1.5
            arrow_style = '->'

        arrow = FancyArrowPatch(
            (4.5, y_from - box_height/2),
            (4.5, y_to + box_height/2),
            arrowstyle=arrow_style,
            color=arrow_color,
            linewidth=arrow_width,
            zorder=1,
            alpha=0.7
        )
        ax.add_patch(arrow)

    # Add legend for categories
    legend_elements = [
        mpatches.Patch(facecolor=tactic_colors['baseline'], edgecolor='black', label='Baseline Pursuit'),
        mpatches.Patch(facecolor=tactic_colors['coordination'], edgecolor='black', label='Message Coordination'),
        mpatches.Patch(facecolor=tactic_colors['kiting'], edgecolor='black', label='Kiting Tactics'),
        mpatches.Patch(facecolor=tactic_colors['prediction'], edgecolor='black', label='Predictive Intercept'),
        mpatches.Patch(facecolor=tactic_colors['formation'], edgecolor='black', label='Formation Control'),
        mpatches.Patch(facecolor=tactic_colors['zone'], edgecolor='black', label='Zone Control'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9,
             title='Tactical Categories', framealpha=0.9)

    # Add phase annotations on the left
    phases = [
        (len(accepted), len(accepted) - 2, 'Phase 1:\nBootstrap', '#E8F8F5'),
        (len(accepted) - 2, len(accepted) - 5, 'Phase 2:\nRefinement', '#FEF9E7'),
        (len(accepted) - 5, 1, 'Phase 3:\nBreakthrough', '#FADBD8'),
    ]

    for y_top, y_bottom, label, color in phases:
        if y_top > 0 and y_bottom > 0:
            ax.add_patch(plt.Rectangle((0.05, y_bottom), 0.4, y_top - y_bottom,
                                      facecolor=color, alpha=0.3, zorder=0))
            ax.text(0.25, (y_top + y_bottom) / 2, label,
                   fontsize=8, va='center', ha='center', fontweight='bold')

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    print(f"✓ Saved: {out_path}")
    plt.close()


def plot_code_complexity(team_a: list, team_b: list, run_dir: Path, out_path: Path):
    """Figure 3: Code Complexity vs Fitness scatter plot."""
    fig, ax = plt.subplots(figsize=(12, 8))

    # Extract data for both teams
    def get_complexity_data(entries, team_label, run_dir):
        data = []
        for e in entries:
            if e['verdict'] == 'confirmed':
                round_num = e['generation']
                fitness = e['fitness']

                # Get code lines
                round_dir = run_dir / f"round_{round_num:04d}"
                loc = count_code_lines(round_dir)

                if loc > 0:
                    data.append({
                        'round': round_num,
                        'fitness': fitness,
                        'loc': loc,
                        'team': team_label,
                        'hypothesis': extract_strategy_name(e['hypothesis_tested'])
                    })
        return data

    data_a = get_complexity_data(team_a, 'A', run_dir)
    data_b = get_complexity_data(team_b, 'B', run_dir)

    # Plot Team A
    if data_a:
        rounds_a = [d['round'] for d in data_a]
        fitness_a = [d['fitness'] for d in data_a]
        loc_a = [d['loc'] for d in data_a]

        scatter_a = ax.scatter(loc_a, fitness_a,
                              c=rounds_a, cmap='Blues',
                              s=200, alpha=0.7, edgecolors='black', linewidth=1.5,
                              marker='s', label='Team A', zorder=5)

        # Annotate Team A points
        for d in data_a:
            ax.annotate(f"R{d['round']}",
                       xy=(d['loc'], d['fitness']),
                       xytext=(5, 5), textcoords='offset points',
                       fontsize=7, color='#1F618D', fontweight='bold')

    # Plot Team B
    if data_b:
        rounds_b = [d['round'] for d in data_b]
        fitness_b = [d['fitness'] for d in data_b]
        loc_b = [d['loc'] for d in data_b]

        scatter_b = ax.scatter(loc_b, fitness_b,
                              c=rounds_b, cmap='Reds',
                              s=200, alpha=0.7, edgecolors='black', linewidth=1.5,
                              marker='o', label='Team B', zorder=5)

        # Annotate Team B points with strategy names
        for d in data_b:
            ax.annotate(f"R{d['round']}",
                       xy=(d['loc'], d['fitness']),
                       xytext=(5, -5), textcoords='offset points',
                       fontsize=7, color='#922B21', fontweight='bold')

        # Draw evolution path for Team B
        if len(data_b) > 1:
            sorted_b = sorted(data_b, key=lambda x: x['round'])
            path_x = [d['loc'] for d in sorted_b]
            path_y = [d['fitness'] for d in sorted_b]
            ax.plot(path_x, path_y, 'r--', alpha=0.3, linewidth=2, zorder=1,
                   label='Team B Evolution Path')

    # Zero line
    ax.axhline(0, color='gray', linestyle='--', linewidth=1, alpha=0.5, label='Draw Parity')

    # Styling
    ax.set_xlabel('Lines of Code (Complexity)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Fitness', fontsize=12, fontweight='bold')
    ax.set_title('M25 Co-Evolution: Code Complexity vs Performance\nDoes Winning Require More Complex Code?',
                 fontsize=14, fontweight='bold', pad=20)
    ax.grid(True, alpha=0.3, linestyle=':', linewidth=0.5)
    ax.legend(loc='lower right', fontsize=10, framealpha=0.9)

    # Add trend analysis text
    if data_b and len(data_b) > 2:
        avg_loc_early = np.mean([d['loc'] for d in data_b[:len(data_b)//2]])
        avg_loc_late = np.mean([d['loc'] for d in data_b[len(data_b)//2:]])
        avg_fit_early = np.mean([d['fitness'] for d in data_b[:len(data_b)//2]])
        avg_fit_late = np.mean([d['fitness'] for d in data_b[len(data_b)//2:]])

        textstr = f'Team B Trend:\n'
        textstr += f'Early: {avg_loc_early:.0f} LOC, {avg_fit_early:+.2f} fitness\n'
        textstr += f'Late: {avg_loc_late:.0f} LOC, {avg_fit_late:+.2f} fitness\n'
        textstr += f'Complexity Change: {avg_loc_late - avg_loc_early:+.0f} LOC\n'
        textstr += f'Fitness Change: {avg_fit_late - avg_fit_early:+.2f}'

        props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
        ax.text(0.02, 0.98, textstr, transform=ax.transAxes, fontsize=9,
                verticalalignment='top', bbox=props, family='monospace')

    # Add colorbars for round numbers
    if data_a:
        cbar_a = plt.colorbar(scatter_a, ax=ax, pad=0.02, aspect=30)
        cbar_a.set_label('Round Number (Team A)', fontsize=9)

    if data_b:
        cbar_b = plt.colorbar(scatter_b, ax=ax, pad=0.08 if data_a else 0.02, aspect=30)
        cbar_b.set_label('Round Number (Team B)', fontsize=9)

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    print(f"✓ Saved: {out_path}")
    plt.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_strategy_evolution.py <run_dir>")
        print("Example: python visualize_strategy_evolution.py data/runs/m25_coevolve_100r")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    journal_path = run_dir / "journal.jsonl"

    if not journal_path.exists():
        print(f"Error: Journal not found at {journal_path}")
        sys.exit(1)

    output_dir = run_dir / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading journal: {journal_path}")
    team_a, team_b = load_journal(journal_path)
    print(f"  Team A: {len(team_a)} rounds ({len([e for e in team_a if e['verdict'] == 'confirmed'])} accepted)")
    print(f"  Team B: {len(team_b)} rounds ({len([e for e in team_b if e['verdict'] == 'confirmed'])} accepted)")
    print()

    print("Generating strategy evolution visualizations...")

    # Generate figures
    plot_strategy_timeline(team_b, run_dir, output_dir / "fig4_strategy_timeline.png")
    plot_code_complexity(team_a, team_b, run_dir, output_dir / "fig5_code_complexity.png")

    print()
    print(f"✓ All visualizations saved to: {output_dir}")
    print()
    print("Generated files:")
    print(f"  4. fig4_strategy_timeline.png   - Visual flow of tactical evolution")
    print(f"  5. fig5_code_complexity.png     - Complexity vs performance scatter")


if __name__ == "__main__":
    main()
