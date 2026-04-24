# Architecture Shift: Dual-LLM (Strategist + Coder) with Role-Specialised Models

**Status:** Design proposal for review — not yet implemented
**Supersedes:** The single-LLM path in `scripts/evolve.py` (call site at `evolve.py:736` → `llm_client.AnthropicClient`)
**Depends on:** Current M15b AAR, M15c journal schema, M16 plumbing; does not require M17 κ-gate to pass
**Companion docs:**
- `docs/RETROSPECTIVE_SELF_IMPROVEMENT.md` — the empirical analysis that motivated this design
- `docs/ARCHITECTURE_PHASE_AND_TACTICS.md` — three follow-on extensions (phase-segmented AAR, phase-aware directives, tactic library). **Extension A is folded into Phase 1 of this rollout as a required dependency**; Extensions B and C are described in that companion doc and are required to deliver *emergent strategies within matches and across generations* (the project's research thesis).

**Scope boundary.** The dual-LLM split described here fixes the *cross-generation* reflection loop (Strategist reflects → Coder implements → fitness observed → Strategist re-plans). It does **not** by itself deliver:
- within-match state-dependent tactics (static C++ from one directive cannot adapt to e.g. being outnumbered mid-match) — see phase-and-tactics doc §4 / Extension B
- emergent novel tactics accumulating across generations — see phase-and-tactics doc §5 / Extension C

These are explicitly out of scope for this document. This doc is the load-bearing prerequisite for both.

---

## 1. Motivation and one-sentence summary

> *Split the monolithic "produce better C++" prompt into two specialised roles: a thinking-model **Strategist** that reads match telemetry and produces a structured strategic directive, and a coding-model **Coder** that translates that directive into GPU-safe C++.*

The retrospective established that the current loop's strategy-learning channel is empty: reflections are deterministic boilerplate, the LLM has no output slot for reasoning, and parent code is not shown. This document proposes the full architectural fix.

## 2. The current architecture (as of post-Track-A)

### 2.1 Data flow

```
┌──────────────────┐
│ AAR (M15b)       │
│ cooldown_util,   │
│ focus_fire, ...  │
└────────┬─────────┘
         │
┌────────▼─────────┐        ┌────────────────────┐
│ Journal (M15c)   │───────▶│ Single-LLM prompt  │──────┐
│ (rule-based      │        │ (evolve_ai.md)     │      │
│  boilerplate)    │        │ ~18K chars         │      │
└──────────────────┘        └────────────────────┘      │
         ▲                                              │
         │                                              ▼
         │                          ┌──────────────────────────┐
         │                          │ AnthropicClient          │
         │                          │ model = claude-haiku-4-5 │
         │                          │ system: "C++ engineer,   │
         │                          │  fenced cpp block only"  │
         │                          └────────────┬─────────────┘
         │                                       │
         │                                       ▼
         │                          ┌──────────────────────────┐
         │                          │ candidate.cpp            │
         │                          │ (one translation unit)   │
         │                          └────────────┬─────────────┘
         │                                       │
         │                          ┌────────────▼─────────────┐
         │                          │ compile → match → fitness│
         │                          └────────────┬─────────────┘
         │                                       │
         └───────────────────────────────────────┘
                (deterministic _deterministic_journal_entry)
```

### 2.2 Problems inherent in this shape

1. **Single role overloaded.** The one LLM call must diagnose + strategise + code + satisfy style rules. Role conflict starves every non-coding function.
2. **Reflection has no output channel.** The prompt's response format explicitly forbids prose outside the `cpp` block.
3. **No parent code in prompt.** The generator rewrites from scratch every generation — no local-search gradient exists.
4. **Journal is a lie.** Rule-based template pretending to be reflection; M17's judge correctly scores it as flat.

## 3. Proposed architecture (dual-LLM)

### 3.1 Data flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Per-generation input bundle                                     │
│ ┌────────────┐  ┌────────────┐  ┌────────────────────────────┐ │
│ │ AAR        │  │ Parent code│  │ Last N strategist          │ │
│ │ metrics    │  │ (full .cpp)│  │ directives (not all journal)│ │
│ └────────────┘  └────────────┘  └────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────▼──────────────┐
              │ STRATEGIST LLM            │
              │ model: thinking-tier      │
              │   (e.g. claude-opus-4,    │
              │    claude-sonnet-4-5,     │
              │    gpt-o3, etc.)          │
              │ system: "strategist"      │
              │ temperature: 0.7          │
              │ max_tokens: 1500          │
              └────────────┬──────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ StrategicDirective (JSON, typed)     │
        │ - diagnosis (cites ≥2 AAR metrics)   │
        │ - strategy.headline                  │
        │ - strategy.target_metric_moves[]     │
        │ - coder_directive.must_implement[]   │
        │ - coder_directive.must_avoid[]       │
        │ - coder_directive.pseudocode_hint    │
        │ - meta.implementability_self_rating  │
        └────────────┬─────────────────────────┘
                     │
   ┌─────────────────┴─────────────────┐
   ▼                                   ▼
[validator:                   [journal writer:
 schema OK?                    append LLM-authored entry
 metrics grounded in AAR?      with directive + grading-slots]
 non-empty diagnosis?]
   │
   │ (rejected → strategist retry, N=1)
   ▼
┌──────────────────────────┐
│ Rendered coder prompt    │
│ ~4-6K chars:             │
│ - ABI headers            │
│ - Safety rules           │
│ - PARENT CODE            │
│ - directive.must_implement│
│ - directive.pseudocode   │
│ (NO raw AAR, NO journal) │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ CODER LLM                │
│ model: coding-tier       │
│   (e.g. claude-sonnet-4-5│
│    + thinking off,       │
│    qwen2.5-coder-32b,    │
│    deepseek-v3-coder)    │
│ system: "careful C++ eng"│
│ temperature: 0.2         │
│ max_tokens: 4000         │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ candidate.cpp            │
│ + ## Changelog (parsed)  │
│ + ## Refusal? (optional) │
└────────────┬─────────────┘
             │
     compile → match → fitness
             │
             ▼
┌──────────────────────────────────────────┐
│ Journal entry (LLM-authored by strategist│
│ + augmented with coder changelog + fitness│
│ outcome). Metric citations grounded       │
│ against post-match AAR.                   │
└──────────────────────────────────────────┘
```

### 3.2 Key shape changes vs. current

| Aspect | Before | After |
|---|---|---|
| LLM calls per generation | 1 | 2 (+ optional retry per role) |
| Reflection author | `_deterministic_journal_entry` template | Strategist LLM, validated |
| Parent code visibility | Absent | Injected into Coder prompt |
| Prompt size (Coder) | ~18K chars | ~4–6K chars (tighter, directive-shaped) |
| Journal field `hypothesis_tested` unique values per 150 gens | 1 | expected ≥ 30 |
| Model specialisation | One model all roles | Thinking tier strategises, coding tier codes |

## 4. Role contracts

### 4.1 Strategist role

**Responsibility.** Given the most recent match's AAR, the parent code, and the last N strategist directives + their observed fitness outcomes, produce a `StrategicDirective`.

**Must do.**
- Cite ≥ 2 AAR metrics by name and value in the diagnosis (validator-enforced, grounded against the AAR JSON).
- Produce a falsifiable `expected_fitness_delta_sign` (+, −, or 0) and at least one `target_metric_move`.
- Write a `pseudocode_hint` between 5 and 20 lines that describes the intended algorithm at the level of *"for each ally with cooldown==0, find nearest enemy inside ring R=80..."*, not at the level of C++ syntax.
- Self-rate `implementability_self_rating` honestly — a directive with `implementability=low` but `expected_fitness_delta_sign=+` will be flagged for reviewer attention.

**Must not do.**
- Emit C++ code. The Coder owns syntactic choices.
- Reference variable names, header files, or ABI symbols beyond what appears in `src/types.h` and the AAR metric vocabulary.
- Read its own prior *prose* reasoning from other generations (prevents self-confirmation bias). It sees only its prior *directives* and their measured fitness outcomes — objective consequences, not its own self-talk.

**Output schema.** See §5.

### 4.2 Coder role

**Responsibility.** Given a `StrategicDirective`, the ABI headers, safety rules, and parent code, produce `candidate.cpp` that maximally faithfully implements `coder_directive.must_implement` while respecting `must_avoid`.

**Must do.**
- Begin with the parent code as the default baseline. Modify only the regions that the directive specifies.
- Pass all existing M6 safety checks (guard-injected loops, no heap, namespace isolation).
- Emit a `## Changelog` block before the `cpp` block describing: {diagnosed, changed, hypothesis, parent_lines_kept_pct}. This is parsed into the journal entry alongside the strategist's directive.

**May do.**
- Emit a `## Refusal` block if `coder_directive.must_implement` is genuinely implementable only by violating a hard safety rule. Refusal goes to the journal and triggers a strategist retry in the *next* generation with the refusal as context. Refusal does **not** retry the coder within the same generation.

**Must not do.**
- Re-derive strategy. If the Coder disagrees with the directive, it must Refuse, not silently improvise.
- Read the AAR or journal directly. Its only channel to telemetry is through the Strategist's directive.

### 4.3 Validator (orchestrator-side, no LLM)

A deterministic Python function that sits between the two LLMs:
- Parses Strategist output into `StrategicDirective` pydantic model.
- Checks `diagnosis` cites metrics that match AAR within 1% relative tolerance (inherits M15c spec).
- Checks `coder_directive.pseudocode_hint` is within length bounds.
- On failure, retries Strategist once with a structured error message. Second failure falls back to the last-known-good directive from the lineage, logged with `validation.fallback: true`.

## 5. `StrategicDirective` schema v1

```python
# scripts/strategic_directive.py (new)
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Sign(str, Enum):
    POS = "+"; NEG = "-"; ZERO = "0"

@dataclass
class MetricMove:
    metric: str            # must be a key present in AAR json
    from_value: float
    to_value: float
    why: str               # one-sentence mechanism

@dataclass
class Diagnosis:
    what_happened: str                       # 1 paragraph, ≤ 600 chars
    root_cause_hypothesis: str               # 1 sentence
    aar_metrics_cited: dict[str, float]      # must match AAR, 1% tol
    confidence: Confidence

@dataclass
class Strategy:
    headline: str                            # one-line thesis
    target_metric_moves: list[MetricMove]    # ≥ 1
    tactic_vocabulary_tags: list[str]        # free-form; tracked for M18
    expected_fitness_delta_sign: Sign

@dataclass
class CoderDirective:
    must_implement: list[str]                # ≥ 1, each a bullet
    must_avoid: list[str]                    # may be empty
    pseudocode_hint: str                     # 5–20 lines, plain English
    ablation_control: str                    # "what single change vs parent"
    allowed_to_diverge: bool                 # default False

@dataclass
class DirectiveMeta:
    implementability_self_rating: Confidence
    parent_generation: int | None
    strategist_model: str
    strategist_tokens: tuple[int, int]       # (input, output)
    schema_version: Literal[1] = 1

@dataclass
class StrategicDirective:
    diagnosis: Diagnosis
    strategy: Strategy
    coder_directive: CoderDirective
    meta: DirectiveMeta
```

This is persisted as `data/runs/<track>/<model>/<seed>/gens/XXXX/directive.json` and is diff-visible in the run bundle. It becomes the primary artefact the journal is built from.

## 6. Model selection policy

### 6.1 Design principle

> **Route by cognitive profile, not by cost.** The Strategist's job is open-ended reasoning over partial evidence; the Coder's job is constrained translation under hard syntax rules. These are different skills; models vary on them independently.

### 6.2 Recommended model tiers

| Role | Primary requirement | Recommended models (Q2 2026 availability) | Why |
|---|---|---|---|
| **Strategist** | Extended reasoning, hypothesis generation, counterfactuals | Claude Opus 4.7, Claude Sonnet 4.5 with extended thinking, OpenAI o3-family, Gemini 2.5 Pro Thinking, DeepSeek R1 | These models expose visible-thinking or extended-thinking modes. The strategist's value comes from the reasoning chain, not the final JSON. |
| **Coder** | Deterministic C++17 correctness, tight adherence to ABI, no creative deviation | Claude Sonnet 4.5 (thinking off), GPT-4.1, Qwen2.5-Coder-32B, DeepSeek-Coder-v3, Codestral | Coding-specialised models have been trained on syntactic constraints; they follow "do X, don't do Y" directives more literally than general models. |

### 6.3 Allowed configurations

Introduce `scripts/llm_client.py` support for per-role config, mapped through new CLI flags on `evolve.py` and `tracks/*.py`:

```
--strategist-model <model-id>      # default: anthropic/claude-sonnet-4-5
--strategist-provider {anthropic,openai,local,mock}
--coder-model <model-id>           # default: anthropic/claude-haiku-4-5
--coder-provider {anthropic,openai,local,mock}
--strategist-thinking {on,off,auto}   # new: enables extended thinking if model supports it
```

This replaces the current single `--model` flag. `--model` is kept as a backward-compat alias that sets both roles to the same value with a deprecation warning.

### 6.4 Token economics under the dual-LLM design

Rough estimate for a 150-gen Track A lineage:

| Role | Per-gen in/out | Per-gen tokens | × 150 gens |
|---|---|---|---|
| Strategist (Sonnet-thinking) | ~3K in / ~1K out + ~2K thinking | ~6K | ~900K |
| Coder (Haiku) | ~6K in / ~3K out | ~9K | ~1.35M |
| **Total per lineage** | | ~15K | **~2.25M** |
| 3 seeds × 150 gens | | | **~6.75M** |

Versus the observed Track A run which burned 3.19M tokens for 3 seeds × (150+150+103) gens ≈ 403 gens total — i.e., ~7.9K tokens/gen. The dual-LLM design is roughly **1.9× the token cost per generation** but is expected to converge on a high-fitness champion in meaningfully fewer generations (estimated 2–4×), netting out to **neutral or cheaper per unit of fitness gained**.

This is a hypothesis, not a guarantee. §10 specifies the A/B test.

## 7. Prompt templates

### 7.1 `prompts/strategist.md` (new)

Skeleton:
```
You are the Strategist for SwarmEvolve — a drone-swarm combat testbed.
You do not write C++. You produce a structured strategic directive that a
Coder model will implement. Your value is in accurate diagnosis and
concrete tactic proposals.

# Match telemetry (last generation, team {TEAM_LETTER} perspective)

The AAR below is phase-segmented (AAR v2 — see Extension A). It reports
metrics both globally and per-phase (opening/midgame/endgame) and per-
balance-state (ahead/even/outnumbered). Your diagnosis should *localise*
failure to a phase or balance-state when possible. A metric that looks
healthy globally may be catastrophically bad in endgame, and vice versa.

{AAR_MARKDOWN}

# Parent code (what the Coder implemented last time)
```cpp
{PARENT_CODE}
```

# Your prior directives and their observed outcomes (last {N} generations)
{PRIOR_DIRECTIVES_TABLE}
 (columns: gen, headline, expected_sign, observed_fitness_delta, succeeded?)

# What to produce

Return exactly one fenced ```json``` block conforming to
StrategicDirective v1 (schema below). Fields with validator constraints
are marked [V]; violating them triggers a retry.

{SCHEMA_SUMMARY}

# Hard rules
- Every number in diagnosis.aar_metrics_cited must appear verbatim in
  the AAR with ≤1% relative error. [V]
- coder_directive.must_implement must have ≥ 1 bullet and ≤ 6. [V]
- pseudocode_hint must be 5–20 lines of plain English. No C++. [V]
- You may propose radical changes but must set ablation_control to
  identify the single load-bearing modification vs parent code.
- If the last 3 of your directives have all produced
  observed_fitness_delta ≤ 0, you MUST propose a tactic with a
  different headline than any of those three.
```

### 7.2 `prompts/evolve_ai.md` (revised)

Cut the AAR and PRIOR_LESSONS sections entirely. Add:

```
# Strategic directive for this generation
{DIRECTIVE_RENDERED_MD}

# Parent code (your starting point — modify, do not rewrite from scratch
  unless the directive.allowed_to_diverge is true)
```cpp
{PARENT_CODE}
```

# Response format

Return, in order:

1. A fenced ```markdown``` block with the ## Changelog section:
   - diagnosed: <one sentence, reflecting what you understood from the directive>
   - changed: <one sentence about what differs from parent>
   - hypothesis: <which directive bullet will move which metric>
   - parent_lines_kept_pct: <integer 0..100>
2. A fenced ```cpp``` block with the entire translation unit.

If the directive requires violating a hard safety rule, instead return
only a fenced ```markdown``` block containing a ## Refusal section with:
   - why: <which rule would be violated>
   - counter_proposal: <what directive change would make this
     implementable>
No cpp block on refusal.
```

## 8. Strategic and tactical learning — what gets documented from each match

The point of the dual-LLM design is that **every match produces a structured, replayable learning artefact**, not an unread log line. Per generation, the run bundle now carries:

| File | Author | Purpose |
|---|---|---|
| `aar.json`, `aar.md` | deterministic | Ground truth telemetry (unchanged) |
| `directive.json` | Strategist LLM | The hypothesis being tested this generation |
| `coder_response.md` | Coder LLM | Changelog block (diagnosed/changed/hypothesis/kept_pct) |
| `candidate.cpp` | Coder LLM | The implementation (unchanged format) |
| `fitness.json` | engine | The observed outcome |
| `journal_entry.json` | orchestrator | Fusion of the above four into the lineage journal |

The journal entry becomes a **four-way tuple**: *(what I claimed would happen, what I implemented, what actually happened, fitness delta)*. This is the first time the data pipeline supports actual strategic learning, because:

- The strategist can be asked next generation: *"you predicted + but observed −; re-diagnose."* That is a training signal, not boilerplate.
- The M17 judge now has genuinely non-trivial text to score (strategist directives range over real strategy space) — the κ gate becomes reachable.
- The M18 tactic detector now has `tactic_vocabulary_tags` with open vocabulary, and tag diversity per lineage becomes a monitorable metric (§11.3).

### 8.1 Lineage-level learning trajectory

Given directive+outcome tuples across a lineage, the orchestrator can compute **strategist calibration score** per lineage:

```
calibration = agreement_rate(
    predicted_sign(directive.strategy.expected_fitness_delta_sign),
    observed_sign(fitness_delta)
)
```

A strategist that predicts `+` and frequently gets `−` is miscalibrated; this is a *measurable* property of the model–task pair. Across models, we can rank *"Sonnet calibrates at 0.71 on Track A, Opus at 0.65, Haiku at 0.48"* — a real empirical result about model strategic capability, not just code-generation capability.

## 9. Risks and mitigations (full inventory)

### R1 — Strategist hallucinates directives the Coder cannot implement

**Severity:** High (most likely failure mode).
**Mechanism:** Strategist proposes *"use simulated annealing over formation assignments"* or *"spawn a background thread to recompute"* — infeasible given the ABI constraints.
**Mitigation (layered):**
1. Strategist prompt lists the full `types.h` and `ai_abi.h` so it knows the ABI surface. Strategist is told *"no heap, no threads, no STL; see hard rules below"* with the same list the Coder sees.
2. Coder's `## Refusal` path logs infeasibility with a counter-proposal. The Strategist's next-gen context includes the refusal, forcing re-planning.
3. Track a metric `strategist_refusal_rate` per lineage; if it exceeds 0.2 over 20 gens, alert and downgrade the strategist (or add few-shot examples of feasible directives to its prompt).

### R2 — Blame attribution ambiguity when fitness drops

**Severity:** Medium.
**Mechanism:** A fitness decline could be (a) strategist picked a bad tactic, (b) coder mistranslated a good tactic, (c) tactic good and implemented, but opponent exploits it.
**Mitigation:**
1. Ablation pairings. Once per week (or once per seed), run:
   - **Strategist-fixed-prose × Coder-LLM**: static strategy "pursuit + focus fire" → Coder implements. Measures Coder-only marginal.
   - **Strategist-LLM × Coder-fixed-template**: LLM strategy → deterministic C++ template chooses among 5 canned patterns. Measures Strategist-only marginal.
   - Baseline: both LLM.
2. The journal tuple (predicted, implemented, observed) already gives per-generation blame signals: if `parent_lines_kept_pct = 95%` and fitness drops, blame strategy. If `parent_lines_kept_pct = 15%` and fitness drops despite a minor directive, blame coder fidelity.

### R3 — Strategist self-confirmation / sycophancy

**Severity:** Medium.
**Mechanism:** If the Strategist sees its own prior *prose* it tends to justify past choices rather than re-plan.
**Mitigation:**
1. The Strategist's prompt shows only (directive_headline, predicted_sign, observed_delta) from prior gens — *not* the prior `diagnosis.what_happened` prose. It re-derives every generation from objective outcomes.
2. A periodic "fresh-start" generation every N gens where the Strategist sees only the AAR and parent code, no prior directives at all. Cheap adversarial probe.
3. Calibration score from §8.1 is monitored; a strategist with calibration < 0.5 over a 30-gen window is auto-switched to a different model ("strategist rotation").

### R4 — Token and latency cost 2×

**Severity:** Low once justified, but noticeable on tight budgets.
**Mechanism:** Two round-trips; Strategist with thinking mode can add 1K–3K hidden-reasoning tokens billed at the model's thinking rate.
**Mitigation:**
1. Use the smaller tier for the Strategist when the task is well-scoped (Sonnet ≥ Haiku works for Track A; Opus is overkill unless Track C co-evolution reveals a need).
2. Cache the Strategist's output when the AAR is bit-identical to a prior generation (rare but possible on determinism-test runs) — deterministic cache keyed on `(aar_hash, parent_code_sha)`.
3. Batch strategist calls across seeds if the track runner can parallelise.

### R5 — Contract schema ossification

**Severity:** Medium.
**Mechanism:** Once the directive schema is in use and every lineage has historical directives, changing the schema invalidates replay.
**Mitigation:**
1. `schema_version` field on every directive, as already in `DirectiveMeta`. Orchestrator supports reading v1 and any future version concurrently via deserialisation dispatch.
2. Additive changes only — never rename or remove fields within a schema version. Breaking changes bump version.
3. A migration script `scripts/migrate_directives.py` that upgrades v1 → v2 entries when/if needed, with a dry-run mode.

### R6 — Two failure modes instead of one (observability burden)

**Severity:** Low–medium.
**Mechanism:** "Loop failed" now means one of: strategist malformed JSON, validator rejected, coder malformed response, coder refused, coder compile-failed, match engine failed.
**Mitigation:**
1. Structured `events.jsonl` already exists — add events `strategist_started`, `strategist_validated`, `strategist_rejected`, `coder_started`, `coder_refused`, `coder_compile_failed`, each with a consistent schema.
2. Per-role soft-fault counters: `max_strategist_failures` and `max_coder_failures` separate from the current `max_compile_failures`. No single counter kills the lineage for the wrong reason (see retrospective's credit-exhaustion defect).

### R7 — Strategist becomes the capability ceiling

**Severity:** Medium, and this is *the* interesting scientific risk.
**Mechanism:** If Strategist A consistently underperforms Strategist B on the same Coder, we're measuring strategist capability, not the loop's ability to self-improve.
**Mitigation:**
1. Treat this as a *feature*: the pipeline becomes a benchmark for LLM strategic capability. Run multiple strategist models against a single Coder baseline; publish a calibration + fitness table.
2. Separately, the monotonic-self-play track (Track B) with the same strategist on both sides removes the strategist-capability dimension and isolates the Coder's learning.

### R8 — Determinism regressions (M20)

**Severity:** High if unmitigated — blocks the byte-identical reproduce harness.
**Mechanism:** Two LLM calls means two sources of non-determinism per generation. Any cache or retry policy change shifts the token stream.
**Mitigation:**
1. `MockClient` extends to support two queues (strategist + coder) consumed in lockstep.
2. M20 reproduce harness `scripts/reproduce.py` is extended to assert bit-identical `directive.json` and `coder_response.md` across replays, not just final fingerprints.
3. Fingerprint function already strips unstable fields (wall_seconds, run_id); extend to strip strategist/coder `tokens` counts which vary by provider-side rounding.

### R9 — Provider heterogeneity (multi-vendor correctness)

**Severity:** Medium once we mix Anthropic strategist with, say, Qwen coder.
**Mechanism:** Different providers interpret `system` prompts differently, have different JSON-mode support, different refusal behaviour.
**Mitigation:**
1. `llm_client.py` already has an abstract `LLMClient`. Add `OpenAIClient`, `LocalOllamaClient` behind the same interface. Each exposes a `supports_json_mode()` / `supports_thinking()` method so the orchestrator can choose prompting strategy.
2. For Coder refusals, the *format* of refusal (our ## Refusal block) is standardised across providers; we do not rely on provider-native refusal hooks.
3. Integration tests at `tests/test_llm_client_matrix.py` that run identical prompts against each configured provider and snapshot behaviour.

### R10 — Information leakage across perspectives (Track B / C)

**Severity:** High for Track C, moot for Track A.
**Mechanism:** In Track C (A-vs-B co-evolution), running two strategists naively would let side-A's strategist see side-B's journal or directive if process isolation leaks.
**Mitigation:**
1. Per-side strategist context is a strict subset of per-side journal + per-side AAR. The NEXT_PHASE_PLAN §Q13 ("does LLM see opponent's journal") already decided *no*; preserve.
2. Process-level isolation of strategist calls is enforced by the track runner passing only per-side seed-dir paths — there is no cross-side import path.

### R11 — Strategist conservatism collapse

**Severity:** Medium.
**Mechanism:** A well-calibrated strategist that sees fitness has plateaued at 1.0 will stop proposing changes — *"carry forward"*. The loop dies in a local optimum.
**Mitigation:**
1. Explicit anti-stagnation clause in the Strategist prompt: if `observed_fitness_delta == 0` for the last 5 generations, the strategist is *required* to propose a directive with `headline` differing from any of the last 5 and `ablation_control` specifying a meaningful change.
2. Optional "curriculum" — once fitness hits 0.9, orchestrator swaps the opponent to a harder baseline (`pursuit_v2` when it exists) to keep the gradient alive. This is a separate milestone and orthogonal to the dual-LLM split, but the dual-LLM design makes it cheap to implement because the strategist can be told *"opponent has changed"* in one directive cycle.
3. **Deeper fix in the phase-and-tactics extensions.** Fitness=1.0 against `pursuit_v1` with a fixed-policy AI does not prove the system has found the limit of strategic space — only the limit of *this opponent's* difficulty. Extensions B and C in `docs/ARCHITECTURE_PHASE_AND_TACTICS.md` broaden what counts as "improvement" to include: using more distinct phases, growing the tactic library, composing primitives successfully. Under those success criteria, fitness=1.0 is *not* a terminating state — the strategist continues to receive progress signal from phase coverage and library diversity metrics. This is the right long-term fix to conservatism collapse; §R11 mitigations (1) and (2) here are the short-term patches.

### R12 — Strategist-rating-itself adversarial risk

**Severity:** Low but worth flagging.
**Mechanism:** If the same model is Strategist *and* M17 judge, it scores its own reflections highly.
**Mitigation:**
1. The M17 judge MUST be a different model than the Strategist. Enforce in CI: the calibrate step rejects a judge-model that equals the strategist-model for any row being scored.
2. Cross-provider judging preferred (e.g. Strategist = Anthropic, Judge = OpenAI) once multi-provider support lands.

## 10. Rollout plan

### Phase 0 — Prerequisite cleanup (0.5 day)
- Land the retrospective's §8.1 (parent code in prompt) and §8.2 (Changelog output block) as a single-LLM MVP. This is the **control** for the A/B test.
- Result: clear baseline number for *"single LLM with parent code + changelog can reach fitness X in Y gens"*.

### Phase 1 — Infrastructure (3 days; was 2 before Extension A was folded in)
- New module `scripts/strategic_directive.py` with the dataclasses and validator.
- Extend `scripts/llm_client.py` with per-role client factory.
- Add `--strategist-*` and `--coder-*` CLI flags to `evolve.py`; keep `--model` as legacy alias.
- Write `prompts/strategist.md` and revise `prompts/evolve_ai.md` per §7.
- Unit tests for `StrategicDirective` (de)serialisation and validator.
- **Extension A (required, from `ARCHITECTURE_PHASE_AND_TACTICS.md` §3):** extend `scripts/telemetry_aar.py` to emit phase-segmented AAR v2 with `by_phase` (opening/midgame/endgame) and `by_balance` (ahead/even/outnumbered) blocks; update `aar.md` renderer; unit tests for short-match degeneracy (A-R1) and fragment merging (A-R2). Without this, the Strategist sees aggregates that hide phase-specific failure, and the dual-LLM reflection loop has no substrate for phase-aware diagnosis.

### Phase 2 — Wiring (1.5 days)
- Modify `evolve.py:_evolve_once` to call strategist, validate, render coder prompt, call coder, parse both responses.
- Replace `_deterministic_journal_entry` invocation path with LLM-authored journal writer that fuses directive + changelog + fitness into the journal entry (schema v2, bump `JOURNAL_SCHEMA_VERSION`).
- Extend `events.jsonl` per R6.
- Extend `MockClient` to support dual-queue responses.

### Phase 3 — Validation (1 day)
- Reproduce-harness update (R8).
- Integration test: full mini Track A with mocked strategist + mocked coder, assert directive.json exists per gen and journal entry shape matches schema v2.
- Safety: test that strategist cannot inject C++ into coder prompt (input sanitisation round-trip).

### Phase 4 — A/B evaluation (2 days compute)
- Run Track A three ways on the same 3 seeds, all with 10M-token budget:
  - **Baseline**: pre-Phase-0 single-LLM (known result: ~100 gens to fitness 1.0 on 2/3 seeds).
  - **MVP**: Phase-0 single-LLM-with-parent-code.
  - **Dual**: Sonnet-strategist + Haiku-coder.
- Compare: generations-to-fitness-0.8, token cost per unit fitness, strategist calibration score, directive vocabulary diversity, M17 judge score variance.
- Ship if Dual shows ≥ 2× fitness-per-generation improvement OR ≥ 1.5× with meaningfully higher vocabulary diversity.

### Phase 5 — Track B/C and multi-provider (later)
- Track B self-play: strategist-fixed vs. coder-LLM first (cheapest), then both-LLM.
- Track C: per-side strategists, enforce R10 isolation. This is where the dual-LLM design's value becomes most visible, because strategic diversity between the two sides is the central phenomenon.
- Add OpenAI and local-Ollama clients per R9.

## 11. Success criteria

The architecture shift is successful iff, in Phase-4 A/B:

1. **Fitness-per-generation** on Track A improves by ≥ 2× (dual vs baseline). I.e., fitness 1.0 reached in ≤ 50 gens on ≥ 2 of 3 seeds.
2. **Directive vocabulary diversity**: ≥ 20 unique `strategy.headline` strings across 50 consecutive gens in any single lineage.
3. **Strategist calibration score** ≥ 0.65 over the lineage (i.e. strategist predicts fitness-delta-sign correctly ≥ 65% of the time).
4. **M17 judge κ** becomes computable (i.e. non-degenerate axis distributions) and ≥ 0.5 against a 50-sample human-labeled subset.
5. **No regression** on M20 reproduce-harness: byte-identical replay under `MockClient` works for both roles.
6. **Per-role failure counters** prevent credit-exhaustion-style cascade failures (retrospective §3, R6).

If criteria 1–2 miss but 4–6 pass, the design is *plumbing-complete* but did not deliver on fitness; investigate strategist model choice (R7) before reverting.

## 12. Explicit non-goals

This document does **not** cover:
- Changes to the physics engine or combat resolution.
- ~~AAR computation changes~~ — **Exception:** Extension A (phase-segmented AAR v2) is a required Phase-1 dependency and is spec'd in `docs/ARCHITECTURE_PHASE_AND_TACTICS.md` §3. The phase-segmentation change to `telemetry_aar.py` lives there, not here.
- Changes to the opponent (pursuit_v1 stays frozen through this shift).
- Any change to fitness definition or statistical gates.
- The reflection-score rubric itself (M17 stays as-is; judge model may change).
- Human-in-the-loop labelling tools (explicitly out of scope — the goal is to *eliminate* the need for them).
- Within-match phase state machines, phase-aware directive schemas, or tactic libraries — these are Extensions B and C, covered in full in `docs/ARCHITECTURE_PHASE_AND_TACTICS.md` and built *on top of* this dual-LLM design.

## 13. Open questions for review

1. **Should the Strategist read only its own directive history, or also the AAR history of all prior generations?** The design above shows only directive+outcome tuples. An alternative is to let the Strategist do temporal reasoning over the full AAR time series. This is more expensive in tokens but potentially much richer in signal. Recommend: start with directive+outcome only; add full AAR history as an ablation experiment in Phase 5.

2. **Should there be a third "Critic" LLM that grades directives before they reach the Coder?** This is overkill for Phase 1 but may become necessary if R1 (hallucinated directives) is severe. Defer to Phase 5 contingent on observed refusal rate.

3. **Open-vocabulary vs. closed-vocabulary `tactic_vocabulary_tags`?** Current M15c design is closed. Dual-LLM design effectively forces open vocabulary (Strategist invents tags). Open: M18 tactic detector must cluster tags post-hoc into stable macro-tactics, not match a fixed dictionary.

4. **What happens on Strategist refusal?** Not yet specified. Proposal: on strategist output "I have no directive to propose" (edge case), orchestrator falls back to the last-known-good directive with `meta.fallback_reason = "strategist_noop"`. Treat repeated strategist refusals symmetrically with coder refusals in R11's anti-stagnation logic.

5. **Cross-model caching of directives for deterministic replay?** The MockClient already replays a queue. For Anthropic-provider replays (not replay-safe today), we'd need to cache `directive.json` contents under a key derived from `(aar_hash, parent_code_sha, strategist_model)` and use it when present. This is useful for tight CI but may mask real regressions.

## 14. Summary for decision-makers

The current loop was designed and shipped as if the generator LLM would reflect strategically on its own. Empirically, it does not — because we never gave it a channel to do so. The single most consequential change this document proposes is **separating the reflection channel from the code-generation channel**, with the secondary change of **specialising models by cognitive role**.

This is not speculative architecture. The retrospective showed the measured defect; this design is the minimum fix that closes it. The risks listed in §9 are real but each has a documented, implementable mitigation, and the Phase-4 A/B test is the honest check: if the dual-LLM design does not beat the single-LLM MVP by a significant margin, we revert the wiring and keep the simpler system. If it does, we have both the mechanical improvement (loop actually learns) and the scientific improvement (interpretable strategy-outcome trajectories) that the project's thesis claims.

### 14.1 What this doc *does not* promise

Reviewers should read this alongside `docs/ARCHITECTURE_PHASE_AND_TACTICS.md`. Key load-bearing distinction:

- **This doc (dual-LLM)**: fixes the cross-generation reflection loop. Output is a Strategist LLM producing typed directives and a Coder LLM implementing them, each generation. That is necessary but *not sufficient* for the project's research thesis of "emergent strategies."
- **Phase-and-tactics doc**: builds on top of this design to deliver (a) state-dependent tactics within a match (Extension B: phase state machine, ABI helpers), and (b) strategic accumulation across matches (Extension C: tactic library, M18-as-promotion-gate). Extension A (phase-segmented AAR) is the prerequisite telemetry and is folded into Phase 1 of *this* rollout.

Concretely: if you land this dual-LLM design and stop there, you get a better sampling loop with interpretable reasoning traces — a significant engineering win but still a monolithic-AI generator. You do *not* yet get a system that can claim "advanced tactics emerge over time." That claim requires Extensions B and C, whose ~12-day incremental cost is justified only if this dual-LLM design has already validated that Strategist-directed planning works at all.

The correct reading order for a reviewer evaluating the full research agenda is:
1. `RETROSPECTIVE_SELF_IMPROVEMENT.md` — why the current loop fails
2. `ARCHITECTURE_SHIFT_DUAL_LLM.md` (this doc) — fix the reflection loop
3. `ARCHITECTURE_PHASE_AND_TACTICS.md` — fix the within-match and multi-generation loops

---
*End of architecture shift document. Review target: engineering + research leads. Paired for review with `ARCHITECTURE_PHASE_AND_TACTICS.md`.*
