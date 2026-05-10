#!/usr/bin/env python3
"""
Fix slide #1 overflow by reducing margins, padding, and heights.
"""

import re


def fix_slide1_overflow():
    """Fix slide 1 vertical overflow"""

    with open("index.html") as f:
        html = f.read()

    # Reduce .three-paths margin-top
    html = re.sub(
        r"\.three-paths \{[^}]*?margin-top: 3rem;",
        lambda m: m.group(0).replace("margin-top: 3rem;", "margin-top: 1.5rem;"),
        html,
    )

    # Reduce .path min-height and padding
    html = re.sub(
        r"(\.path \{[^}]*?)min-height: 350px;", r"\1min-height: 280px;", html, flags=re.DOTALL
    )

    html = re.sub(
        r"(\.path \{[^}]*?)padding: 2rem 1\.5rem;",
        r"\1padding: 1.5rem 1rem;",
        html,
        flags=re.DOTALL,
    )

    # Reduce margin-top for the diagram after three-paths
    html = re.sub(
        r'(<div style="margin-top: 3rem;">.*?<div class="diagram-container"><img src="figures/three_paradigms\.png")',
        r'<div style="margin-top: 1.5rem;">\n                    <div class="diagram-container"><img src="figures/three_paradigms.png"',
        html,
        flags=re.DOTALL,
    )

    # Reduce three_paradigms.png max-height if needed
    html = re.sub(
        r'(<img src="figures/three_paradigms\.png"[^>]*?)>', r'\1 style="max-height: 220px;">', html
    )

    with open("index.html", "w") as f:
        f.write(html)

    print("✅ Fixed slide #1 overflow")
    print("Adjustments:")
    print("  - Reduced .three-paths margin-top: 3rem → 1.5rem")
    print("  - Reduced .path min-height: 350px → 280px")
    print("  - Reduced .path padding: 2rem 1.5rem → 1.5rem 1rem")
    print("  - Reduced diagram margin-top: 3rem → 1.5rem")
    print("  - Set three_paradigms.png max-height: 220px")


if __name__ == "__main__":
    fix_slide1_overflow()
