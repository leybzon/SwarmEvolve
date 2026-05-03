# CLAUDE.md - M25 Co-Evolution Presentation Guidance

This file provides M25-specific guidance for working on the co-evolution presentation. For general presentation principles, **always consult the parent directory files first**.

---

## Reference Documentation (Parent Directory)

Before working on this presentation, read:

1. **`../CLAUDE.md`** - Meta-guidance for presentation work (mindset shift, design principles)
2. **`../CONCEPTS.md`** - Evolutionary theory library (Red Queen, Punctuated Equilibrium, etc.)
3. **`../NARRATIVES.md`** - Story arcs and dramatic structures
4. **`../VISUALS.md`** - Visual design language (color psychology, chart patterns)
5. **`../AUDIENCE_PROFILES.md`** - Adaptation strategies for different audiences

---

## M25 Experiment Summary

### The Story
**David vs Goliath + Red Queen Effect**

- **Team A (Goliath)**: M22 Gen 33 champion, 204 LOC, +1.0 fitness, 100 generations of evolution
- **Team B (David)**: pursuit_v1 baseline, 66 LOC, -0.8 fitness, losing 8/10 matches
- **Challenge**: Can Team B defeat a champion through co-evolution?
- **Result**: YES - fitness reversal at Round 31, final fitness +0.9

### Key Moments (Video Assets Available)

1. **Round 1 (-0.8 fitness)**: Team B baseline, simple pursuit, overwhelmed
2. **Round 13 (0.0 parity)**: First breakthrough - message coordination, kiting
3. **Round 31 (+0.9 dominance)**: BREAKTHROUGH - Formation Spread (80-unit spacing)
4. **Round 41 (+0.9 final)**: Refined zone control, Team A trapped in local optimum

### The Breakthrough Innovation

**Formation Spread** (Round 31):
```cpp
const float min_spacing = 80.0f;  // KEY INNOVATION
// Repulsion forces maintain territorial spacing
// Result: 60% arena coverage, overwhelms Team A's kiting
```

This was NOT programmed by humans - it **emerged** through LLM-guided evolution.

---

## Available Assets

### Figures (8 PNG files in `figures/`)
1. `fitness_timeline.png` - The hero graph (Team A blue, Team B red crossing)
2. `tactical_staircase.png` - Punctuated equilibrium visualization
3. `code_growth.png` - 66 LOC → 210 LOC complexity growth
4. `learning_speed_comparison.png` - Co-evolution 35% faster
5. `team_a_stagnation.png` - 47 rejected mutations (fragile optimum)
6. `team_b_acceptance_rate.png` - 8 acceptances in 95 rounds
7. `fitness_distribution.png` - Power law of innovation
8. `complexity_fragility.png` - LOC vs acceptance rate

### Videos (4 MP4 files in `videos/`)
1. `m25_r1_baseline.mp4` - Team B at -0.8 fitness (the problem)
2. `m25_r13_parity.mp4` - First breakthrough to 0.0 (hope)
3. `m25_r31_breakthrough.mp4` - Formation Spread dominance (climax)
4. `m25_r41_final.mp4` - Final refined strategy (resolution)

---

## M25 Narrative Arc

### Act 1: Setup (Slides 1-5)
**Goal**: Establish the world, introduce the conflict

**Key Message**: "How do we program swarms? Evolution is the third way."

**Emotional Arc**: Curiosity → Interest → Anticipation

**Slides**:
1. Hook: Three programming approaches
2. Title: SwarmEvolve
3. Safety & architecture (loop guards, GPU sandbox)
4. The experiment setup
5. Meet the fighters (Team A vs Team B)

---

### Act 2: Journey (Slides 6-10)
**Goal**: Show the struggle, reveal the breakthrough

**Key Message**: "Team B shouldn't have won. But it did."

**Emotional Arc**: Surprise → Understanding → Respect

**Slides**:
6. Red Queen Effect (fitness reversal graph - THE HERO VISUAL)
7. Tactical Staircase (6 phases of evolution)
7b. Code Evolution (66 LOC → 210 LOC side-by-side)
7c. Tactical Innovations (4 tactic cards)
8. Learning Speed (co-evolution 35% faster)
9. Team A Stagnation (fragile optimum, 47 rejections)

---

### Act 3: Resolution (Slides 11-12)
**Goal**: Prove it works, inspire action

**Key Message**: "Adaptability beats perfection."

**Emotional Arc**: Confidence → Inspiration → Call to Action

**Slides**:
10. Complexity vs Fragility (Team A trapped in local optimum)
11. Video Demo (embed R31 breakthrough)
12. Conclusion (key findings, $10 vs $1M, reproduction command)

---

## Primary Metaphors Used

(See `../CONCEPTS.md` for full details)

1. **Red Queen Effect** (Slide 6)
   - "Running to stand still" - Team A must evolve just to maintain parity
   - Visual: Blue line declining, red line rising, crossover at Round 31

2. **Punctuated Equilibrium** (Slide 7)
   - Evolution as staircase, not smooth curve
   - Visual: 6 tactical phases with sudden jumps

3. **Fragile Optimum** (Slide 9)
   - Team A = Panda's thumb (over-specialized, can't adapt)
   - Visual: 47 rejected mutations, stuck at 204 LOC

4. **Power Law of Innovation** (Slide 7c)
   - Most improvements small (+0.1-0.2 fitness)
   - Rare breakthroughs transformative (+0.9 at R31)

5. **Predator-Prey Arms Race** (Slide 6)
   - Team A kites → Team B predicts → Team A can't counter
   - Visual: Oscillating fitness lines converging at equilibrium

---

## Visual Language

### Color Semantics (Strictly enforced)
- **Team A Blue** (`#4285f4`): Established champion, static perfection, cold logic
- **Team B Red** (`#ea4335`): Scrappy challenger, adaptive, dynamic
- **Breakthrough Green** (`#34a853`): Innovation moments, success
- **Fragility Gray** (`#888`): Failures, stagnation, dead-ends
- **Highlight Gold** (`#f4b400`): Warnings, "vibe coding" critique

### Typography (Current implementation)
- **Slide Titles** (h2): 3rem, bold, white
- **Subtitles** (h3): 2rem, regular, light gray
- **Metric Numbers**: 3-4rem, bold, breakthrough green

### Animations (reveal.js fragments)
- **Slide 6**: Progressive reveal (Team A line → Team B line → crossover)
- **Slide 7**: Fragment per tactical phase (6 steps)
- **Slide 7c**: Fade-in per tactic card (4 cards)

---

## Audience: General Tech (15-minute talk)

**Default configuration** - See `../AUDIENCE_PROFILES.md` for adaptations.

**What they care about**:
- ✅ What is this? (Novel approach to AI programming)
- ✅ Why does it matter? (Democratizes AI research, $10 vs $1M)
- ✅ How is it different? (Emergent tactics, not hand-coded)
- ✅ Can I try it? (Single command reproduction)

**What to emphasize**:
- David vs Goliath narrative
- $10 vs $1M comparison
- Red Queen Effect (engaging metaphor)
- Video demo (visual proof)

**What to skip**:
- AST injection implementation details
- Statistical significance tests
- GPU parallelization architecture

**Code depth**: 5-10 line excerpts only (e.g., Formation Spread repulsion forces)

---

## Adaptation Checklist

If the audience or time changes, consult `../AUDIENCE_PROFILES.md` and adjust:

- **Researchers**: Add statistical tests, ablation studies, threats to validity
- **Engineers**: Show AST injection code, architecture diagram, performance metrics
- **Executives**: Cut to 5 slides (Problem → Solution → Proof → Impact → Action)
- **Students**: Add learning roadmap, project ideas, setup tutorial
- **Popular Science**: Remove all code, add biological metaphors, story arc focus

---

## Key Talking Points (Speaker Notes)

### Slide 6 (Red Queen Effect)
"This graph shows something that shouldn't happen. Team B starts at -0.8 fitness, losing 8 out of 10 matches. Team A is a champion with 100 generations of evolution behind it. By Round 31, Team B is winning 9 out of 10. This is the Red Queen Effect - Team A ran to stand still, but Team B ran faster."

### Slide 7c (Tactical Innovations)
"These four innovations emerged over 95 rounds. Message Coordination in Round 3. Predictive Intercept in Round 13. Formation Spread in Round 31 - this is the breakthrough. Zone Control in Round 41. We didn't program these. The LLM planner discovered them through evolutionary pressure."

### Slide 9 (Team A Stagnation)
"Team A tried to adapt. 47 times. All 47 mutations were rejected. Why? It's trapped in a local optimum - like the panda's thumb. Too specialized to change. Team B started simple, stayed flexible, and won."

### Slide 12 (Conclusion)
"The reproduction command is one line: `python3 scripts/evolve_coevolve.py --rounds 100`. Total cost: $10 in API credits. AlphaStar cost $1 million. This is what democratization looks like."

---

## What Makes M25 Special

1. **First co-evolution experiment**: Alternating teams, arms race dynamics
2. **Fitness reversal**: Underdog defeats established champion
3. **Emergent tactics**: Formation Spread discovered, not programmed
4. **Power law innovation**: 1 massive jump (R31), 7 incremental steps
5. **Learning acceleration**: Co-evolution 35% faster than isolated evolution
6. **Fragile optimum demonstration**: Team A's 47 rejections prove brittleness
7. **Cost democratization**: $10 budget, 1.5 hours, consumer GPU

---

## Anti-Patterns to Avoid (M25-specific)

### ❌ Overclaiming Statistical Significance
- **Problem**: M25 used seed=42 only, n=10 matches per round
- **Fix**: Say "suggestive" or "proof-of-concept", not "statistically proven"

### ❌ Hiding the Struggle
- **Problem**: Showing only Round 31 breakthrough
- **Fix**: Show Rounds 1-12 plateau, build dramatic tension

### ❌ Code Without Context
- **Problem**: Showing Formation Spread code without explaining zone coverage
- **Fix**: Show code + arena visualization + predator-prey metaphor

---

## Presentation Workflow

### 1. Development Mode
```bash
cd presentations/m25_coevolution
open index.html  # macOS
# Or python3 -m http.server 8000 for cross-origin issues
```

### 2. Test on Actual Hardware
- Projector test: Check slide visibility from 20 feet
- Video playback: Ensure MP4s play smoothly
- Font size: Minimum 16px body text readable?

### 3. Speaker Notes
- Press `s` in reveal.js to open speaker view
- Add notes to `<aside class="notes">` tags in HTML

---

## Future Improvements

(Track these as potential enhancements)

- [ ] Add D3.js interactive fitness timeline (zoom, pan, annotations)
- [ ] Create animated tactical evolution (morph code snippets R1 → R31)
- [ ] Add live demo option (run 5-round evolution during talk)
- [ ] Generate alternate versions for other audiences (exec 5-min, researcher 30-min)
- [ ] Add audio narration for asynchronous viewing
- [ ] Create printable handout (PDF export with key graphs)

---

## Quick Reference: M25 Key Metrics

| Metric | Value |
|--------|-------|
| **Total Rounds** | 95 (of 100 planned) |
| **Team B Acceptances** | 8 |
| **Team A Acceptances** | 0 |
| **Team A Rejections** | 47 |
| **Fitness Reversal** | Round 31 |
| **Final Fitness** | +0.9 (Team B), +1.0 → stuck (Team A) |
| **Code Growth** | 66 LOC → 210 LOC (3.2×) |
| **API Cost** | ~$10 |
| **Runtime** | 1.5 hours |
| **Breakthrough** | Formation Spread (80-unit min_spacing) |

---

## Requirements Verification Checklist

**CRITICAL**: Before considering a presentation update complete, verify ALL requirements are actually implemented in the final file. Do NOT rely on memory or assumptions. Use grep/Read tools to confirm.

### Pre-Deployment Checklist

When user requests changes, create a verification checklist and validate AFTER making changes:

1. **Definition Boxes** (`grep -c "class=\"definition\"" index.html`)
   - [ ] Evolution (Biological) definition added
   - [ ] Evolution (Code) definition added
   - [ ] Fitness definition added
   - [ ] LOC (Lines of Code) definition added
   - [ ] Punctuated Equilibrium definition added
   - [ ] Local Optimum definition (if requested)
   - [ ] Co-evolution definition (if requested)

2. **Credits Slide** (`grep -c "<section>" index.html` should match expected slide count)
   - [ ] Charles Darwin (1809-1882) with description
   - [ ] Gregor Mendel (1822-1884) with description
   - [ ] Carl Linnaeus (1707-1778) with description
   - [ ] Stephen Jay Gould (1941-2002) with description
   - [ ] Leigh Van Valen (1935-2010) with description
   - [ ] Tools section (Claude Code, Sonnet 4, Haiku 4.5, OpenACC, C++17)
   - [ ] GitHub links (main repo + presentation folder)
   - [ ] Reproduction command (full evolve_coevolve.py invocation)

3. **Cost De-emphasis** (`grep -c "\$10" index.html`)
   - [ ] Remove "$10 in API credits" from slide 4
   - [ ] Remove "$10 budget" from benefit card
   - [ ] Remove cost comparison table from slide 9
   - [ ] Keep contextual mention in credits slide only

4. **Illustration Suggestions** (`grep -c "img-suggestion" index.html`)
   - [ ] Title slide: Phylogenetic tree
   - [ ] Slide 1: Three-panel (hand-coding | vibe coding | evolution)
   - [ ] Slide 2: Darwin's finches + code phylogeny
   - [ ] Slide 3: Arena diagram with trajectories
   - [ ] Slide 5: Horizontal timeline with fitness jumps
   - [ ] Slide 6: LOC vs Fitness scatter plot
   - [ ] Slide 8: Predator-prey oscillation (Lotka-Volterra)
   - [ ] Slide 12: Finches vs CAD blueprint
   - [ ] Slide 13: Five scientist portraits
   - Expect: ~9 placeholders with detailed suggestions (not just "[Image here]")

5. **File Verification** (after ALL edits complete)
   ```bash
   # Count sections (should be 14 for M25: title + 12 content + credits)
   grep -c "<section>" index.html

   # Verify all scientists present
   grep -i "darwin" index.html
   grep -i "mendel" index.html
   grep -i "linnaeus" index.html
   grep -i "gould" index.html
   grep -i "van valen" index.html

   # Verify definition boxes
   grep -c "definition-term" index.html  # Should be ≥5

   # Verify illustration suggestions
   grep -c "img-suggestion" index.html  # Should be ≥9

   # Verify cost de-emphasis
   grep -c "\$10" index.html  # Should be 1-2 (contextual only)
   ```

### Common Failure Modes (Lessons Learned)

1. **"I said I added it, but I didn't"**
   - Symptom: Describing changes in response but not actually editing the file
   - Prevention: ALWAYS verify with grep/Read after claiming completion
   - Example: Claimed to add credits slide, but `grep -c "Mendel" index.html` returned 0

2. **"I edited the wrong version"**
   - Symptom: Making changes to index_v2.html when index.html is the active file
   - Prevention: Check file path in Edit tool, verify with `ls -la` before editing

3. **"The section doesn't exist"**
   - Symptom: Script tries to replace content but regex doesn't match
   - Prevention: Read the file first, verify exact text to replace exists

4. **"Partial implementation"**
   - Symptom: Adding 3 of 5 scientists, or 2 of 10 illustration suggestions
   - Prevention: Create explicit checklist BEFORE making changes, verify each item

### Workflow to Prevent Mistakes

1. **User requests changes** → Create numbered checklist
2. **Make changes** → Edit files
3. **Verify EACH item** → Use grep/Read to confirm
4. **Report results** → Show verification output, admit failures
5. **Fix failures** → Re-edit until verification passes

### Example Verification Report

Good (shows actual verification):
```
✅ Definition boxes: 5 found (Evolution Biological, Evolution Code, Fitness, LOC, Punctuated Equilibrium)
✅ Credits slide: Slide 13 exists, all 5 scientists present
❌ Illustration suggestions: Only 2 found, expected ~10 → FIXING NOW
✅ Cost de-emphasis: 1 mention remaining (contextual in credits)
```

Bad (claims without evidence):
```
✅ Added all definition boxes
✅ Credits slide complete
✅ Illustration suggestions added
```

---

**Remember**: This is M25's story - the underdog who beat the champion through adaptability. Every slide should reinforce that narrative.
