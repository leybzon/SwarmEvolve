# CLAUDE.md - Presentation Layer Guidance

This file provides guidance to Claude Code when working on **presentations** in this directory, as opposed to software implementation in the main repository.

## Core Principle: Concepts Over Code

When working in `presentations/`, the focus shifts from **software engineering** to **storytelling, philosophy, and audience engagement**. You are no longer building a system—you are crafting a narrative about evolutionary dynamics, emergent intelligence, and the democratization of AI research.

---

## Mindset Shift

### In `/src`, `/scripts`, `/docs`:
- **Goal**: Build robust, deterministic software
- **Language**: Technical precision, implementation details
- **Audience**: Engineers, researchers, code reviewers
- **Constraints**: Memory limits, GPU compatibility, type safety

### In `/presentations`:
- **Goal**: Inspire, educate, and persuade audiences
- **Language**: Metaphor, analogy, visual storytelling
- **Audience**: General tech community, researchers, executives, students
- **Constraints**: Attention span, cognitive load, emotional engagement

---

## Key Documentation Files

When working on presentations, **always consult these files first**:

1. **`CONCEPTS.md`** - Evolutionary biology metaphors, theoretical frameworks
2. **`NARRATIVES.md`** - Story arcs, dramatic structure, presentation patterns
3. **`VISUALS.md`** - Visual language, color psychology, design patterns
4. **`AUDIENCE_PROFILES.md`** - Adaptation strategies for different audiences

---

## Presentation Design Principles

### 1. Show, Don't Tell
- **Bad**: "Team B's fitness improved from -0.8 to +0.9"
- **Good**: Show the Red Queen graph with dramatic color shift, then reveal the breakthrough moment

### 2. Use Biological Metaphors
- **Code evolution** → DNA mutation & natural selection
- **Fitness function** → Survival pressure in an ecosystem
- **Local optimum** → Evolutionary dead-end (like the panda's thumb)
- **Co-evolution** → Predator-prey arms race
- **Formation Spread** → Territorial behavior in wolf packs

### 3. Build Dramatic Tension
Every presentation needs:
- **Setup**: The challenge (How do we program swarms?)
- **Conflict**: The problem (Hand-coding is slow, AI hallucinates)
- **Crisis**: The test (Can Team B beat 100 generations of evolution?)
- **Climax**: The breakthrough (Round 31 Formation Spread)
- **Resolution**: The insight (Co-evolution accelerates learning by 35%)

### 4. Minimize Text, Maximize Visuals
- **Target**: <20 words per slide (excluding code snippets)
- **Use**: Large numbers, bold colors, diagrams, videos
- **Avoid**: Bullet-point lists, dense paragraphs, walls of text

### 5. Make Every Number a Story
- **Bad**: "3.2× code growth"
- **Good**: "To win, Team B had to write 3× more code than it started with. Every major innovation cost 30-40 lines. Intelligence isn't free—it demands complexity."

---

## Visual Language Guide

### Color Semantics
- **Team A (Blue `#4285f4`)**: Established, powerful, static perfection
- **Team B (Red `#ea4335`)**: Challenger, adaptive, scrappy underdog
- **Breakthrough (Green `#34a853`)**: Innovation, discovery, success
- **Fragility (Gray `#888`)**: Failure, stagnation, evolutionary dead-ends

### Animation Principles
- **Fragments**: Use for step-by-step reveals (e.g., 6 tactical phases)
- **Fade-ins**: For diagrams that support the narrative
- **No auto-advance**: Let the speaker control pacing

### Typography Hierarchy
1. **Slide Title** (h2): The "what" (e.g., "The Red Queen Effect")
2. **Slide Subtitle** (h3): The "why" (e.g., "Reversing an Insurmountable Gap")
3. **Body Text**: The "how" (diagrams, code, metrics)

---

## Storytelling Frameworks

### The Hero's Journey (Team B as Underdog)
1. **Ordinary World**: pursuit_v1 baseline (66 LOC, -0.8 fitness)
2. **Call to Adventure**: Challenge the M22 champion
3. **Refusal**: Early failures (Rounds 2-10 plateau)
4. **Mentor**: LLM planner (Claude Sonnet 4)
5. **Trials**: 6 tactical phases, 47 Team A failures
6. **Breakthrough**: Round 31 Formation Spread
7. **Return**: +0.9 fitness, defeating the champion

### The Scientific Discovery Arc
1. **Hypothesis**: Can co-evolution enable underdogs to win?
2. **Experiment**: 100 rounds, alternating teams
3. **Data**: 95 rounds, 8 acceptances, fitness reversal
4. **Surprise**: Power law innovation (1 big jump, many small steps)
5. **Insight**: Strong opponents accelerate learning
6. **Implications**: Democratizes AI research ($10 vs $1M)

---

## Code in Presentations

### When to Show Code
- ✅ **Before/after comparisons** (Round 1 vs Round 31)
- ✅ **Breakthrough moments** (Formation Spread repulsion forces)
- ✅ **Simplicity → complexity evolution** (66 LOC → 210 LOC)

### When to Hide Code
- ❌ Implementation details (loop guards, AST injection)
- ❌ Boilerplate (includes, namespaces, struct definitions)
- ❌ Full functions (show 5-10 line excerpts only)

### Code Snippet Best Practices
```cpp
// GOOD: Highlights the innovation
const float min_spacing = 80.0f;  // Zone coverage!
repulse_x += push_x * strength;

// BAD: Too much context
#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, ...) {
    // ... 200 lines of boilerplate ...
}
```

---

## Audience Adaptation Checklist

Before creating a presentation, ask:

1. **Who is the audience?** (Engineers, researchers, execs, general public)
2. **What is their background?** (Deep RL experts, AI generalists, non-technical)
3. **What do they care about?** (Performance, cost, interpretability, novelty)
4. **How much time do you have?** (5 min pitch, 15 min talk, 30 min deep-dive)
5. **What is the desired outcome?** (Convince, educate, inspire, recruit)

Consult `AUDIENCE_PROFILES.md` for adaptation strategies.

---

## Anti-Patterns to Avoid

### ❌ The Wall of Text
**Problem**: Slides with >50 words of body text
**Fix**: Replace with diagrams, large numbers, or code snippets

### ❌ The Spec Sheet
**Problem**: Listing features without narrative ("It has X, Y, Z")
**Fix**: Tell the story of discovery ("We tried X, it failed. Then we discovered Y...")

### ❌ The Implementation Dump
**Problem**: Showing software architecture diagrams
**Fix**: Show **outcomes** (fitness graphs, tactical evolution), not **inputs** (class hierarchies)

### ❌ The Jargon Trap
**Problem**: Using unexplained technical terms ("AST injection", "OODA loop", "OpenACC pragmas")
**Fix**: Either explain or replace with metaphors ("loop safety guardrails", "tactical decision cycle")

---

## Presentation File Structure

```
presentations/
├── CLAUDE.md              # This file (meta-guidance)
├── CONCEPTS.md            # Evolutionary theory & philosophy
├── NARRATIVES.md          # Story arcs & dramatic structures
├── VISUALS.md             # Design language & metaphors
├── AUDIENCE_PROFILES.md   # Adaptation strategies
└── {experiment_name}/     # e.g., m25_coevolution/
    ├── CLAUDE.md          # Experiment-specific guidance (references parent docs)
    ├── index.html         # The presentation itself
    ├── README.md          # Usage instructions
    └── assets/            # Figures, videos, images
```

---

## Creating a New Presentation

### Step 1: Research the Experiment
- Read `docs/{EXPERIMENT}_REPORT.md`
- Analyze `data/runs/{experiment}/journal.jsonl`
- Review generated figures and videos

### Step 2: Extract the Story
- Identify the **protagonist** (Which team? Which tactic?)
- Find the **conflict** (What challenge did they face?)
- Locate the **breakthrough** (When did the reversal happen?)
- Determine the **insight** (What did we learn?)

### Step 3: Choose Metaphors (consult `CONCEPTS.md`)
- Which evolutionary concept fits best? (Red Queen, punctuated equilibrium, Baldwin effect)
- What natural analogy works? (Predator-prey, immune system, ecosystem)

### Step 4: Design Visual Hierarchy (consult `VISUALS.md`)
- Primary visual: The "hero graph" (usually fitness over time)
- Supporting visuals: Code comparisons, tactical diagrams
- Accent visuals: Videos, animations

### Step 5: Build the Narrative (consult `NARRATIVES.md`)
- Choose a story arc (Hero's Journey, Scientific Discovery, David vs Goliath)
- Map slides to narrative beats
- Add dramatic reveals (fragments, fade-ins)

### Step 6: Adapt for Audience (consult `AUDIENCE_PROFILES.md`)
- Technical depth: Code snippets vs high-level concepts
- Time allocation: 5/10/15/30 minute variants
- Emphasis: Performance, cost, interpretability, novelty

---

## Example: M25 Co-Evolution Presentation

### Story Arc: David vs Goliath + Red Queen
- **Setup**: Three ways to program swarms (traditional, vibe coding, evolution)
- **Challenge**: Can Team B beat M22's 100-generation champion?
- **Baseline**: -0.8 fitness, losing 8/10 matches
- **Journey**: 6 tactical phases (pursuit → zone control)
- **Breakthrough**: Round 31 Formation Spread (+0.9 fitness)
- **Insight**: Co-evolution accelerates learning by 35%
- **Impact**: $10 budget matches $1M AlphaStar dynamics

### Key Metaphors Used:
- **Red Queen Effect**: "Running to stand still" (Lewis Carroll)
- **Punctuated Equilibrium**: Staircase, not smooth curve
- **Arms Race**: Predator-prey adaptation cycles
- **Fragile Optimum**: Panda's thumb (over-specialized, can't adapt)

### Visual Language:
- Blue (Team A) = Established power
- Red (Team B) = Scrappy challenger
- Green = Breakthrough moments
- Gradient borders on tactic cards = Evolutionary flow

---

## Final Checklist Before Presenting

- [ ] **Narrative clarity**: Can a non-expert follow the story?
- [ ] **Visual balance**: <30% text, >70% visuals?
- [ ] **Emotional arc**: Setup → tension → climax → resolution?
- [ ] **Surprise moment**: Is there a "wow" reveal (e.g., Round 31 breakthrough)?
- [ ] **Actionable takeaway**: What should the audience remember?
- [ ] **Time budget**: Does it fit the allocated slot?
- [ ] **Speaker notes**: Are notes clear for live delivery?

---

## When in Doubt

1. **Read** `CONCEPTS.md` for metaphors
2. **Read** `NARRATIVES.md` for story structures
3. **Read** `VISUALS.md` for design patterns
4. **Read** `AUDIENCE_PROFILES.md` for adaptation strategies
5. **Ask**: "Would my grandparent understand this slide?" (If no, simplify)

---

**Remember**: You're not writing documentation. You're crafting an experience. Make it memorable, visual, and emotionally resonant.
