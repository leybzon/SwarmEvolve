# SwarmEvolve M25 Co-Evolution Presentation

**A 15-minute technical presentation showcasing LLM-guided co-evolution for drone swarm combat.**

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

## Slide Structure (12 slides, ~15 minutes)

1. **The Hook** (1 min) - Three approaches to programming swarms
2. **Title Slide** (30 sec) - Introduction
3. **The Deterministic Crucible** (1.5 min) - Architecture constraints
4. **Starting & Restarting Evolution** (2 min) - Safety guardrails and AST injection
5. **The Co-Evolution Challenge** (1.5 min) - Team A vs Team B setup
6. **The Red Queen Effect** (1.5 min) - Fitness reversal results
7. **The Tactical Staircase** (2 min) - 6 phases of evolution
8. **Learning Speed Comparison** (1 min) - Co-evolution accelerates learning by 35%
9. **Complexity & Fragility** (1.5 min) - Code growth and champion fragility
10. **Democratizing AI Game Theory** (1.5 min) - $10 vs $1M comparison
11. **Video Demo** (1.5 min) - Round 41 zone control in action
12. **Conclusion & Future Work** (1 min) - Key findings and next steps

**Total:** ~15 minutes + 5 minutes Q&A buffer

## Speaker Notes

Press **S** to enter speaker notes mode. Each slide has detailed presenter notes to guide your delivery.

## Assets Included

- **8 PNG figures** (3.1 MB total):
  - `fig1_arms_race_timeline.png`
  - `fig2_champion_staircase.png`
  - `fig3_red_queen_effect.png`
  - `fig4_strategy_timeline.png`
  - `fig5_code_complexity.png`
  - `fig6_strategy_progression.png`
  - `fig7_counter_tactic_network.png`
  - `fig8_emergent_behaviors.png`

- **4 MP4 videos** (3.2 MB total):
  - `m25_round01_1.mp4` - Round 1 baseline (Team B losing)
  - `m25_round13_13.mp4` - Round 13 parity
  - `m25_round31_31.mp4` - Round 31 breakthrough
  - `m25_round41_41.mp4` - Round 41 final champion

## Technical Details

- **Framework:** reveal.js 5.0.4 (loaded from CDN)
- **Total file size:** ~6.5 MB (presentation + assets)
- **Browser compatibility:** All modern browsers (Chrome, Firefox, Safari, Edge)
- **Offline capable:** Yes (after first load, CDN assets are cached)
- **Print to PDF:** Use Chrome's print function with "Save as PDF"

## Customization

### Change Theme
Edit line 10 in `index.html`:
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5.0.4/dist/theme/black.css">
```
Available themes: `black`, `white`, `league`, `beige`, `sky`, `night`, `serif`, `simple`, `solarized`

### Adjust Timing
Modify slide-specific timing in the `<section>` tags or add auto-slide timing:
```javascript
Reveal.initialize({
    autoSlide: 5000, // Auto-advance every 5 seconds
    // ... other config
});
```

### Color Scheme
Team A (blue): `#4285f4`
Team B (red): `#ea4335`
Highlight (green): `#34a853`

Edit CSS variables in `<style>` section to customize colors.

## Presenting Tips

1. **Start with the video in Slide 11** - Show the final result first to hook the audience, then go back to Slide 1 to explain how we got there
2. **Use fragments sparingly** - Slides 4 and 7 have fragments (step-through animations). Press Space to reveal them sequentially.
3. **Emphasize the $10 cost** - This is the killer value proposition in Slide 10
4. **Let the video play fully** - Slide 11's video is 234 ticks (~10 seconds). Let it run without narration, then explain what happened.
5. **Practice the 15-minute timing** - Use speaker notes mode (S) to rehearse with notes visible

## Troubleshooting

**Videos not playing?**
- Ensure you're viewing via `http://` (not `file://`) - use Python's HTTP server
- Check browser console for errors
- MP4 codec may not be supported on older browsers

**Images not loading?**
- Verify all PNG/MP4 files are in the same directory as `index.html`
- Check file permissions

**Presentation looks wrong?**
- Clear browser cache
- Ensure reveal.js CDN is accessible (requires internet on first load)
- Try a different browser

## Reproduction

To regenerate the M25 experiment data and videos:

```bash
# From repository root
cd /Users/yevgeniy.leybzon/Documents/DroneEvolution

# Run M25 experiment (95 rounds, ~1.5 hours)
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

# Generate visualizations
python3 scripts/visualize_coevolve.py data/runs/m25_coevolve_100r/journal.jsonl
python3 scripts/visualize_strategy_evolution.py data/runs/m25_coevolve_100r/journal.jsonl
python3 scripts/visualize_tactic_relationships.py data/runs/m25_coevolve_100r/journal.jsonl

# Generate videos
python3 scripts/generate_m25_videos.py
```

## License

This presentation is part of the SwarmEvolve project. See repository root for license details.

---

**Questions or feedback?** Open an issue on the GitHub repository.
