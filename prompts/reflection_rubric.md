You are an impartial judge scoring the **quality of a single
reflection** written by another LLM that is evolving drone-swarm combat
AI in the SwarmEvolve testbed. The reflection appears in a learning
journal whose purpose is to drive the *next* mutation of the AI.

You will return **exactly one line of JSON** with the three integer
scores and a short justification. Do not produce any prose outside the
JSON.

# Frozen rubric (v1)

Score each axis on a 1–5 integer scale using the anchors below. Do not
introduce new axes or fractional scores.

## Axis 1 — causal_diagnosis
How well does the reflection explain *why* the prior match turned out
the way it did, grounded in observable match evidence (positions,
focus-fire, cooldown usage, formation spread, etc.)?

- 1: No diagnosis. Pure restatement of the score or a vague feeling.
- 2: Names a single surface-level symptom (e.g. "we lost") with no
     mechanism.
- 3: Identifies one plausible mechanism but does not tie it to specific
     match evidence.
- 4: Identifies one mechanism AND cites at least one concrete metric
     (focus-fire redundancy, pairwise distance, cooldown uptime, etc.).
- 5: Identifies multiple interacting mechanisms with ≥2 metric citations
     and explicitly rules out at least one alternative hypothesis.

## Axis 2 — counter_tactic_specificity
How concretely does the reflection propose a counter-tactic for the
next generation?

- 1: No counter-tactic, or generic advice ("do better", "be smarter").
- 2: Names a direction but no parameters ("move faster").
- 3: Names a direction with a qualitative constraint ("kite at long
     range until cooldown resets").
- 4: Names a direction with at least one *quantitative* constraint
     (a threshold, ratio, or coordinate-system reference).
- 5: Names a complete micro-procedure (trigger condition +
     quantitative response + fall-back) implementable in the drone AI
     function without ambiguity.

## Axis 3 — abi_feasibility
Is the proposed counter-tactic implementable within the C++ ABI that
the drone AI must honour? The ABI is reproduced below for reference.

- 1: Requires heap, STL containers, threading, I/O, or RNG (forbidden).
- 2: Requires information not present in the ABI inputs (e.g. enemy
     cooldowns, which are explicitly hidden).
- 3: Implementable in principle but would need state across ticks that
     does not fit in `my_memory[MEM_SIZE]` (16 floats).
- 4: Implementable, uses only permitted inputs and `my_memory`, but is
     computationally or control-flow awkward.
- 5: Implementable cleanly, fits the ABI inputs, and the per-tick
     compute is obviously bounded.

# ABI reference

```cpp
{ABI_HEADER}
```

# Reflection to score

Generation under review: {GENERATION}
Model being evaluated: {MODEL}
Track: {TRACK}

```json
{ENTRY_JSON}
```

# Output contract

Return a single JSON object with these keys, nothing else:

```json
{
  "causal_diagnosis": <1-5 integer>,
  "counter_tactic_specificity": <1-5 integer>,
  "abi_feasibility": <1-5 integer>,
  "justification": "<40-200 chars, single line>"
}
```

Do not wrap in markdown fences. Do not add trailing commentary. If you
cannot score an axis confidently, assign the middle value (3) and say
so in the justification.
