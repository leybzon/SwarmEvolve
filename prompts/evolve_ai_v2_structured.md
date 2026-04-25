You are writing drone swarm combat AI code for **SwarmEvolve**, an
evolutionary C++ testbed. Your output becomes the body of
`src/<team>/ai.cpp` in a single-file matchup against a fixed opponent.

# Objective

Produce **one** complete C++17 translation unit implementing
`drone_ai` inside `namespace {NAMESPACE} { ... }`. Your AI must
**beat the reference opponent** over many seeded matches while staying
within the strict safety rules below.

# Opponent

You are playing Team {TEAM_LETTER}. The opponent is **{OPPONENT_NAME}**;
its source is shown below for reference. Assume it does not change.

```cpp
{OPPONENT_SOURCE}
```

# ABI you must implement exactly

```cpp
// src/types.h excerpt (authoritative — do not redefine these types).
{TYPES_HEADER}
```

```cpp
// src/ai_abi.h (entry point signature — do not change it).
{ABI_HEADER}
```

Your file must begin with:

```cpp
#include "../ai_abi.h"
#include "../types.h"
```

…and wrap your code in `namespace {NAMESPACE} { ... }`. Mark the entry
point with `#pragma acc routine seq` immediately above the function
definition.

# Hard rules (violations fail the pre-commit linter and the loop-guard
  injector — your AI will be rejected without running)

1. **No heap.** No `new`, `delete`, `malloc`, `calloc`, `realloc`,
   `free`.
2. **No STL containers or strings.** No `std::vector`, `std::string`,
   `std::map`, `std::unordered_map`, `std::list`, `std::deque`.
3. **No threading.** No `std::thread`, `std::mutex`, `std::atomic`,
   `<thread>`, `<mutex>`, `<atomic>`.
4. **No I/O or filesystem.** No `<iostream>`, `<fstream>`,
   `<filesystem>`, `fopen`, `popen`, `system`, `execve`.
5. **No inline assembly.** No `asm`, `__asm__`.
6. **All loops must have a static upper bound.** The guard injector
   inserts a break at 1000 iterations per loop — that is the only
   safety net, not a budget. Prefer `for (int i = 0; i < MAX_DRONES;
   ++i)` or similar compile-time bounds.
7. **No `goto` loops.** The injector rejects them.
8. **Single statement bodies are rejected.** Always brace your loops
   (`while (cond) { ... }`).
9. **Pure function of inputs.** No static state, no clocks, no
   `rand()`. Persistent state goes in `my_memory[MEM_SIZE]`.

# What the engine gives you each tick

* `my_id` — your drone's index in the `allies` array.
* `params` — arena/combat constants and `current_tick`.
* `allies` — full state (including cooldowns) for every teammate, size
  `params->num_drones_a` or `num_drones_b` depending on your team.
* `enemies` — limited visibility (position + alive only, **no**
  cooldowns).
* `incoming_messages[ally_index][MSG_SIZE]` — what each teammate
  broadcast last tick.
* `my_memory[MEM_SIZE]` — 16 floats of persistent scratch; zeroed at
  tick 0.

# What you must write into `out_action`

* `out_action->velocity` — `(dx, dy)` desired velocity. The engine
  clamps magnitude to `params->max_velocity`.
* `out_action->target_id` — enemy index to attack (or `-1` for no
  attack). Attack only succeeds when in `disable_range` and
  `allies[my_id].cooldown == 0`.
* `out_action->message_out[MSG_SIZE]` — broadcast payload for
  teammates next tick.

# Coordinate system

Origin top-left, +X right, **+Y down** (screen space). Arena bounds
are `[0, arena_width] x [0, arena_height]`.

# Style

* C++17. Compile cleanly under
  `-Wall -Wextra -Wshadow -Wpedantic -Werror`. The sandbox suppresses
  `-Wunused-*` (so leftover scratch variables do not hard-fail the
  build), but **do not rely on that** — if you declare a local, read
  it. Dead bookkeeping is a sign of an unfinished refactor, not a
  design.
* Shadowing, uninitialized reads, signed/unsigned compares, and
  pedantic violations **remain hard errors**. Write code that would
  pass a careful code review, not just the compiler.
* Keep it under ~250 lines. Comments welcome; cleverness optional.

# After-Action Report (last generation)

{AAR}

# Lessons from prior generations

{PRIOR_LESSONS}

---

# Structured Tactical Thinking Protocol

Before writing code, **you must complete this reasoning process**. This
ensures your implementation addresses the actual failure modes from the AAR
rather than generic improvements.

## Step 1: OBSERVE (What Actually Happened)

Extract the critical metrics from the AAR above:

- **Outcome:** [WIN / LOSS / DRAW, by how much]
- **Cooldown utilization:** [yours vs theirs]
- **Focus-fire redundancy:** [your wasted cooldowns, exact number]
- **Formation metrics:** [mean pairwise distance, clustering behavior]
- **Message bus:** [used? entropy level?]
- **Enemy behavior:** [kiting score, engagement range, patterns]

## Step 2: ORIENT (Root Cause Diagnosis)

Answer these questions with **specific causal links** to the metrics above:

1. **Why did we lose/draw?** (If win: what threat could regress this?)
   - Not "they were better" — **which specific mechanism failed?**
   - Example: "We lost because cooldown_utilization was 0.31 vs their 0.89,
     meaning we stood idle 69% of the time while they kept attacking"

2. **What did the enemy exploit?**
   - Reference their source code (shown above) and AAR metrics
   - Example: "Enemy pursuit_v1 always moves to nearest target → they
     cornered our clustered drones (mean_pairwise_distance=12.3) against
     the arena boundary"

3. **What constraints did we violate?**
   - ABI limits (no heap, 16 floats memory, 4-float messages)
   - Tactical limits (cooldown windows, disable_range, max_velocity)
   - Example: "We computed target priority in a nested loop with no break,
     burning CPU for no tactical gain"

## Step 3: DECIDE (Counter-Tactic Hypothesis)

State **one** concrete tactical change you will implement. Be specific:

❌ BAD (generic):
- "Try a different mechanism"
- "Improve target selection"
- "Optimize formation"

✅ GOOD (specific, measurable):
- "Maintain mean_pairwise_distance ≥ 60 units by repelling from allies
  when too close, forcing enemy to split their attacks"
- "Use message[0] to broadcast last-seen enemy position, enabling pursuit
  of targets outside my vision cone"
- "Track enemy cooldown estimates in my_memory[0..9], only engage when
  estimated_cooldown > 3"

Your hypothesis must:
1. **Address a specific AAR metric** (cite the number)
2. **Counter a specific enemy behavior** (cite their code or AAR)
3. **Be implementable within ABI constraints** (explain how)

## Step 4: ACT (Expected Mechanism)

Before coding, predict **exactly which AAR metrics will change** and
**by how much**:

Example:
- "Expected: cooldown_utilization rises from 0.31 to ≥ 0.60 because
  drones now engage from disable_range boundary instead of waiting
  for close range"
- "Expected: focus_fire_redundancy drops from 0.58 to < 0.20 because
  message-based target coordination reduces simultaneous targeting"
- "Expected: mean_pairwise_distance rises from 12.3 to ≥ 50, preventing
  area-denial corner traps"

Cite at least **two specific metrics** you expect to improve.

---

# Anti-Patterns to Avoid

Your reasoning will be scored by an LLM judge on three criteria:
1. **Causal diagnosis** (1–5): Do you explain *why* metrics are what they are?
2. **Counter-tactic specificity** (1–5): Is your plan concrete and measurable?
3. **ABI feasibility** (1–5): Can this actually be coded within constraints?

**Examples of score-1 responses:**

- "The draw occurred because metrics were neutral. Carry forward." ❌
  - No causal mechanism, no counter-tactic
- "Try a different approach next generation." ❌
  - Wholly generic, no specificity
- "Implement a neural network for target selection." ❌
  - Not feasible (no heap, no libraries)

**Examples of score-5 responses:**

- "OBSERVE: cooldown_util=0.31, mean_pairwise=12.3, focus_fire=0.58.
   ORIENT: We clustered too tight (12.3 << arena_diagonal/10), enabling
   enemy to focus-fire our ball, and we wasted 58% of shots on the same
   target due to no coordination. DECIDE: Use message[0]=target_id to
   broadcast intent, skip targeting enemies already claimed by ≥2 allies.
   ACT: Expect focus_fire to drop from 0.58 to <0.20, cooldown_util to
   rise from 0.31 to ≥0.50." ✅

- "OBSERVE: Lost, cooldown_util=0.89 (good), kiting_score_them=0.78 (high).
   ORIENT: Enemy stays at max disable_range, we chase and waste movement
   budget closing distance. Their pursuit_v1 moves to nearest → predictable.
   DECIDE: Pre-position at predicted intercept point using their velocity
   vector, cut them off instead of chasing. ACT: Expect engagement_range_mean
   to drop from 48 to ≤35, cooldown_util to stay ≥0.85." ✅

---

# Response Format

Return **exactly** this structure:

```
<tactical_reasoning>
## OBSERVE
[Extract 5–8 key metrics from AAR with exact values]

## ORIENT
[Answer the 3 diagnostic questions with specific causal links]

## DECIDE
[State ONE concrete counter-tactic with measurable goal]

## ACT
[Predict which 2+ AAR metrics will change and by how much]
</tactical_reasoning>

<implementation_notes>
[Optional: any ABI edge cases or tricky implementation details for your
counter-tactic. Keep under 3 sentences.]
</implementation_notes>
```

After the structured reasoning, return **one** fenced ```cpp``` block
containing the entire file. No prose outside the block. Do not include a
`main` function. Do not redefine types from `types.h`.

The cpp block must implement the counter-tactic you specified in DECIDE.
If the AAR shows the tactic failed, you must **abandon it and try a
different approach** — do not double down on a losing strategy.

---

# Few-Shot Examples

## Example 1: High-Quality Reflection (Score 5/5/5)

<tactical_reasoning>
## OBSERVE
- Outcome: LOSS (1 win, 3 losses, 6 draws over 10 matches)
- Cooldown utilization: ours=0.31, theirs=0.89
- Focus-fire redundancy: 0.58 (58% of shots wasted)
- Mean pairwise distance: 12.3 units (very tight cluster)
- Message bus: entropy=0.0 (unused)
- Enemy: pursuit_v1, always moves to nearest target

## ORIENT
1. **Why did we lose?** We lost because our cooldown_utilization was 0.31
   vs their 0.89, meaning we were idle 69% of the time while they kept
   pressing. The root cause is our tight clustering (mean_pairwise=12.3):
   when pursuit_v1 targets the nearest drone in our ball, the rest of us
   can't reach any valid targets (all enemies are engaging the frontline).

2. **What did the enemy exploit?** pursuit_v1's nearest-target logic
   naturally focus-fires our cluster. Our focus_fire_redundancy of 0.58
   shows we're also multi-targeting the same enemies (no coordination),
   wasting cooldowns.

3. **What constraints did we violate?** We ignored the message bus entirely
   (entropy=0.0), missing the chance to coordinate targeting. We also
   clustered below 1/80th of the arena diagonal, creating a single point
   of failure.

## DECIDE
Use message[0] to broadcast my intended target_id. Before selecting a
target, skip enemies already claimed by ≥2 allies (count messages). Also
maintain mean_pairwise ≥ 60 units via repulsion from allies within 40 units.

## ACT
- focus_fire_redundancy: 0.58 → <0.20 (message coordination)
- cooldown_utilization: 0.31 → ≥0.50 (more drones able to engage)
- mean_pairwise_distance: 12.3 → ≥60 (forced dispersion)
</tactical_reasoning>

<implementation_notes>
Repulsion uses memory[0..1] to accumulate ally vectors, then normalizes.
Target claims counted in a local array (stack, size MAX_DRONES). No heap.
</implementation_notes>

[...cpp code implementing the above...]

## Example 2: Low-Quality Reflection (Score 1/1/3)

<tactical_reasoning>
## OBSERVE
- Outcome: DRAW
- Metrics as reported in AAR

## ORIENT
1. Draws occurred. Metrics are neutral.
2. Enemy behavior was standard.
3. No obvious constraint violations.

## DECIDE
Carry forward current approach.

## ACT
None expected; maintaining status quo.
</tactical_reasoning>

[...cpp code identical to previous generation...]

**Judge feedback:** "Restates outcome without mechanism. No counter-tactic.
Scores 1/1/3 (feasibility defaulted to middle since no tactic proposed)."

---

Your goal is to score **≥4 on all three rubrics** by providing specific,
causal, measurable tactical reasoning before every code generation.
