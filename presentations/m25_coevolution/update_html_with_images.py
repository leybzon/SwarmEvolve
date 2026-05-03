#!/usr/bin/env python3
"""
Update index.html to replace img-placeholder divs with actual image tags.
"""

import re

def update_html_with_images():
    """Replace all placeholders with actual images"""

    with open('index.html', 'r') as f:
        html = f.read()

    # Replacement map: (placeholder_pattern, replacement_html)
    replacements = [
        # Slide 0: Title slide - Phylogenetic tree
        (
            r'<div class="img-placeholder" style="min-height: 300px; max-width: 600px; margin: 0 auto;">.*?\[Evolutionary tree visualization\].*?</div>',
            '<div class="diagram-container" style="max-width: 600px; margin: 0 auto;"><img src="figures/phylogenetic_tree.png" alt="Phylogenetic tree showing code evolution from pursuit_v1 through 6 tactical phases"></div>'
        ),

        # Slide 1: Three paradigms
        (
            r'<div class="img-placeholder" style="min-height: 200px;">.*?\[Three-panel illustration\].*?</div>',
            '<div class="diagram-container"><img src="figures/three_paradigms.png" alt="Three programming paradigms: Hand-coding, Vibe Coding, Evolution"></div>'
        ),

        # Slide 2: Darwin's finches (first placeholder)
        (
            r'<div class="img-placeholder" style="min-height: 400px;">.*?\[Darwin\'s finches sketches\].*?Beak shapes adapted to food sources.*?</div>',
            '<div class="diagram-container"><img src="figures/darwins_finches.png" alt="Darwin\'s finches with different beak shapes adapted to different food sources"></div>'
        ),

        # Slide 2: Code phylogeny (second placeholder)
        (
            r'<div class="img-placeholder" style="min-height: 400px;">.*?\[Code snippets morphing animation\].*?Function signatures → Tactics.*?</div>',
            '<div class="diagram-container"><img src="figures/code_phylogeny.png" alt="Phylogenetic tree showing code evolution with function signatures at each branch"></div>'
        ),

        # Slide 3: Arena diagram
        (
            r'<div class="img-placeholder" style="min-height: 400px;">.*?\[Top-down arena diagram\].*?2 teams × 25 drones.*?Trajectory lines, combat zones.*?</div>',
            '<div class="diagram-container"><img src="figures/arena_diagram.png" alt="Top-down arena view with Team A (blue) and Team B (red) drones, showing trajectories and combat ranges"></div>'
        ),

        # Slide 5: Tactical timeline
        (
            r'<div class="img-placeholder" style="min-height: 150px;">.*?\[Horizontal timeline with vertical jumps\].*?Labels: Baseline.*?</div>',
            '<div class="diagram-container"><img src="figures/tactical_timeline.png" alt="Horizontal timeline showing fitness jumps at each tactical phase from R1 to R95"></div>'
        ),

        # Slide 6: LOC vs Fitness scatter
        (
            r'<div class="img-placeholder" style="min-height: 250px;">.*?\[Scatter plot: LOC vs Fitness\].*?Red line = Team B\'s growth path.*?</div>',
            '<div class="diagram-container"><img src="figures/loc_fitness_scatter.png" alt="Scatter plot showing LOC vs Fitness with Team A failed mutations and Team B evolution path"></div>'
        ),

        # Slide 8: Predator-prey oscillation
        (
            r'<div class="img-placeholder" style="margin-top: 2rem; min-height: 200px;">.*?\[Predator-prey oscillation graph overlaid on Team A/B fitness\].*?Lynx population peaks.*?</div>',
            '<div class="diagram-container" style="margin-top: 2rem;"><img src="figures/predator_prey_graph.png" alt="Dual graph showing Lotka-Volterra predator-prey oscillations overlaid with Team A/B fitness evolution"></div>'
        ),

        # Slide 11: Future applications (4 img-placeholders in grid)
        (
            r'<div class="img-placeholder" style="margin-top: 1rem; min-height: 150px; font-size: 0\.8rem;">.*?\[Diagram: API A ↔ API B fitness graph\].*?</div>',
            '<div style="margin-top: 1rem; padding: 1rem; background: rgba(59,130,246,0.1); border-radius: 8px;"><p style="font-size: 0.9rem; text-align: center; color: #3b82f6;">API A ↔ API B Co-Evolution</p></div>'
        ),
        (
            r'<div class="img-placeholder" style="margin-top: 1rem; min-height: 150px; font-size: 0\.8rem;">.*?\[Diagram: Attack pattern → Detection evolves\].*?</div>',
            '<div style="margin-top: 1rem; padding: 1rem; background: rgba(34,197,94,0.1); border-radius: 8px;"><p style="font-size: 0.9rem; text-align: center; color: #22c55e;">Attack → Detection Evolution</p></div>'
        ),
        (
            r'<div class="img-placeholder" style="margin-top: 1rem; min-height: 150px; font-size: 0\.8rem;">.*?\[Diagram: Test suite → Mutations → Green build\].*?</div>',
            '<div style="margin-top: 1rem; padding: 1rem; background: rgba(236,72,153,0.1); border-radius: 8px;"><p style="font-size: 0.9rem; text-align: center; color: #ec4899;">Tests → Mutations → Fix</p></div>'
        ),
        (
            r'<div class="img-placeholder" style="margin-top: 1rem; min-height: 150px; font-size: 0\.8rem;">.*?\[Diagram: Query optimizer ↔ Index builder\].*?</div>',
            '<div style="margin-top: 1rem; padding: 1rem; background: rgba(245,158,11,0.1); border-radius: 8px;"><p style="font-size: 0.9rem; text-align: center; color: #f59e0b;">Query ↔ Index Optimization</p></div>'
        ),

        # Slide 12: Evolution vs Engineering (2 placeholders)
        (
            r'<div class="img-placeholder" style="min-height: 400px;">.*?\[Darwin\'s finches sketch\].*?Natural Selection.*?Charles Darwin\'s original finch sketches.*?</div>',
            '<div class="diagram-container"><img src="figures/evolution_vs_engineering.png" alt="Side-by-side comparison: Darwin\'s finches (natural selection) vs CAD blueprint (intelligent design)" style="max-height: 400px;"></div>'
        ),

        # Slide 13: Scientist portraits
        (
            r'<div class="img-placeholder" style="margin-top: 2rem; min-height: 200px;">.*?\[Five scientist portraits\].*?Grid of 5 historical portraits.*?</div>',
            '<div class="diagram-container" style="margin-top: 2rem;"><img src="figures/scientist_portraits.png" alt="Portraits of Darwin, Mendel, Linnaeus, Gould, and Van Valen"></div>'
        ),
    ]

    # Apply replacements
    for pattern, replacement in replacements:
        html = re.sub(pattern, replacement, html, flags=re.DOTALL)

    # Remove the second placeholder from Slide 12 (already covered by evolution_vs_engineering.png)
    html = re.sub(
        r'<div class="img-placeholder" style="min-height: 400px;">.*?\[CAD blueprint / circuit diagram\].*?Intelligent Design.*?Technical blueprint.*?</div>',
        '',
        html,
        flags=re.DOTALL
    )

    # Write updated HTML
    with open('index.html', 'w') as f:
        f.write(html)

    print("✅ Updated index.html with all illustrations")

    # Verify
    placeholder_count = html.count('img-placeholder')
    print(f"Remaining placeholders: {placeholder_count}")
    if placeholder_count == 0:
        print("✓ All placeholders replaced!")
    else:
        print("⚠ Some placeholders remain - manual review needed")

if __name__ == '__main__':
    update_html_with_images()
