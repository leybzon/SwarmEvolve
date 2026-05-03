#!/usr/bin/env python3
"""
Fix slides that are cut off at the bottom by adjusting image sizes,
margins, font sizes, and restructuring content to fit within viewport.
"""

import re

def fix_slide_overflow():
    """Fix all slides with vertical overflow issues"""

    with open('index.html', 'r') as f:
        html = f.read()

    # Slide 2: Reduce image heights and adjust layout
    # The two-column layout with large images is causing overflow
    html = re.sub(
        r'(<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 3rem; align-items: center; margin-top: 2rem;">.*?<div class="diagram-container"><img src="figures/darwins_finches\.png".*?</div>)',
        r'<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; align-items: center; margin-top: 1rem;">\n                    <div>\n                        <div class="diagram-container"><img src="figures/darwins_finches.png" alt="Darwin\'s finches with different beak shapes adapted to different food sources" style="max-height: 300px;"></div>',
        html,
        flags=re.DOTALL
    )

    html = re.sub(
        r'(<div class="diagram-container"><img src="figures/code_phylogeny\.png".*?</div>)',
        r'<div class="diagram-container"><img src="figures/code_phylogeny.png" alt="Phylogenetic tree showing code evolution with function signatures at each branch" style="max-height: 300px;"></div>',
        html
    )

    # Slide 2: Reduce mutation/selection/iteration section margin
    html = re.sub(
        r'<div style="display: grid; grid-template-columns: repeat\(3, 1fr\); gap: 2rem; margin-top: 3rem;">',
        r'<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem; margin-top: 1.5rem;">',
        html
    )

    # Slide 3: Reduce arena diagram and adjust layout
    html = re.sub(
        r'(<div class="diagram-container">\n                        <div class="diagram-container"><img src="figures/arena_diagram\.png".*?</div>)',
        r'<div class="diagram-container">\n                        <div class="diagram-container"><img src="figures/arena_diagram.png" alt="Top-down arena view with Team A (blue) and Team B (red) drones, showing trajectories and combat ranges" style="max-height: 280px;"></div>',
        html
    )

    # Slide 3: Reduce video height
    html = re.sub(
        r'<video controls loop style="width: 100%; max-height: 200px;">',
        r'<video controls loop style="width: 100%; max-height: 150px;">',
        html
    )

    # Slide 5: Reduce tactical timeline image height
    html = re.sub(
        r'(<div style="margin: 1\.5rem 0;">.*?<div class="diagram-container"><img src="figures/tactical_timeline\.png")',
        r'<div style="margin: 1rem 0;">\n                    <div class="diagram-container"><img src="figures/tactical_timeline.png" style="max-height: 150px;"',
        html,
        flags=re.DOTALL
    )

    # Slide 5: Reduce tactic grid margins
    html = re.sub(
        r'<div class="tactic-grid">',
        r'<div class="tactic-grid" style="margin-top: 1rem;">',
        html
    )

    # Slide 5: Make tactic cards more compact
    html = re.sub(
        r'(\.tactic-card \{[^}]*?)padding: 1\.5rem;',
        r'\1padding: 1rem;',
        html
    )

    # Slide 6: Reduce code panel sizes
    html = re.sub(
        r'(\.code-split \{[^}]*?)gap: 1\.5rem;',
        r'\1gap: 1rem;',
        html
    )

    # Slide 6: Reduce scatter plot height
    html = re.sub(
        r'(<div class="diagram-container"><img src="figures/loc_fitness_scatter\.png".*?</div>)',
        r'<div class="diagram-container"><img src="figures/loc_fitness_scatter.png" alt="Scatter plot showing LOC vs Fitness with Team A failed mutations and Team B evolution path" style="max-height: 280px;"></div>',
        html
    )

    # Slide 6: Make code comparison section more compact
    html = re.sub(
        r'(<div class="code-split">.*?</div>\n                </div>\n\n                <p class="emphasis")',
        lambda m: m.group(0).replace('margin-top: 2rem', 'margin-top: 1rem'),
        html,
        flags=re.DOTALL
    )

    # Global: Reduce h2 bottom margin
    html = re.sub(
        r'(\.reveal h2 \{[^}]*?)margin-bottom: 1\.5rem;',
        r'\1margin-bottom: 1rem;',
        html
    )

    # Global: Reduce h3 bottom margin
    html = re.sub(
        r'(\.reveal h3 \{[^}]*?)margin-bottom: 2rem;',
        r'\1margin-bottom: 1rem;',
        html
    )

    # Slide 8: Reduce predator-prey graph height
    html = re.sub(
        r'(<div class="diagram-container" style="margin-top: 2rem;"><img src="figures/predator_prey_graph\.png")',
        r'<div class="diagram-container" style="margin-top: 1rem;"><img src="figures/predator_prey_graph.png" style="max-height: 350px;"',
        html
    )

    # Add max-height constraint to diagram-container images globally
    html = re.sub(
        r'(\.diagram-container img[^}]*?\{[^}]*?)',
        r'\1\n            max-height: 450px;',
        html
    )

    # Reduce reveal.js default margins
    html = re.sub(
        r'margin: 0\.04,',
        r'margin: 0.02,',
        html
    )

    with open('index.html', 'w') as f:
        f.write(html)

    print("✅ Fixed slide overflow issues")
    print("Adjustments made:")
    print("  - Reduced image heights on slides 2, 3, 5, 6, 8")
    print("  - Reduced margins and gaps throughout")
    print("  - Made tactic cards more compact")
    print("  - Reduced heading margins")
    print("  - Added global max-height constraints")

if __name__ == '__main__':
    fix_slide_overflow()
