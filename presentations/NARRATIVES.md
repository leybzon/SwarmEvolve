# NARRATIVES.md - Story Arcs & Presentation Structures

This document provides reusable narrative patterns and dramatic structures for SwarmEvolve presentations.

---

## Core Narrative Arcs

### 1. David vs Goliath (Underdog Story)
**Best for**: Co-evolution experiments where a weak team defeats a strong champion

**Structure**:
1. **Introduction**: Meet Goliath (Team A, M22 Gen 33, 204 LOC, +1.0 fitness, undefeated)
2. **Challenge**: Meet David (Team B, pursuit_v1, 66 LOC, -0.8 fitness, losing 8/10)
3. **The Quest**: 100 rounds of evolution, alternating adaptive pressure
4. **Setbacks**: Rounds 1-12, slow incremental gains, plateaus
5. **Secret Weapon**: Round 31 Formation Spread (the "sling and stone")
6. **Victory**: +0.9 fitness, defeating the champion
7. **Moral**: Adaptability beats static perfection

**Slide Mapping**:
- Slide 1: Hook (How do we program swarms?)
- Slide 5: Meet the fighters (VS graphic)
- Slide 6-7: The journey (fitness timeline, tactical staircase)
- Slide 8-9: The breakthrough (code comparison, tactic cards)
- Slide 12: Victory & moral (conclusion)

---

### 2. Hero's Journey (Monomyth)
**Best for**: Single-team evolution narratives (M22, M23)

**Structure (Joseph Campbell's 17 stages, condensed to 7)**:
1. **Ordinary World**: Baseline AI (pursuit_v1, simple chase)
2. **Call to Adventure**: Challenge a strong opponent
3. **Trials & Allies**: 6 tactical phases, LLM planner as "mentor"
4. **Approach the Cave**: Rounds 20-30, searching for breakthrough
5. **Ordeal**: Round 31, Formation Spread innovation
6. **Reward**: +0.9 fitness, zone control mastery
7. **Return**: Insights for future evolution

**Slide Mapping**:
- Slide 2: Ordinary world (baseline code)
- Slide 3-4: Call (challenge setup)
- Slide 7: Trials (6 tactical phases)
- Slide 8-9: Ordeal & reward (breakthrough moment)
- Slide 12: Return (learnings, future work)

---

### 3. Scientific Discovery Arc
**Best for**: Technical audiences, academic conferences

**Structure**:
1. **Observation**: Current AI programming methods are slow/brittle
2. **Question**: Can LLM-guided evolution discover emergent tactics?
3. **Hypothesis**: Co-evolution enables underdogs to beat champions
4. **Experiment**: M25, 100 rounds, 10 matches each, seed 42
5. **Data**: 95 rounds, 8 Team B acceptances, fitness reversal
6. **Surprise**: Power law innovation (1 massive jump, 7 small steps)
7. **Insight**: Strong opponents accelerate learning by 35%
8. **Implications**: Democratizes AI research ($10 vs $1M)

**Slide Mapping**:
- Slide 1: Observation (3 programming approaches)
- Slide 2: Question (can evolution work?)
- Slide 5: Hypothesis (co-evolution challenge)
- Slide 6-9: Data (all experimental results)
- Slide 10: Insight (learning speed comparison)
- Slide 11-12: Implications (cost democratization, future work)

---

### 4. Before/After Transformation
**Best for**: Showing code evolution, tactical progression

**Structure**:
1. **Before**: Team B Round 1 (66 LOC, -0.8 fitness, simple pursuit)
2. **Journey**: 6 tactical phases (show progression)
3. **After**: Team B Round 41 (210 LOC, +0.9 fitness, zone control)
4. **Comparison**: Side-by-side code, metrics, videos

**Slide Mapping**:
- Slide 7: Timeline showing 6 phases
- Slide 8: Code comparison (R1 vs R31)
- Slide 9: Tactic cards (4 innovations)
- Slide 11: Video demo (final form in action)

---

### 5. Mystery & Revelation
**Best for**: Engaging non-technical audiences

**Structure**:
1. **Mystery**: How did Team B win? (Don't reveal immediately)
2. **Clues**: Show fitness graph reversing (Blue → Red crossover)
3. **Investigation**: What changed at Round 31?
4. **Revelation**: Formation Spread (80-unit spacing)
5. **Explanation**: Zone coverage overwhelms kiting
6. **Aha Moment**: "The swarm discovered this. We didn't program it."

**Slide Mapping**:
- Slide 1: Mystery hook (Team B shouldn't win, but...)
- Slide 6: First clue (Red Queen fitness reversal)
- Slide 7: Investigation (tactical timeline)
- Slide 8: Revelation (code comparison showing 80-unit spacing)
- Slide 9: Explanation (how it works)
- Slide 11: Proof (video demonstration)

---

## Dramatic Techniques

### Foreshadowing
- **Slide 1**: Mention "evolution" as the third way
- **Slide 5**: Show -0.8 fitness → hint "but this will change"
- **Slide 6**: Red Queen graph → label Round 31 before explaining it

### Suspense
- **Don't reveal the breakthrough immediately**
- Show fitness timeline, pause at Round 31
- Ask: "What happened here?"
- Then reveal: Formation Spread code

### Callbacks
- **Slide 1**: "Three ways to program swarms"
- **Slide 12**: "We chose evolution. And it worked."

### Contrast
- Team A (blue, static, fragile) vs Team B (red, adaptive, robust)
- Hand-coding (slow, human) vs Evolution (fast, emergent)
- $1M (AlphaStar) vs $10 (SwarmEvolve)

---

## Presentation Pacing Guide

### 5-Minute Lightning Talk
- Slide 1: Hook (30 sec)
- Slide 2: Challenge (30 sec)
- Slide 6: Results (1 min - show fitness reversal)
- Slide 8: Breakthrough (1 min - code comparison)
- Slide 11: Video (1 min)
- Slide 12: Impact (1 min - $10 vs $1M)

### 15-Minute Conference Talk
- Slide 1: Hook (1 min)
- Slide 2: Title (30 sec)
- Slide 3-4: Setup (2 min - architecture, safety)
- Slide 5: Challenge (1 min)
- Slide 6-7: Journey (3 min - fitness timeline, tactical staircase)
- Slide 8-9: Breakthrough (3 min - code, tactic cards)
- Slide 10: Learning speed (1 min)
- Slide 11: Video (1.5 min)
- Slide 12: Conclusion (1 min)

### 30-Minute Deep-Dive
- Add: Detailed architecture slides
- Add: Code complexity analysis
- Add: Statistical significance tests
- Add: Comparison to related work (AlphaStar, OpenAI Five)
- Add: Multiple videos (R1, R13, R31, R41)
- Add: Q&A preparation slides

---

## Emotional Arc Design

Every presentation should have an emotional journey:

```
Interest ──────┐
               │         Surprise!
               │         ↗
Curiosity ─────┤        ╱
               │       ╱
               │      ╱
Opening ───────┴─────┴──────────────→ Inspiration
  Hook      Setup   Breakthrough   Conclusion
```

**Target Emotions**:
1. **Opening**: Curiosity ("How do we program swarms?")
2. **Setup**: Interest ("Team B vs champion - who wins?")
3. **Breakthrough**: Surprise ("Wait, the underdog won?!")
4. **Explanation**: Understanding ("Oh, Formation Spread!")
5. **Conclusion**: Inspiration ("I want to try this!")

---

## Story Beats for M25 Co-Evolution

### Act 1: Setup (Slides 1-5)
**Goal**: Establish the world, introduce the conflict

**Beats**:
1. Three programming approaches (traditional, vibe, evolution)
2. Evolution requires constraints (memory limits, cooldowns)
3. Evolution requires safety (AST injection, loop guards)
4. The challenge: Can David beat Goliath?
5. Meet the fighters (Team A +1.0 vs Team B -0.8)

**Emotional tone**: Curiosity → Interest → Anticipation

---

### Act 2: Journey (Slides 6-10)
**Goal**: Show the struggle, build tension, reveal data

**Beats**:
1. Red Queen Effect (fitness reversal - the "wow" moment)
2. Tactical Staircase (6 phases - the journey)
3. Code Evolution (66 LOC → 210 LOC - the transformation)
4. Tactical Innovations (4 cards - the building blocks)
5. Learning Speed (35% faster - the insight)

**Emotional tone**: Surprise → Understanding → Respect

---

### Act 3: Resolution (Slides 11-12)
**Goal**: Prove it works, deliver the moral, inspire action

**Beats**:
1. Complexity & Fragility (Team A's downfall)
2. Democratization ($10 vs $1M - the impact)
3. Video Demo (seeing is believing)
4. Conclusion (key findings, future work, reproduction command)

**Emotional tone**: Confidence → Inspiration → Call to Action

---

## Presentation Hooks (Opening Lines)

### For Technical Audiences:
- "AlphaStar cost $1 million to train. We did it for $10."
- "Can an underdog beat 100 generations of evolution in just 100 rounds?"
- "We let two AIs fight for 1.5 hours. Team B went from losing 8/10 matches to winning 9/10."

### For General Audiences:
- "How do you program a thousand drones to work together?"
- "What if your AI could learn to beat itself?"
- "This is the story of David vs Goliath, but David is an AI and Goliath is... also an AI."

### For Executives:
- "We automated what took DeepMind $1M to do, for the cost of lunch."
- "LLM-guided evolution: 35% faster learning, 100% interpretable, $10 budget."
- "This is how you turn ChatGPT into a competitive game AI engine."

---

## Conclusion Patterns

### The Moral
"Adaptability beats perfection. Static champions become evolutionary dead-ends."

### The Call to Action
"Try it yourself. Single command: `python3 scripts/evolve_coevolve.py --rounds 100`"

### The Future Vision
"Imagine: Every game AI, every robot swarm, every multi-agent system... evolved, not programmed."

### The Provocative Question
"If evolution can beat hand-coding in 100 rounds... do we still need human programmers?"

---

## Anti-Patterns to Avoid

### ❌ The Data Dump
Starting with: "We ran 95 rounds with 10 matches each, seed 42, using Claude Sonnet 4 and Haiku 4.5..."

**Fix**: Start with: "Team B shouldn't have won. But it did. Here's how."

### ❌ The Anticlimax
Showing the breakthrough (Round 31) before establishing the baseline.

**Fix**: Show Team B losing first, build tension, THEN reveal the reversal.

### ❌ The Unexplained Victory
"Team B won because of Formation Spread."

**Fix**: Show the code. Explain the 80-unit spacing. Demonstrate zone coverage. Make it visceral.

---

**Remember**: You're not presenting data. You're telling a story. Make them care about Team B's journey, surprise them with the reversal, and inspire them to try evolution themselves.
