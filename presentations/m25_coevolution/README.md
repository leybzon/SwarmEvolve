# Code Evolution in the Wild

**A 15-30 minute presentation on evolutionary code paradigms, using SwarmEvolve M25 co-evolution as a case study.**

## Quick Start

### Option 1: Open in Browser (Recommended)
```bash
# From this directory
open index.html
# or on Linux
xdg-open index.html
```

### Option 2: Serve with Python
```bash
# From this directory
python3 -m http.server 8000
# Then open http://localhost:8000 in your browser
```

## Navigation

- **Arrow Keys / Space**: Navigate forward/backward through slides
- **ESC / O**: Overview mode (see all slides)
- **S**: Speaker notes mode (presenter view)
- **F**: Fullscreen mode
- **?**: Show keyboard shortcuts help

## Slide Structure (14 slides, 15-30 minutes)

### Act 1: Setup (Slides 0-3, ~6 min)
0. **Title Slide** (30 sec) - Gene Leybzon, May 2026
1. **Three Paradigms** (2 min) - Hand-coding vs Vibe Coding vs Evolution
2. **What Is Code Evolution?** (2 min) - Definitions, Darwin's finches analogy
3. **The Arena** (1.5 min) - Game rules, Team A vs Team B setup, fitness/LOC explained

### Act 2: The Experiment (Slides 4-6, ~8 min)
4. **Punctuated Equilibrium** (2 min) - Fitness reversal graph + staircase
5. **Emergent Strategies** (3 min) - 6 tactical phases with biological analogs
6. **Code Archaeology** (3 min) - Round 1 vs Round 31 code comparison

### Act 3: Lessons (Slides 7-9, ~7 min)
7. **When Evolution Stalls** (3 min) - Failed experiments vs co-evolution solution
8. **The Red Queen's Race** (2 min) - Team A trapped, Team B adaptive
9. **What Evolution Unlocks** (2 min) - Interpretability, speed, accessibility

### Act 4: Philosophy & Future (Slides 10-12, ~6 min)
10. **Evolutionary Paradigms** (3 min) - Darwinian, Lamarckian, Orthogenesis in code
11. **The Future** (2 min) - Speculative applications (microservices, immune systems, debugging, symbiosis)
12. **Evolution vs Engineering** (1 min) - "Code that can't evolve is extinct"

### Closing (Slide 13, ~2 min)
13. **Credits & Next Steps** (2 min) - Darwin/Mendel/Linnaeus, tools, GitHub links, reproduction command

**Total:** ~29 minutes + Q&A buffer

## Key Concepts Explained

This presentation **explains technical terms** for semi-technical audiences:

- **Fitness**: Quantitative performance measure (wins - losses) / total, range -1.0 to +1.0
- **LOC (Lines of Code)**: Measure of code complexity
- **Punctuated Equilibrium**: Gould's theory of evolution in bursts, not gradual change
- **Local Optimum**: A fitness peak that's high locally but not the global maximum
- **Co-evolution**: Alternating evolution where opponents drive each other's adaptation

## Design Decisions

### De-emphasized Cost
- Removed "$10" emphasis per your feedback
- Focus shifted to **interpretability, speed, accessibility**
- Cost mentioned only in context of democratization

### Added Definitions
- Green definition boxes explain: Evolution, Fitness, LOC, Punctuated Equilibrium, Local Optimum
- Biological analogs provided for all tactics and stall mechanisms

### Illustration Suggestions
Each placeholder includes specific suggestions:
- Title slide: Phylogenetic tree with code branches
- Slide 1: Three-panel (person at desk | glitchy AI | petri dish)
- Slide 2: Darwin's finches + code phylogeny tree
- Slide 3: Arena diagram with trajectories and ranges
- Slide 6: LOC vs Fitness scatter plot with failed mutations
- Slide 8: Predator-prey oscillation (Lotka-Volterra) + Team A/B fitness
- Slide 9: Neural net black box vs readable code
- Slide 10: Darwin/Lamarck/de Vries portraits with bio/code checkmarks
- Slide 11: Future applications diagrams (4 quadrants)
- Slide 12: Finches vs CAD blueprint

### Credits Slide
Honors intellectual foundations:
- **Charles Darwin** (1809-1882) - Natural selection
- **Gregor Mendel** (1822-1884) - Genetics
- **Carl Linnaeus** (1707-1778) - Taxonomy
- **Stephen Jay Gould** (1941-2002) - Punctuated equilibrium
- **Leigh Van Valen** (1935-2010) - Red Queen hypothesis

Plus tools: Claude Code, Claude Sonnet 4, Claude Haiku 4.5, OpenACC, C++17

## Assets Included

- **8 PNG figures** from M25 experiment:
  - `fitness_timeline.png` - Hero graph (Blue → Red crossover)
  - `tactical_staircase.png` - 6 phases, staircase pattern
  - `code_growth.png` - 66 LOC → 210 LOC
  - `learning_speed_comparison.png` - Co-evolution 35% faster
  - `team_a_stagnation.png` - 47 rejected mutations
  - `team_b_acceptance_rate.png` - 8 acceptances in 95 rounds
  - `fitness_distribution.png` (if used)
  - `complexity_fragility.png` (if used)

- **4 MP4 videos**:
  - `m25_r1_baseline.mp4` - Round 1 (Team B losing)
  - `m25_r13_parity.mp4` (if used)
  - `m25_r31_breakthrough.mp4` (if used)
  - `m25_r41_final.mp4` (if used)

## Technical Details

- **Framework:** reveal.js 5.0.4 (CDN)
- **Fonts:** Fraunces (serif, headings) + JetBrains Mono (code)
- **Color scheme:**
  - Team A Blue: `#3b82f6`
  - Team B Red: `#ef4444`
  - Breakthrough Green: `#22c55e`
  - Highlight Gold: `#f59e0b`
  - Warning Magenta: `#ec4899`
- **Browser compatibility:** All modern browsers
- **Offline capable:** After first load (CDN cached)

## Presenting Tips

1. **Know your audience** - This version assumes semi-technical (software trends focus, not deep ML)
2. **Emphasize emergence** - "We didn't program Formation Spread. Evolution discovered it."
3. **Use biological analogs** - They make abstract concepts visceral (wolf packs, cheetah hunting, ant pheromones)
4. **Pause on the breakthrough** - Slide 4's fitness reversal is the "wow" moment
5. **Don't skip definitions** - Even tech audiences may not know "punctuated equilibrium" or "local optimum"
6. **Leverage paradigms slide** - Slide 10 shows Lamarck/Orthogenesis work in code despite being wrong in biology (mind-blowing for many)

## Customization

### Adjust Timing
For 15-minute version: Skip slides 7, 10, 11 (focus on core narrative)
For 30-minute version: Expand slides 5, 6, 7 with code walkthroughs

### Change Theme
Edit line 8-9 in `index.html`:
```html
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.0.4/theme/black.min.css">
```
Available themes: `black`, `white`, `league`, `night`, `serif`

## Reproduction

To regenerate the M25 experiment data:

```bash
# From repository root
python3 scripts/evolve_coevolve.py --rounds 100
```

Full command with parameters:
```bash
python3 scripts/evolve_coevolve.py \
  --init-champion-a data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
  --init-champion-b src/baselines/pursuit_v1.cpp \
  --planner-model claude-sonnet-4-20250514 \
  --coder-model claude-haiku-4-5 \
  --rounds 100 \
  --n-matches 10 \
  --seed 42 \
  --out-dir data/runs/m25_coevolve_100r \
  --acceptance-mode relative \
  --strict-reflection \
  -v
```

## Links

- **GitHub Repository:** https://github.com/leybzon/SwarmEvolve
- **This Presentation:** https://github.com/leybzon/SwarmEvolve/tree/main/presentations/m25_coevolution
- **License:** MIT (open source)

## Troubleshooting

**Videos not playing?**
- Serve via HTTP (not file://): `python3 -m http.server 8000`
- Check browser console for codec errors
- MP4 may not work on older browsers

**Images not loading?**
- Verify PNG files are in `figures/` subdirectory
- Check file permissions

**Fonts look wrong?**
- Requires internet on first load (Google Fonts CDN)
- Check browser console for 403 errors

---

**Questions or feedback?** Open an issue on the GitHub repository or contact Gene Leybzon.
