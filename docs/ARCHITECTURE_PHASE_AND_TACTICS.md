# Architecture Extensions: Phase-Awareness, State-Dependent Tactics, and Emergent Tactic Libraries

**Status:** Design proposal for review — not yet implemented
**Companion to:** `docs/ARCHITECTURE_SHIFT_DUAL_LLM.md` (Extension A is folded into that doc's Phase 1; Extensions B and C are described here in full)
**Predecessors:** `docs/RETROSPECTIVE_SELF_IMPROVEMENT.md`
**Depends on:** Dual-LLM split (required prerequisite for B and C)

---

## 1. The gap this document addresses

The dual-LLM doc closes the *cross-generation* feedback loop (Strategist reflects → Coder implements → fitness observed → Strategist re-plans). It does not close the *within-match* feedback loop. Under the dual-LLM design alone, the Strategist produces **one directive per generation** and the Coder compiles it into **static C++ that runs the same logic from tick 0 to tick 200**.

This means even a perfectly-executing dual-LLM loop will produce monolithic AIs that cannot say:

> *"We're outnumbered 4-vs-9; stop engaging and kite to a corner to let cooldowns reset."*

unless the Coder happened to hardcode that specific branch. The stated project goal — **advanced strategies emerging eventually from the self-improvement loop** — requires tactics to be **state-dependent within a match** and to **accumulate across matches into a reusable vocabulary**.

This doc proposes three extensions, stacked:

- **Extension A — Phase-segmented AAR.** The Strategist must *see* phase-specific failure to diagnose it. Cheap, mechanical, folded into dual-LLM Phase 1.
- **Extension B — Phase-aware directive schema + state-machine ABI helpers.** The directive forces the Coder to produce a finite-state-machine AI, not a fixed loop. Medium cost, high leverage.
- **Extension C — Tactic library with combinator grammar and M18-as-promotion-gate.** Successful tactic primitives get named, stored, and composed across generations. This is the *emergence* mechanism. Higher cost, highest leverage; converts M18 from shelf-ware into load-bearing infrastructure.

## 2. Three levels of tactical adaptation (do not conflate them)

| Level | What it is | Current state | Where addressed |
|---|---|---|---|
| **L1 — Intra-match conditional behaviour** | AI has `if outnumbered { defensive } else { aggressive }` branches in its C++ | Absent. Gen-91 champion on Track A is a fixed-policy pursuer. | Extension B |
| **L2 — Cross-match strategic adaptation** | Strategist notices "we lose in endgame" and produces endgame-specific directive | Partially addressed by dual-LLM alone, but blind without phase-segmented AAR | Dual-LLM + Extension A |
| **L3 — Emergent novel tactics across lineage** | System invents tactics (e.g. bait-sacrifice) that no prompt contained | Impossible under current or dual-LLM design; needs accumulation | Extension C |

Each level depends on the one above it. Building L3 without L1 produces a library of named primitives none of which the Coder can actually implement.

## 3. Extension A — Phase-segmented AAR

### 3.1 Problem

Current `aar.json` reports whole-match aggregates: `cooldown_utilization_us=0.79`, `focus_fire_redundancy=0.00`. These are indistinguishable from *"perfectly utilised cooldowns throughout"* and *"under-utilised in opening, over-utilised in endgame, averaging to 0.79."* The Strategist cannot localise failure without per-phase breakdowns.

### 3.2 Design

Extend `scripts/telemetry_aar.py::render_aar()` to compute every metric over four windows plus two overlays:

**Temporal phases** (exhaustive, non-overlapping):
- `opening`: ticks `[0, 0.2 × T)` where `T` = observed match length.
- `midgame`: ticks `[0.2 × T, 0.6 × T)`.
- `endgame`: ticks `[0.6 × T, T]`.

**Balance overlays** (non-exhaustive, possibly overlapping with temporal phases):
- `ahead`: contiguous runs where `our_alive > their_alive`.
- `even`: contiguous runs where `our_alive == their_alive`.
- `outnumbered`: contiguous runs where `our_alive < their_alive`.

For each window, recompute the existing metric set (cooldown utilisation, focus-fire redundancy, mean pairwise distance, hit rate, engagement range, opponent kiting score, dispersion index, message entropy). Report empty windows (no ticks satisfy the overlay) as `null`, never as 0 — the Strategist must distinguish "we were never outnumbered" from "we were outnumbered and did nothing."

### 3.3 Schema (aar.json v2)

```json
{
  "schema_version": 2,
  "perspective": "A",
  "outcome": "TEAM_A_WIN",
  "ticks": 38,
  "global": { /* existing AAR fields unchanged */ },
  "by_phase": {
    "opening":  { "ticks": [0, 7], "cooldown_util": 0.42, ... },
    "midgame":  { "ticks": [7, 22], "cooldown_util": 0.81, ... },
    "endgame":  { "ticks": [22, 38], "cooldown_util": 1.00, ... }
  },
  "by_balance": {
    "ahead":       { "tick_count": 19, "cooldown_util": 0.92, ... },
    "even":        { "tick_count": 12, "cooldown_util": 0.67, ... },
    "outnumbered": { "tick_count": 7,  "cooldown_util": 0.14, ... }
  },
  "transitions": [
    { "from_phase": "opening", "to_phase": "midgame", "at_tick": 7, "our_alive": 10, "their_alive": 8 },
    { "from_phase": "midgame", "to_phase": "endgame", "at_tick": 22, "our_alive": 10, "their_alive": 3 }
  ]
}
```

`aar.md` renders a phase-segmented table; the existing "global" block stays at the top for backwards-compatible scraping.

### 3.4 Risks (Extension A)

- **A-R1: Short matches degenerate windows.** A 5-tick match has a 1-tick opening and 2-tick endgame; per-phase averages are noisy. **Mitigation:** if any phase has < 3 ticks, collapse to `global` only and emit `by_phase: null` with `reason: "match_too_short"`. The Strategist is instructed to treat null-phase matches as "diagnose from global only."
- **A-R2: Overlay windows fragment.** Ahead/even/outnumbered can alternate 10 times in one match, producing tiny fragments. **Mitigation:** apply a minimum-run-length of 3 ticks; fragments below threshold are merged into the surrounding run.
- **A-R3: Schema version explosion.** Bumping AAR from v1 → v2 propagates to journal entries that cite AAR metrics. **Mitigation:** `aar_metrics_cited` in the journal entry stays a flat dict; the *citation path* (e.g. `"by_phase.endgame.cooldown_util"`) is a dotted-string key. Validator traverses dots; v1 keys remain valid.

### 3.5 Integration with dual-LLM doc

Extension A is **required Phase-1 work** in the dual-LLM rollout. Without it, the Strategist reads aggregates that hide phase-specific failure, and the directive vocabulary cannot reference phases it cannot see. The dual-LLM doc is amended (§1.Phase-1) to list Extension A as a dependency, not an option.

## 4. Extension B — Phase-aware directive schema and state-machine ABI

### 4.1 Problem

Even with phase-segmented AAR, the Coder's output is still a single C++ function with no phase concept. A directive saying *"in endgame, contract ring while focus-firing"* becomes a hard translation problem: the LLM must design the phase detector, the hysteresis, the transition predicates, *and* the per-phase behaviour, all under safety constraints. Experience from other LLM-code-gen projects suggests state-machine synthesis from prose is unreliable.

### 4.2 Design — two parts

**(B.1) Shipped state-machine skeleton in the ABI.**

A hand-written header `src/phase.h` defines:

```cpp
#pragma once
#include "types.h"

enum Phase : int {
  PHASE_OPENING = 0,
  PHASE_MIDGAME = 1,
  PHASE_ENDGAME = 2,
  PHASE_EMERGENCY = 3,
  PHASE_COUNT = 4,
};

// Slot in my_memory[MEM_SIZE] reserved for the phase state machine.
// AI code is expected to store current phase in memory[MEM_PHASE] and
// the tick count since last transition in memory[MEM_PHASE_DWELL].
constexpr int MEM_PHASE        = 14;
constexpr int MEM_PHASE_DWELL  = 15;
constexpr int HYSTERESIS_TICKS = 5;

#pragma acc routine seq
inline int current_phase(const float* memory) {
  int p = (int)memory[MEM_PHASE];
  return (p >= 0 && p < PHASE_COUNT) ? p : PHASE_OPENING;
}

#pragma acc routine seq
inline void update_phase(float* memory, int new_phase) {
  int cur = current_phase(memory);
  if (new_phase != cur && memory[MEM_PHASE_DWELL] >= HYSTERESIS_TICKS) {
    memory[MEM_PHASE] = (float)new_phase;
    memory[MEM_PHASE_DWELL] = 0.0f;
  } else if (new_phase == cur) {
    memory[MEM_PHASE_DWELL] += 1.0f;
  }
}

// Helper: compute the "recommended" phase from observable state. The
// Coder may use this verbatim, or override with directive-specific
// thresholds.
#pragma acc routine seq
inline int recommended_phase(
  int my_alive, int their_alive, int tick, int estimated_match_length
) {
  if (my_alive < their_alive && (their_alive - my_alive) >= 2) {
    return PHASE_EMERGENCY;
  }
  float t = (estimated_match_length > 0)
            ? (float)tick / (float)estimated_match_length : 0.0f;
  if (t < 0.2f) return PHASE_OPENING;
  if (t < 0.6f) return PHASE_MIDGAME;
  return PHASE_ENDGAME;
}
```

Two `my_memory[]` slots are reserved (14 and 15). The existing `MEM_SIZE=16` is not changed. AI code retains `my_memory[0..13]` (14 floats) for its own scratch — ample for tactical state.

**(B.2) Phase-aware `CoderDirective` schema (replaces the flat version in dual-LLM doc §5).**

```python
# scripts/strategic_directive.py (revised from dual-LLM doc)
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class PhasePrescription:
    phase: Literal["opening", "midgame", "endgame", "emergency"]
    trigger_override: str | None         # pseudocode; null = use recommended_phase()
    behavior: list[str]                  # ≥1 must-implement bullets
    expected_metrics: dict[str, str]     # e.g. {"cooldown_util": ">0.8"}

@dataclass
class Transition:
    from_phase: str
    to_phase: str
    condition: str                       # pseudocode boolean over observable state
    hysteresis_ticks: int = 5

@dataclass
class CoderDirective:
    phases: dict[str, PhasePrescription] # keys: subset of {opening, midgame, endgame, emergency}
    transitions: list[Transition]        # may be empty → use recommended_phase()
    fallback: PhasePrescription          # used if no phase prescription matches
    ablation_control: str
    allowed_to_diverge: bool = False
```

Three key constraints:

1. **The Strategist must provide at least two phase prescriptions.** A single-phase directive is disallowed — the point of this extension is to force phase-awareness. Validator-enforced.
2. **Each `PhasePrescription.expected_metrics` maps metric name to a boolean expression** like `">0.8"`, `"<40"`, `"increases vs parent"`. Post-match, the orchestrator checks whether the expectations held; if not, the directive is logged as *partially-failed* with specific phase blame. This is Level-2 adaptation made concrete.
3. **`trigger_override`** lets the Strategist tune phase boundaries per-directive. Default is `recommended_phase()` (the shipped helper). Override is a one-liner pseudocode the Coder translates.

### 4.3 Coder prompt changes

Coder prompt (revised from dual-LLM doc §7.2) gains:

```
# Phase state machine

You MUST implement a phase state machine. The ABI helpers in
`phase.h` (included automatically) provide:
- `current_phase(memory)` reads my_memory[MEM_PHASE].
- `update_phase(memory, new_phase)` writes with hysteresis.
- `recommended_phase(my_alive, their_alive, tick, est_len)` default.

Your drone_ai must, on every tick:
1. Compute the intended phase (via recommended_phase or the
   trigger_override bodies supplied per phase in the directive).
2. Call update_phase() with the result.
3. Dispatch behaviour by current_phase(memory):

    switch (current_phase(memory)) {
      case PHASE_OPENING:   { <directive.phases.opening.behavior> } break;
      case PHASE_MIDGAME:   { <directive.phases.midgame.behavior> } break;
      case PHASE_ENDGAME:   { <directive.phases.endgame.behavior> } break;
      case PHASE_EMERGENCY: { <directive.phases.emergency.behavior> } break;
      default:              { <directive.fallback.behavior> }
    }

Do not invent new phases. Do not bypass the hysteresis helpers.
```

### 4.4 Match-phase verification (closing the loop)

After the match runs, the orchestrator reads `aar.json.transitions` (from Extension A) and checks:

- Did the AI actually enter each phase the directive prescribed? If the directive prescribed an `emergency` phase but the AI never exited `midgame`, log `phase_unused = ["emergency"]` on the journal entry.
- Did `expected_metrics` hold within each phase? For each phase × metric pair, compute satisfied / not-satisfied. Log as `phase_expectations_satisfied: {"opening.cooldown_util": true, "endgame.focus_fire_redundancy": false}`.

This is the feedback signal that lets the Strategist tell *next generation*:
> *"Your endgame prescription was implemented but cooldown utilisation stayed low during endgame — the behaviour bullet `focus-fire nearest` does not engage enough targets; propose a different endgame behaviour."*

Without this post-match verification, Extension B is just a syntax change. With it, phase-level hypotheses become falsifiable.

### 4.5 Risks (Extension B)

- **B-R1: Hysteresis flapping.** LLM may write phase logic that oscillates despite the helper. **Mitigation:** `update_phase` is the *only* way to write `memory[MEM_PHASE]`. The safety linter (extend M6) rejects direct writes to `memory[14..15]`. Tested in a unit test that scans for direct `my_memory[14]` assignments.
- **B-R2: Phase prescriptions are all "pursue nearest and shoot."** Strategist produces four phase bullets that are the same behaviour reworded. **Mitigation:** validator computes token-level overlap between phase prescriptions; if >70% overlap across all phases, the directive is rejected as "phase-collapsed" and the Strategist is retried with the critique *"your phase prescriptions are too similar; the point of phases is differentiated behaviour."*
- **B-R3: Emergency phase never triggers in practice.** On Track A vs pursuit_v1, we may rarely be outnumbered. Dead code. **Mitigation:** this is *information*, not a bug. The journal logs `phase_used = ["opening", "midgame", "endgame"]` with emergency absent; after 20 matches with emergency unused, the Strategist is prompted to either remove the emergency prescription (simplification) or propose a different emergency trigger (exploration). Either choice is learnable.
- **B-R4: State machine consumes `my_memory` slots AI already uses.** Existing champions may rely on `memory[14..15]`. **Mitigation:** this is a breaking ABI change for older code. Track A champions must be ported or regenerated. Bump `AI_ABI_SCHEMA` version and gate: code compiled against v1 ABI is flagged as legacy and cannot be used as parent code for v2 runs.
- **B-R5: Hand-written `phase.h` over-constrains tactic space.** The fixed 4-phase enum may be wrong for some tactics. **Mitigation:** Extension C's tactic library allows open-vocabulary phase names that the Coder maps to `PHASE_*` slots; phase semantics are directive-defined, slot indices are fixed. If 4 slots becomes limiting, bump to 8 in a future ABI revision.

### 4.6 Success criteria (Extension B)

Extension B is successful on a post-dual-LLM Track A run iff:

1. ≥ 80% of accepted champions use ≥ 3 distinct phases in at least one match.
2. `phase_expectations_satisfied` rate ≥ 0.7 across lineage (Strategist predictions about per-phase metrics usually hold).
3. At least one lineage produces a champion that beats pursuit_v1 with fitness ≥ 0.9 **and** uses `PHASE_EMERGENCY` at least once (i.e. phase-awareness is doing causal work).
4. No regression on M20 byte-identical reproduce harness.

## 5. Extension C — Tactic library, combinator grammar, and M18-as-promotion-gate

### 5.1 Problem

Even with phase-awareness, each generation starts cold. Gen-50's Strategist proposes "tight-ring formation with focus fire"; gen-51's Strategist, seeing the same AAR, might re-propose "loose formation kiting" — not because gen-50's tactic failed, but because there's no mechanism to *name, store, and reference* a successful tactic across generations. The vocabulary stays ephemeral.

For strategies to **emerge**, the vocabulary must grow: primitives get coined, tested, refined, retired. Later directives reference earlier primitives by name. Compositions of primitives become new primitives. This is the substrate the current system lacks.

### 5.2 Design — three components

#### 5.2.1 Tactic primitive registry (`tactic_library.jsonl`)

Per-lineage append-only file at `data/runs/<track>/<model>/<seed>/tactic_library.jsonl`. One row per primitive revision:

```json
{
  "schema_version": 1,
  "primitive_id": "P017",
  "name": "corner_kite",
  "coined_generation": 42,
  "definition": {
    "applicable_phases": ["emergency", "endgame"],
    "prerequisites": ["our_alive <= 4", "their_alive >= our_alive + 2"],
    "prescription": [
      "move toward nearest arena corner",
      "engage only enemies within disable_range * 0.7",
      "prefer targets with low cooldown (implied by repeated shots)"
    ],
    "expected_metrics": {
      "opponent_kiting_score": "<0.3",
      "cooldown_util": ">0.6"
    }
  },
  "evidence": [
    {"generation": 42, "fitness_delta": +0.15, "m18_verified": true},
    {"generation": 67, "fitness_delta": +0.08, "m18_verified": true},
    {"generation": 89, "fitness_delta": -0.02, "m18_verified": false}
  ],
  "status": "active",
  "staleness": 0.12,
  "opponent_scope": "sha:abc123 (pursuit_v1)"
}
```

Key fields:
- **`primitive_id`** is monotonically assigned. Primitives are never deleted; retired primitives get `status: "retired"` with `retired_generation`.
- **`staleness`** decays toward 0 with non-use, increases toward 1 when recent uses fail. Formula: `staleness(t) = clamp(staleness(t-1) * 0.95 + penalty(t), 0, 1)` where `penalty(t)` is +0.2 for a failed use, −0.1 for a successful use, 0 for non-use.
- **`opponent_scope`** pins the primitive to the opponent it was learned against. If the opponent changes, primitives carry `"verified_against: [sha:abc]", "unverified_against: [sha:def]"`.

#### 5.2.2 Library-aware Strategist prompting

Strategist prompt (revised from dual-LLM doc §7.1) gains:

```
# Your lineage's tactic library

You have {N_ACTIVE} active primitives ({N_RETIRED} retired) from this
lineage. Each primitive has a usage count, success rate, and staleness
score. You may:

1. **Cite** an existing primitive by id in your coder_directive. The
   Coder will look up the primitive's prescription and expected_metrics.
2. **Compose** two or three primitives via sequential composition (phase-
   scoped: "P017 in endgame; P004 in midgame"). The Coder implements
   each primitive in its assigned phase.
3. **Coin** a new primitive if no existing one fits. You MUST propose
   a name, prerequisites, and prescription. Coining is expensive —
   prefer composition when possible.
4. **Refine** an existing primitive by proposing an updated
   prescription referencing its id. The refinement is added as a new
   row with `parent_primitive_id` set.
5. **Retire** a primitive whose staleness > 0.8, explaining why.

Your directive's `library_operations` field must be one of:
{cite, compose, coin, refine, retire}, and must be justified against
the phase-segmented AAR you just observed.

Current library (staleness-sorted, active only):
{LIBRARY_TABLE}
```

The library table is a rendered markdown summary:

```
| id   | name           | phases          | uses | success_rate | stale |
|------|----------------|-----------------|------|--------------|-------|
| P017 | corner_kite    | emergency, end  | 12   | 0.83         | 0.12  |
| P004 | ring_tight     | midgame         | 31   | 0.71         | 0.08  |
| P022 | feint_flanker  | opening         | 3    | 0.67         | 0.35  |
| ...                                                                   |
```

Strategist is biased toward composition (cheap, consolidating) over coining (expensive, exploratory). The ratio of coin:cite:compose is a monitorable metric; healthy lineages should show coin ≫ at gen-1, declining to roughly equal by gen-100.

#### 5.2.3 M18-as-promotion-gate

Today, M18's tactic detector (`scripts/tactic_detector.py`) runs post-hoc on traces and produces labels nobody reads. In the new design, M18 becomes the **verification gate** for primitive claims:

- When a directive cites primitive `P017` ("corner_kite"), the orchestrator runs the match and then invokes `tactic_detector.detect(trace_path, primitive="corner_kite")`. This is a deterministic check that looks for the *structural pattern* in the trace (e.g. "≥3 drones within 100 units of an arena corner for ≥5 contiguous ticks while engaging").
- If M18 detects the pattern → `m18_verified: true` on the evidence row. Success counts.
- If M18 does *not* detect the pattern → `m18_verified: false`. The Coder claimed to implement corner_kite, but the behaviour doesn't match the pattern. This is *evidence of Coder unfaithfulness* and is surfaced to the Strategist: *"Your directive cited P017 corner_kite, but the resulting AI did not actually execute the pattern. Either the Coder mistranslated or P017's definition needs refinement."*

This turns M18 into a load-bearing closed-loop signal rather than shelf-ware. It also gives the Strategist a second blame-attribution dimension: in addition to "fitness delta sign was wrong," there's now "the tactic I cited was not actually implemented."

#### 5.2.4 Pattern definitions (the bridge between prose and detection)

Each primitive in the library must have a **structural pattern** that M18 can check. The pattern is authored when the primitive is coined. Example:

```json
{
  "primitive_id": "P017",
  "pattern": {
    "detector": "corner_proximity_with_engagement",
    "params": {
      "corner_radius": 100,
      "min_drones": 3,
      "min_contiguous_ticks": 5,
      "must_engage": true
    }
  }
}
```

The detector name maps to a registered function in `scripts/tactic_detector.py`. The set of registered detectors is closed (growing) — when the Strategist coins a primitive, it must choose from the existing detector catalog or propose a new detector (which becomes a code-review item, not an automatic addition). This keeps the pattern language grounded and auditable.

The initial catalog at milestone-start should include ~10 detectors covering primitives we expect to emerge: `corner_proximity_with_engagement`, `tight_ring`, `line_abreast`, `pincer`, `bait_sacrifice`, `focus_fire_sustained`, `disengage_to_cooldown`, `spread_flanking`, `mass_push`, `hold_perimeter`. Extend as the lineage surfaces new patterns.

### 5.3 Composition grammar (minimum viable)

Phase-1 of Extension C ships with **sequential composition only**: "primitive A in phase X, primitive B in phase Y." This maps cleanly to Extension B's phase state machine.

Phase-2 (later milestone) may add:
- **Conjunctive composition**: "A and B simultaneously" (e.g. focus_fire + tight_ring).
- **Fallback composition**: "A, or B if A fails." Requires defining primitive failure detection.
- **Parameterised composition**: "A(radius=80) in phase X." Primitives become small functions with a few tunable scalars.

These are deferred because sequential composition alone is already expressive enough to test the emergence hypothesis, and the others have non-trivial design work on the Coder's side.

### 5.4 Risks (Extension C)

- **C-R1: Library ossification.** Once `P004` has 31 successful uses, Strategist cites it reflexively even against changed opponents. **Mitigation:** staleness decay; mandatory re-verification when `opponent_scope` changes; periodic "fresh start" generation every 30 gens where Strategist is forbidden from citing primitives older than 20 gens.
- **C-R2: Coining-spam.** Strategist coins a new primitive every generation instead of citing. Library bloats; nothing accumulates. **Mitigation:** coining requires the Strategist to justify *why no existing primitive fits*. A `similarity_score` is computed (embedding-based or tag-overlap-based) between proposed and existing primitives; score > 0.7 blocks coining and forces a cite or refine.
- **C-R3: Compositions are incoherent.** Strategist composes "corner_kite + mass_push" which is self-contradictory. **Mitigation:** primitives carry `prerequisites` (e.g. corner_kite requires `our_alive <= 4`); composition validator checks prerequisite compatibility. Incompatible compositions are rejected with a specific critique back to the Strategist.
- **C-R4: M18 detectors are too strict or too lenient.** A detector that always returns false makes the gate useless; a detector that always returns true makes verification meaningless. **Mitigation:** each detector ships with its own unit tests against known-positive and known-negative traces. A detector that fails either set is held in a staging area until fixed. Acceptance tests live in `tests/test_tactic_detector.py`.
- **C-R5: Library grows without bound.** After 1000 generations, lineage has 400 primitives most of which are never cited. **Mitigation:** primitives with `uses == 0` and `age > 50 gens` are auto-retired. The active-library size presented to the Strategist is capped (e.g. top-30 by usage × success_rate × (1 − staleness)). Full library remains in the file for audit.
- **C-R6: Cross-lineage transfer is absent.** Each seed's library is independent; learning doesn't pool across seeds. **Mitigation:** deliberate for Phase 1 (keeps experimental conditions clean). Post-Phase 1, introduce a `tactic_library_global.jsonl` at `data/runs/<track>/<model>/` aggregated across seeds, with seed-of-origin tracked per primitive. This is a known-desirable follow-up but not required for initial emergence experiments.
- **C-R7: M18 detector design becomes the bottleneck.** Every new primitive needs a new detector, and detector writing is manual engineering work. **Mitigation:** the detector catalog is intentionally small and closed-additive. The Strategist is biased toward reusing the ~10 seed detectors. An LLM-generated detector path is a tempting future direction but explicitly deferred — the moment detectors are LLM-authored, they stop being a trustworthy promotion gate.
- **C-R8: Emergence doesn't happen.** The richest risk. We build all this infrastructure and the library just reflects the seed catalog; nothing novel emerges. **Mitigation:** this is the experiment, and the null result is itself informative — it would tell us that with Haiku-class models and the current prompt, the strategic space is not rich enough for emergence to spontaneously occur. The response would be (a) try larger Strategist models, (b) introduce opponent diversity, or (c) adjust the reward signal to explicitly prefer novelty. Each of these is a separate experiment the infrastructure now supports.

### 5.5 Success criteria (Extension C)

Success on a 300-gen Track A run with Extension C enabled iff:

1. **Library growth**: the active library reaches ≥ 15 primitives by gen 100 and stabilises (new-primitive-rate drops below 0.1/gen) by gen 200.
2. **Citation dominance by midgame**: by gen 100, ≥ 50% of directives cite or compose existing primitives rather than coining.
3. **Composition emergence**: by gen 200, ≥ 10% of directives use composition (≥2 primitives in one directive).
4. **M18-verification rate**: ≥ 0.75 across the lineage. Below 0.5 means Coder fidelity is too low; below 0.75 suggests detector-catalog mismatch.
5. **Fitness-per-primitive-use**: primitives with >5 uses have a success rate ≥ 0.6. (Primitives that sound good but don't actually help are retired.)
6. **Emergent novelty (qualitative)**: human review of the gen-200 library finds at least one primitive whose definition describes a tactic no prompt or seed-detector explicitly suggested. This is the real research check.
7. Self-play (Track B) library growth outpaces Track A (the opponent changes, so more primitives get coined/retired).

### 5.6 Research output

The gen-200 tactic library, rendered as a dependency graph (primitives cite their parents through `refine` operations, and compositions cite their components), is the primary research artefact of this milestone. It is what distinguishes the project from "LLM writes good drone AI" and lets it claim "system accumulates strategic vocabulary over time." The library is saveable, diffable, and comparable across models.

## 6. Full dependency graph

```
┌─────────────────────────────────────────────────────────────┐
│ Extension C — Tactic library + M18 promotion gate           │
│ (emergent strategies, library growth, composition)          │
└─────────────────────────┬───────────────────────────────────┘
                          │ requires
┌─────────────────────────▼───────────────────────────────────┐
│ Extension B — Phase state machine + phase-aware directive   │
│ (state-dependent tactics within a match)                    │
└─────────────────────────┬───────────────────────────────────┘
                          │ requires
┌─────────────────────────▼───────────────────────────────────┐
│ Extension A — Phase-segmented AAR                           │
│ (Strategist sees phase-specific failure)                    │
└─────────────────────────┬───────────────────────────────────┘
                          │ requires
┌─────────────────────────▼───────────────────────────────────┐
│ Dual-LLM split (Strategist + Coder)                         │
│ (reflection channel + parent code in prompt)                │
└─────────────────────────┬───────────────────────────────────┘
                          │ requires
┌─────────────────────────▼───────────────────────────────────┐
│ Single-LLM MVP (retrospective §8.1–8.3)                     │
│ (parent code + changelog block — A/B baseline)              │
└─────────────────────────────────────────────────────────────┘
```

Skip-level builds are possible in principle (e.g. Extension B on single-LLM) but each skip invalidates evaluation: without the Strategist, phases are hardcoded, which isn't emergence.

## 7. Rollout plan (refining dual-LLM doc §10)

| Phase | Content | Doc | Duration | Gate |
|---|---|---|---|---|
| 0 | Single-LLM MVP (parent code + changelog) | retrospective §8.1–8.3 | 0.5 day | fitness gain vs pre-MVP Track A |
| 1 | Dual-LLM wiring + Extension A (phase AAR) | dual-LLM + this §3 | 5 days | dual-LLM directive schema works; AAR v2 tests pass |
| 2 | Extension B (phase state machine + phase-aware directive) | this §4 | 4 days | ≥80% of champions use ≥3 phases; B §4.6 criteria met |
| 3 | Extension C seed catalog + library plumbing | this §5 | 3 days | 10 seed detectors registered, `tactic_library.jsonl` writes, citations parse |
| 4 | Extension C integration (M18-as-gate, composition) | this §5 | 4 days | library growth criteria §5.5 begin to register on 100-gen runs |
| 5 | Track B/C multi-seed experiments + cross-lineage aggregation | future | 5+ days | §5.6 research output |

Total: ~21.5 days of engineering to go from the current state to a system that can be honestly called self-improving *with emergent strategies*. Roughly 5× the dual-LLM-alone budget, but the dual-LLM alone does not deliver on the project's research thesis.

## 8. Telemetry and interpretability

Each of the three extensions adds interpretability artefacts:

| Extension | New artefact | Purpose |
|---|---|---|
| A | `aar.json.by_phase`, `aar.md` phase table | Localise metric failure in time |
| B | `journal_entry.phase_expectations_satisfied`, `phase_used`, `phase_unused` | Phase-level hypothesis outcomes |
| C | `tactic_library.jsonl`, `directive.json.library_operations`, `evidence[].m18_verified` | Strategy accumulation and verification |

Together these produce a **dense, queryable record of every generation's strategic bet, its implementation fidelity, and its observed outcome.** Research-grade output the project cannot produce today.

## 9. Open questions

1. **Should phase detection be per-drone or per-team?** Currently `phase.h` implies per-drone (each drone looks at alive counts and decides). This allows some drones to be in `ENGAGE` while others are in `KITE`. Alternative: team-level phase broadcast via `message_out`. **Recommendation:** start per-drone (simpler, current ABI supports it); add team-phase broadcast as an optional directive feature in Phase 4.
2. **Should `my_memory[15]` (dwell counter) be exposed to the Strategist in the AAR?** It would let the Strategist diagnose "you entered endgame but immediately left it." **Recommendation:** yes — add `phase_dwell_histogram` to `aar.json.by_phase`.
3. **How are primitives serialised for the Coder?** The Coder receives the prose prescription. But nothing stops the Coder from mis-implementing primitive `P017` in ways that still pass M18's structural check. **Recommendation:** accept this; the structural check is deliberately loose so that the Coder has implementation freedom within a tactic family. If two different C++ implementations both satisfy M18's `corner_proximity_with_engagement`, they are both "corner_kite" for our purposes.
4. **Cross-seed library pooling — when?** Phase 5 above, but earlier pooling (say Phase 3) would speed emergence. **Risk:** experimental independence between seeds is lost. **Recommendation:** keep independent in Phase 1–4; introduce opt-in aggregation in Phase 5 with a clearly-marked `lineage_scope: "global"` flag.
5. **Should the library be shared across model choices, or scoped per model?** A library learned by Opus-Strategist may confuse Haiku-Strategist because Haiku can't compose at the same level. **Recommendation:** scope per `(opponent_sha, strategist_model)` initially; cross-model transfer is a separate research question.

## 10. Non-goals

- Changes to the physics or combat engine.
- Changes to the fitness definition or statistical gates.
- Automatic opponent curriculum (mentioned in dual-LLM §R11 as adjacent but orthogonal).
- LLM-authored M18 detectors (explicit trust-anchor preservation).
- Cross-track library transfer (Track A → Track B primitive reuse).

## 11. Summary

The dual-LLM proposal fixes the *cross-generation* reflection loop. This document fixes the *intra-match* tactical adaptation loop (Extension B) and the *multi-generation strategic accumulation* loop (Extension C), with Extension A as the telemetry prerequisite that enables both.

Emergence is not free. It requires:
- data that shows *where* in the match things fail (Extension A),
- code that *can* adapt within a match (Extension B),
- a vocabulary that *persists and composes* across matches (Extension C).

With all three extensions, the system has the infrastructure to accumulate a named, verified, phase-scoped tactic library across a lineage. **Whether advanced tactics actually emerge is then an empirical question about model capability and opponent diversity**, not an architectural one. That is the right boundary to reach before claiming the project's research thesis.

---
*End of phase-and-tactics architecture document. Review target: engineering + research leads. Paired for review with `ARCHITECTURE_SHIFT_DUAL_LLM.md`.*
