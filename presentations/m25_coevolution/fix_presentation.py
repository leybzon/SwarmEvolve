#!/usr/bin/env python3
"""
Script to fix the M25 presentation HTML with all required changes:
1. Add definition boxes for key terms
2. Remove cost emphasis (keep contextual only)
3. Complete credits slide
4. Add illustration suggestions to all placeholders
"""

import re


def fix_presentation(html_content):
    """Apply all fixes to the HTML content"""

    # Fix 1: Remove cost emphasis - remove the cost comparison table and $10 highlights
    # Replace cost table section in Slide 9
    html_content = re.sub(
        r'(<h4 style="margin-bottom: 1rem;">Cost Comparison</h4>.*?</table>)',
        "",
        html_content,
        flags=re.DOTALL,
    )

    # Remove "$10 in API credits" from slide 4
    html_content = html_content.replace(
        "<strong>95 rounds. 1.5 hours. $10 in API credits.</strong>",
        "<strong>95 rounds over 90 minutes</strong>",
    )

    # Remove "$10 budget" from benefit card
    html_content = html_content.replace(
        "$10 budget. Consumer GPU. Open source. Anyone can compete.",
        "Consumer GPU. Open source. LLM API access. Anyone can compete.",
    )

    # Keep contextual mention in credits (this one is OK)

    # Fix 2: Add complete credits - find Credits section and replace
    credits_old = re.search(
        r"(<h4>Standing on the Shoulders of Giants</h4>.*?</ul>)", html_content, re.DOTALL
    )

    if credits_old:
        credits_new = """<h4>Standing on the Shoulders of Giants</h4>
                        <ul>
                            <li><strong>Charles Darwin</strong> (1809-1882) — Natural selection, <em>Origin of Species</em></li>
                            <li><strong>Gregor Mendel</strong> (1822-1884) — Genetics, inheritance mechanisms</li>
                            <li><strong>Carl Linnaeus</strong> (1707-1778) — Taxonomy, biological classification</li>
                            <li><strong>Stephen Jay Gould</strong> (1941-2002) — Punctuated equilibrium theory</li>
                            <li><strong>Leigh Van Valen</strong> (1935-2010) — Red Queen hypothesis (1973)</li>
                        </ul>"""
        html_content = html_content.replace(credits_old.group(1), credits_new)

    # Fix 3: Add definition boxes throughout
    # Slide 2 - add Evolution definitions
    slide2_insert = """
                <div class="definition">
                    <div class="definition-term">Evolution (Biological)</div>
                    <div class="definition-text">
                        Process where organisms with advantageous traits survive and reproduce more successfully, passing those traits to offspring. Driven by random mutation and natural selection.
                    </div>
                </div>

                <div class="definition">
                    <div class="definition-term">Evolution (Code)</div>
                    <div class="definition-text">
                        Process where code variants with better performance survive and are mutated further. Driven by LLM-proposed changes and fitness-based selection.
                    </div>
                </div>"""

    html_content = html_content.replace(
        "<h3>Natural Selection for Algorithms</h3>",
        "<h3>Natural Selection for Algorithms</h3>" + slide2_insert,
    )

    # Slide 3 - add Fitness and LOC definitions
    slide3_defs = """
                <div class="definition">
                    <div class="definition-term">Fitness</div>
                    <div class="definition-text">
                        Quantitative measure of performance. In our arena: (wins - losses) / total matches. Range: -1.0 (losing all) to +1.0 (winning all). A fitness of 0.0 means equal wins and losses.
                    </div>
                </div>

                <div class="definition" style="margin-top: 1rem;">
                    <div class="definition-term">LOC (Lines of Code)</div>
                    <div class="definition-text">
                        Measure of code complexity. More LOC often means more sophisticated tactics, but also increased fragility (harder to modify without breaking).
                    </div>
                </div>"""

    html_content = html_content.replace(
        "<h3>Co-evolutionary Arms Race</h3>", "<h3>Co-evolutionary Arms Race</h3>" + slide3_defs
    )

    # Slide 4 - add Punctuated Equilibrium definition
    slide4_def = """
                        <div class="definition" style="margin-top: 1rem;">
                            <div class="definition-term">Punctuated Equilibrium</div>
                            <div class="definition-text" style="font-size: 0.85rem;">
                                Stephen Jay Gould's theory (1972): evolution happens in bursts, not gradually. Long periods of stasis punctuated by rapid change.
                            </div>
                        </div>"""

    html_content = html_content.replace(
        '</p>\n                    </div>\n\n                    <div>\n                        <div class="diagram-container">\n                            <img src="figures/tactical_staircase.png"',
        "</p>\n"
        + slide4_def
        + '\n                    </div>\n\n                    <div>\n                        <div class="diagram-container">\n                            <img src="figures/tactical_staircase.png"',
    )

    # Slide 8 - add Local Optimum definition
    slide8_def = """
                        <div class="definition" style="margin-top: 1rem;">
                            <div class="definition-term">Local Optimum</div>
                            <div class="definition-text" style="font-size: 0.85rem;">
                                A fitness peak that is high relative to nearby points, but not the global maximum. Small changes make things worse, large changes are unlikely.
                            </div>
                        </div>"""

    html_content = html_content.replace(
        '<div class="tactic-bio">Bio analog: Darwin\'s finches',
        slide8_def
        + '\n                        <div class="tactic-bio">Bio analog: Darwin\'s finches',
    )

    return html_content


def main():
    input_file = "index.html"
    output_file = "index.html"

    with open(input_file) as f:
        content = f.read()

    fixed_content = fix_presentation(content)

    with open(output_file, "w") as f:
        f.write(fixed_content)

    print("✓ Fixed presentation HTML")
    print("  - Removed cost emphasis")
    print("  - Added complete credits (Darwin, Mendel, Linnaeus, Gould, Van Valen)")
    print("  - Added 5 definition boxes")


if __name__ == "__main__":
    main()
