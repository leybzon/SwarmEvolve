#!/usr/bin/env python3
"""Visualize tactical relationships and emergent behaviors in co-evolution.

Creates network graphs and timelines showing how strategies relate to each other.
"""

import json
import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def load_journal(journal_path: Path) -> tuple[list, list]:
    """Load journal and split by team."""
    with open(journal_path) as f:
        entries = [json.loads(line) for line in f]

    team_a = [e for e in entries if e["track"] == "A"]
    team_b = [e for e in entries if e["track"] == "B"]

    return team_a, team_b


def extract_strategy_name(hypothesis: str) -> str:
    """Extract short strategy name from hypothesis."""
    parts = hypothesis.split(":")
    if len(parts) > 0:
        name = parts[0].strip()
        name = re.sub(r"^Refined:\s*", "", name)
        return name
    return hypothesis[:40]


def categorize_tactic(hypothesis: str, mechanism: str) -> str:
    """Extract primary tactic type."""
    text = (hypothesis + " " + mechanism).lower()

    if "zone" in text or "baiting" in text:
        return "Zone Control"
    if "formation" in text or "spread" in text:
        return "Formation"
    if "predict" in text or "intercept" in text:
        return "Prediction"
    if "kite" in text or "kiting" in text or "retreat" in text:
        return "Kiting"
    if "message" in text or "coordinat" in text or "claim" in text:
        return "Coordination"
    if "pursuit" in text or "chase" in text:
        return "Pursuit"
    return "Other"


def plot_strategy_progression_tree(team_b: list, out_path: Path):
    """Figure 6: Strategy Progression Flowchart - hierarchical descent tree."""
    accepted = [e for e in team_b if e["verdict"] == "confirmed"]

    if len(accepted) < 2:
        print("Not enough accepted champions for progression tree")
        return

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, len(accepted) + 2)
    ax.axis("off")

    fig.suptitle(
        "M25 Co-Evolution: Strategy Progression Tree\nLineage of Successful Tactics (Team B)",
        fontsize=16,
        fontweight="bold",
        y=0.98,
    )

    # Build tree structure
    tree_data = []
    for i, entry in enumerate(accepted):
        round_num = entry["generation"]
        fitness = entry["fitness"]
        strategy = extract_strategy_name(entry["hypothesis_tested"])
        mechanism = entry.get("mechanism_expected", "")
        tactic_type = categorize_tactic(entry["hypothesis_tested"], mechanism)

        tree_data.append(
            {
                "level": i,
                "round": round_num,
                "fitness": fitness,
                "strategy": strategy,
                "tactic_type": tactic_type,
                "parent": i - 1 if i > 0 else None,
            }
        )

    # Color mapping
    tactic_colors = {
        "Pursuit": "#E8E8E8",
        "Coordination": "#AED6F1",
        "Kiting": "#A9DFBF",
        "Prediction": "#F9E79F",
        "Formation": "#F5B7B1",
        "Zone Control": "#D7BDE2",
        "Other": "#E8E8E8",
    }

    # Draw nodes
    node_positions = {}
    box_width = 3.5
    box_height = 0.6

    for _i, node in enumerate(tree_data):
        level = node["level"]
        y_pos = len(tree_data) - level + 1

        # Calculate x position (center, with slight offset for depth)
        x_pos = 8 - (level * 0.15)

        node_positions[level] = (x_pos, y_pos)

        # Draw box
        color = tactic_colors.get(node["tactic_type"], "#E8E8E8")

        # Highlight breakthrough
        linewidth = 3 if node["fitness"] > 0.5 else 1.5

        box = FancyBboxPatch(
            (x_pos - box_width / 2, y_pos - box_height / 2),
            box_width,
            box_height,
            boxstyle="round,pad=0.05",
            facecolor=color,
            edgecolor="black",
            linewidth=linewidth,
            zorder=2,
        )
        ax.add_patch(box)

        # Add gold star for breakthrough
        if node["fitness"] > 0.5 and (level == 0 or tree_data[level - 1]["fitness"] < 0.5):
            ax.text(
                x_pos - box_width / 2 - 0.3,
                y_pos,
                "★",
                fontsize=24,
                color="gold",
                va="center",
                ha="center",
                zorder=5,
            )

        # Round and fitness (left)
        ax.text(
            x_pos - box_width / 2 + 0.2,
            y_pos,
            f"R{node['round']}",
            fontsize=9,
            fontweight="bold",
            va="center",
        )

        fitness_color = "#27AE60" if node["fitness"] > 0 else "#E74C3C"
        ax.text(
            x_pos - box_width / 2 + 0.8,
            y_pos,
            f"{node['fitness']:+.2f}",
            fontsize=9,
            fontweight="bold",
            color=fitness_color,
            va="center",
        )

        # Strategy name (center)
        strategy_short = (
            node["strategy"][:30] + "..." if len(node["strategy"]) > 30 else node["strategy"]
        )
        ax.text(
            x_pos, y_pos, strategy_short, fontsize=9, va="center", ha="center", fontweight="bold"
        )

        # Tactic type (right)
        ax.text(
            x_pos + box_width / 2 - 0.2,
            y_pos,
            node["tactic_type"],
            fontsize=7,
            va="center",
            ha="right",
            style="italic",
            color="#555",
        )

    # Draw connecting arrows
    for i, node in enumerate(tree_data[1:], 1):
        parent_idx = node["parent"]
        if parent_idx is not None and parent_idx in node_positions:
            x_parent, y_parent = node_positions[parent_idx]
            x_child, y_child = node_positions[i]

            # Determine arrow style based on fitness improvement
            fitness_delta = node["fitness"] - tree_data[parent_idx]["fitness"]

            if fitness_delta > 0.5:
                arrow_color = "#27AE60"
                arrow_width = 3
                label = f"+{fitness_delta:.2f}"
            elif fitness_delta > 0.1:
                arrow_color = "#52BE80"
                arrow_width = 2
                label = f"+{fitness_delta:.2f}"
            elif fitness_delta > 0:
                arrow_color = "#85C1E2"
                arrow_width = 1.5
                label = f"+{fitness_delta:.2f}"
            else:
                arrow_color = "#95A5A6"
                arrow_width = 1
                label = f"{fitness_delta:.2f}"

            # Draw arrow
            arrow = FancyArrowPatch(
                (x_parent, y_parent - box_height / 2 - 0.05),
                (x_child, y_child + box_height / 2 + 0.05),
                arrowstyle="-|>",
                color=arrow_color,
                linewidth=arrow_width,
                zorder=1,
                alpha=0.7,
                mutation_scale=20,
            )
            ax.add_patch(arrow)

            # Add fitness delta label
            mid_x = (x_parent + x_child) / 2 + 0.3
            mid_y = (y_parent + y_child) / 2
            ax.text(
                mid_x,
                mid_y,
                label,
                fontsize=7,
                color=arrow_color,
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.8),
            )

    # Add legend
    legend_elements = [
        mpatches.Patch(facecolor=color, edgecolor="black", label=tactic)
        for tactic, color in tactic_colors.items()
        if any(n["tactic_type"] == tactic for n in tree_data)
    ]
    ax.legend(
        handles=legend_elements, loc="upper left", fontsize=9, title="Tactic Types", framealpha=0.9
    )

    # Add annotations
    ax.text(
        0.5,
        0.5,
        "Lineage shows\nevolutionary path\nfrom simple to\ncomplex tactics",
        fontsize=9,
        style="italic",
        color="gray",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="lightyellow", alpha=0.5),
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def plot_counter_tactic_network(team_a: list, team_b: list, out_path: Path):
    """Figure 7: Counter-Tactic Network Graph - who counters whom."""
    accepted_a = [e for e in team_a if e["verdict"] == "confirmed"]
    accepted_b = [e for e in team_b if e["verdict"] == "confirmed"]

    _fig, ax = plt.subplots(figsize=(14, 10))

    # Create directed graph
    G = nx.DiGraph()

    # Add nodes
    node_colors = []
    node_sizes = []
    node_labels = {}

    # Team A nodes
    for entry in accepted_a:
        node_id = f"A{entry['generation']}"
        strategy = extract_strategy_name(entry["hypothesis_tested"])[:20]
        fitness = entry["fitness"]

        G.add_node(node_id, team="A", strategy=strategy, fitness=fitness, round=entry["generation"])
        node_colors.append("#4A90E2")
        node_sizes.append(1000 + fitness * 500)
        node_labels[node_id] = f"A:R{entry['generation']}\n{strategy[:15]}\n({fitness:+.1f})"

    # Team B nodes
    for entry in accepted_b:
        node_id = f"B{entry['generation']}"
        strategy = extract_strategy_name(entry["hypothesis_tested"])[:20]
        fitness = entry["fitness"]

        G.add_node(node_id, team="B", strategy=strategy, fitness=fitness, round=entry["generation"])
        node_colors.append("#E24A4A")
        node_sizes.append(1000 + abs(fitness) * 500)
        node_labels[node_id] = f"B:R{entry['generation']}\n{strategy[:15]}\n({fitness:+.1f})"

    # Add edges based on temporal sequence and success
    # Team B tactics that improved counter earlier Team A tactics
    for i, entry_b in enumerate(accepted_b[1:], 1):
        prev_b = accepted_b[i - 1]

        node_from = f"B{prev_b['generation']}"
        node_to = f"B{entry_b['generation']}"

        # Evolution edge
        fitness_delta = entry_b["fitness"] - prev_b["fitness"]
        if fitness_delta > 0:
            G.add_edge(
                node_from,
                node_to,
                edge_type="evolution",
                weight=fitness_delta,
                label=f"+{fitness_delta:.2f}",
            )

    # Cross-team countering relationships
    # When Team B improves, it's countering current Team A champion
    for entry_b in accepted_b:
        # Find Team A champion at the time
        for entry_a in accepted_a:
            if entry_a["generation"] < entry_b["generation"]:
                # Team B tactic came after Team A, potentially counters it
                if entry_b["fitness"] > 0:  # Team B is winning
                    node_a = f"A{entry_a['generation']}"
                    node_b = f"B{entry_b['generation']}"

                    if node_a in G and node_b in G:
                        # Only show strongest counter relationships
                        if entry_b["fitness"] > 0.5:
                            G.add_edge(
                                node_b,
                                node_a,
                                edge_type="counter",
                                weight=entry_b["fitness"],
                                label="counters",
                            )

    # Position nodes
    pos = nx.spring_layout(G, k=2, iterations=50, seed=42)

    # Separate by team
    team_a_nodes = [n for n in G.nodes() if n.startswith("A")]
    team_b_nodes = [n for n in G.nodes() if n.startswith("B")]

    # Adjust positions to separate teams
    for node in team_a_nodes:
        pos[node][0] -= 0.5  # Shift left
    for node in team_b_nodes:
        pos[node][0] += 0.5  # Shift right

    # Draw edges
    evolution_edges = [
        (u, v) for u, v, d in G.edges(data=True) if d.get("edge_type") == "evolution"
    ]
    counter_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("edge_type") == "counter"]

    nx.draw_networkx_edges(
        G,
        pos,
        edgelist=evolution_edges,
        edge_color="gray",
        arrows=True,
        arrowsize=20,
        width=2,
        alpha=0.5,
        ax=ax,
        connectionstyle="arc3,rad=0.1",
    )

    nx.draw_networkx_edges(
        G,
        pos,
        edgelist=counter_edges,
        edge_color="red",
        arrows=True,
        arrowsize=25,
        width=3,
        alpha=0.7,
        ax=ax,
        style="dashed",
        connectionstyle="arc3,rad=0.2",
    )

    # Draw nodes
    nx.draw_networkx_nodes(
        G,
        pos,
        node_color=node_colors,
        node_size=node_sizes,
        alpha=0.9,
        ax=ax,
        edgecolors="black",
        linewidths=2,
    )

    # Draw labels
    nx.draw_networkx_labels(G, pos, node_labels, font_size=7, font_weight="bold", ax=ax)

    # Title and legend
    ax.set_title(
        "M25 Co-Evolution: Counter-Tactic Network\nDirected Graph of Strategic Relationships",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )

    legend_elements = [
        mpatches.Patch(facecolor="#4A90E2", edgecolor="black", label="Team A Tactics"),
        mpatches.Patch(facecolor="#E24A4A", edgecolor="black", label="Team B Tactics"),
        plt.Line2D([0], [0], color="gray", linewidth=2, label="Evolution (same team)"),
        plt.Line2D(
            [0], [0], color="red", linewidth=3, linestyle="--", label="Counters (cross-team)"
        ),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=10, framealpha=0.9)

    ax.axis("off")

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def plot_emergent_behavior_timeline(team_b: list, out_path: Path):
    """Figure 8: Emergent Behavior Timeline - qualitative observations."""
    accepted = [e for e in team_b if e["verdict"] == "confirmed"]

    if len(accepted) < 2:
        print("Not enough accepted champions for behavior timeline")
        return

    _fig, ax = plt.subplots(figsize=(16, 8))

    # Define behavior phases based on strategy analysis
    phases = []

    for i, entry in enumerate(accepted):
        round_num = entry["generation"]
        fitness = entry["fitness"]
        strategy = extract_strategy_name(entry["hypothesis_tested"])
        mechanism = entry.get("mechanism_expected", "")
        tactic_type = categorize_tactic(entry["hypothesis_tested"], mechanism)

        # Analyze what's new in this phase
        if i == 0:
            behavior = "Direct pursuit with no coordination"
            weakness = "Predictable movement, vulnerable to kiting"
            innovation = "Baseline (inherited from pursuit_v1)"
        elif "Coordination" in tactic_type or "coordinat" in mechanism.lower():
            behavior = "Message-based target claiming"
            weakness = "Still chases retreating enemies"
            innovation = "First use of communication"
        elif "Prediction" in tactic_type or "predict" in mechanism.lower():
            behavior = "Predicts enemy retreat vectors"
            weakness = "Reactive rather than proactive"
            innovation = "Anticipatory positioning"
        elif "Formation" in tactic_type or "spread" in mechanism.lower():
            behavior = "Formation control with zone coverage"
            weakness = "Initial formation not yet optimized"
            innovation = "Spatial distribution tactics"
        elif "Zone" in tactic_type or "baiting" in mechanism.lower():
            behavior = "Coordinated baiting and zone control"
            weakness = "None discovered (plateau reached)"
            innovation = "Multi-drone coordination patterns"
        else:
            behavior = "Tactical refinement"
            weakness = "Incremental improvement"
            innovation = "Parameter tuning"

        phases.append(
            {
                "round": round_num,
                "fitness": fitness,
                "strategy": strategy,
                "tactic_type": tactic_type,
                "behavior": behavior,
                "weakness": weakness,
                "innovation": innovation,
            }
        )

    # Plot timeline
    ax.set_xlim(-5, max(p["round"] for p in phases) + 10)
    ax.set_ylim(-1.5, 1.5)

    # Draw fitness background
    rounds = [p["round"] for p in phases]
    fitness_vals = [p["fitness"] for p in phases]

    ax.fill_between(rounds, fitness_vals, 0, alpha=0.2, color="blue", label="Fitness")
    ax.plot(
        rounds,
        fitness_vals,
        "o-",
        color="blue",
        linewidth=2,
        markersize=8,
        label="Champion Fitness",
    )

    # Zero line
    ax.axhline(0, color="gray", linestyle="--", linewidth=1, alpha=0.5, label="Draw Parity")

    # Annotate each phase
    for i, phase in enumerate(phases):
        round_num = phase["round"]
        fitness = phase["fitness"]

        # Alternate annotation positions
        y_offset = 0.8 if i % 2 == 0 else -0.8
        y_text = fitness + y_offset

        # Draw annotation box
        box_text = f"Round {round_num}\n{phase['strategy'][:25]}\n\n"
        box_text += f"Behavior:\n{phase['behavior']}\n\n"
        box_text += f"Innovation:\n{phase['innovation']}\n\n"
        box_text += f"Weakness:\n{phase['weakness']}"

        # Color based on fitness
        if fitness > 0.5:
            box_color = "#D5F4E6"
            edge_color = "#27AE60"
        elif fitness > 0:
            box_color = "#FCF3CF"
            edge_color = "#F39C12"
        elif fitness > -0.5:
            box_color = "#FADBD8"
            edge_color = "#E74C3C"
        else:
            box_color = "#E8E8E8"
            edge_color = "#95A5A6"

        # Annotation
        ax.annotate(
            box_text,
            xy=(round_num, fitness),
            xytext=(round_num, y_text),
            fontsize=7,
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor=box_color,
                edgecolor=edge_color,
                linewidth=2,
                alpha=0.9,
            ),
            arrowprops=dict(arrowstyle="->", color=edge_color, linewidth=2),
            verticalalignment="center",
            horizontalalignment="center",
        )

    # Styling
    ax.set_xlabel("Round Number", fontsize=12, fontweight="bold")
    ax.set_ylabel("Fitness", fontsize=12, fontweight="bold")
    ax.set_title(
        "M25 Co-Evolution: Emergent Behavior Timeline\nQualitative Analysis of Tactical Development (Team B)",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.grid(True, alpha=0.3, axis="x", linestyle=":", linewidth=0.5)
    ax.legend(loc="lower right", fontsize=10, framealpha=0.9)

    # Add phase labels
    phase_boundaries = [0] + [phases[i]["round"] for i in range(1, len(phases))]
    phase_labels = ["Bootstrap", "Coordination", "Prediction", "Formation", "Zone Control"]

    for i in range(min(len(phase_boundaries) - 1, len(phase_labels))):
        start = phase_boundaries[i]
        end = phase_boundaries[i + 1] if i + 1 < len(phase_boundaries) else max(rounds) + 5
        mid = (start + end) / 2

        ax.text(
            mid,
            -1.3,
            phase_labels[i],
            fontsize=10,
            fontweight="bold",
            ha="center",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", alpha=0.7),
        )

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    print(f"✓ Saved: {out_path}")
    plt.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_tactic_relationships.py <run_dir>")
        print("Example: python visualize_tactic_relationships.py data/runs/m25_coevolve_100r")
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
    print(
        f"  Team A: {len(team_a)} rounds ({len([e for e in team_a if e['verdict'] == 'confirmed'])} accepted)"
    )
    print(
        f"  Team B: {len(team_b)} rounds ({len([e for e in team_b if e['verdict'] == 'confirmed'])} accepted)"
    )
    print()

    print("Generating tactic relationship visualizations...")

    plot_strategy_progression_tree(team_b, output_dir / "fig6_strategy_progression.png")
    plot_counter_tactic_network(team_a, team_b, output_dir / "fig7_counter_tactic_network.png")
    plot_emergent_behavior_timeline(team_b, output_dir / "fig8_emergent_behaviors.png")

    print()
    print(f"✓ All visualizations saved to: {output_dir}")
    print()
    print("Generated files:")
    print("  6. fig6_strategy_progression.png     - Hierarchical descent tree")
    print("  7. fig7_counter_tactic_network.png   - Network graph of who counters whom")
    print("  8. fig8_emergent_behaviors.png       - Qualitative behavior timeline")


if __name__ == "__main__":
    main()
