# Retrospective: Why the Self-Improvement Loop Is Not Actually Self-Improving

**Author:** SwarmEvolve project retrospective, post Track A (claude-haiku-4-5, 3 seeds, 403 gens total)
**Scope:** Audit of the spec-vs-implementation gap in the strategy-learning pipeline
**Verdict:** The loop is currently a **random-restart search** wearing a self-reflection costume. Strategy and tactical knowledge are not learned, not accumulated, and not used in the next generation's prompt in any semantically meaningful way.

---

## 1. The stated goal

From the project brief and `docs/NEXT_PHASE_PLAN.md` §M15c:

> *"The journal answers what have I learned across my entire lineage — which tactics have tended to work, which mechanisms have repeatedly failed against this opponent… The LLM authors journal entries but the orchestrator validates them against the AAR before accepting."*

The vision: no human judge, no scripted heuristic; **objective fitness scores drive LLM-authored strategic reflection, which feeds the next generation's prompt, which produces incrementally better tactics**. Self-improvement without human-in-the-loop.

## 2. What actually happens

### 2.1 The observed Track A results

| Seed | Gens | Champion gen | Champion fitness | Shape of curve |
|---|---|---|---|---|
| 42 | 150 | 91 | 1.00 | 3 step-ups (0 → 0.1 → 0.2 → 1.0), long flat plateaus |
| 43 | 150 | 106 | 1.00 | Similar step pattern |
| 44 | 103 (exhausted) | 36 | 0.80 | One lucky jump at gen 36, then decay + credit-exhaustion |

### 2.2 The mechanism of "improvement"

Inspecting `seed42/gens/{0000..0019}/candidate.cpp`, consecutive generations share only **11–36% of source lines**. Each generation is not an edit of the previous — it is a **from-scratch rewrite** by the LLM. The 150-generation loop is functionally equivalent to drawing 150 i.i.d. samples from Haiku's conditional distribution over "C++ drone AI that beats pursuit_v1", and keeping the argmax.

That is why the fitness curve is a step function with long flat stretches: the loop is waiting for a *lucky sample*, not climbing a gradient.

## 3. The five specific places the spec is violated

### 3.1 Journal entries are rule-based, not LLM-authored

`scripts/evolve.py:506` — function `_deterministic_journal_entry()`. Every journal row is a template fill:

```python
hypothesis = "accept-if-better candidate; measure combat metrics and compare to prior champion"
advice = "carry forward" if summary.accepted else "try a different mechanism next generation"
```

Empirical confirmation across all three seeds:

| Field | Unique values across 398 entries |
|---|---|
| `hypothesis_tested` | **1** (literally one string, verbatim) |
| `advice_to_future_self` | **2** ("carry forward", "try a different mechanism next generation") |
| `tactic_tags` | **3** distinct sets, all drawn from 4 tag options |
| `mechanism_expected` | **1** ("mean score improves over champion") |

The author comment at `evolve.py:518` is explicit:
> *"This is the M16 baseline writer: deterministic, never hallucinates… **The M17 LLM-authored writer will replace this path for Track-C.**"*

**That replacement was never implemented.** The "LLM-authored" path in §M15c is an empty slot. M17 shipped a *judge* (for scoring reflections) but not the *author* (the thing that would produce reflections worth scoring).

### 3.2 The LLM is never asked to reflect

`prompts/evolve_ai.md` is the only prompt the generator LLM ever sees. Its response format instruction is:

> *"Return one fenced ```cpp``` block containing the entire file. No prose outside the block."*

The prompt **explicitly forbids the LLM from emitting any reflection text**. It cannot diagnose its own prior failure because it is not allowed to. The `{PRIOR_LESSONS}` and `{AAR}` blocks are *input* context; there is no matching *output* channel for the model to synthesize new lessons from them.

### 3.3 The previous champion's code is not shown to the LLM

Scan of the full 18,123-character prompt.md at seed42/gen0091 (the generation that produced the 1.0 champion):

| Searched for | Found? |
|---|---|
| "previous champion" | ❌ |
| "prior code" | ❌ |
| "parent code" | ❌ |
| Previous-gen `drone_ai` source | ❌ |

The LLM is handed:
- the *opponent's* source (pursuit_v1.cpp, frozen),
- the ABI headers,
- the AAR of the previous generation (combat metrics),
- the rule-based journal lines (which all say the same thing),

…but **not its own previous attempt**. It cannot do "change lines 45–60 of what I wrote last time" because it does not have last time. Every generation starts from a blank file.

This alone guarantees the fitness curve will be a step function over i.i.d. samples. There is no mechanism by which a local improvement can be preserved and built upon.

### 3.4 The AAR is injected but there is no prompt scaffolding for using it

The prompt has a section `# After-Action Report (last generation)` with the previous AAR markdown inlined. But there is no instruction like *"propose one concrete change to address the highest-leverage metric in the AAR above, and explain it in a comment at the top of your file"*. The AAR is a wall of numbers the model may or may not condition on.

Testable hypothesis: if you deleted the `{AAR}` and `{PRIOR_LESSONS}` substitution entirely and re-ran Track A, the results would be within sampling noise of the current run. The journal is not doing causal work.

### 3.5 The `tactic_tags` vocabulary is closed and trivial

Rule-based tagging in `_deterministic_journal_entry` lines 551–564:

```python
tags_set: list[str] = ["accept_if_better"]
if focus_fire_redundancy > 0.3: tags_set.append("focus_fire")
if cooldown_utilization < 0.3: tags_set.append("low_cooldown_uptime")
if mean_pairwise_distance < 40: tags_set.append("tight_formation")
elif mean_pairwise_distance > 120: tags_set.append("loose_formation")
```

Four tags, all derived from three AAR metrics, bucketed by fixed thresholds. Across 398 entries only 3 unique tag-sets were ever produced. The retrieval layer (journal.render_for_prompt) does "tag overlap" as a recall signal — but if the tag vocabulary has three distinct values, the recall is trivially a "show the most recent three entries" degenerate.

## 4. Why M17 (the reflection-quality judge) made this worse, not better

M17 added a calibration harness for a *judge* that scores reflection quality on three axes. When we ran Opus-4 as judge over 50 samples of the existing deterministic reflections, it returned flat scores:

- `causal_diagnosis`: 39×1, 11×2
- `counter_tactic_specificity`: 50×1 (constant)
- `abi_feasibility`: 50×3 (constant)

The judge is correctly telling us "these reflections have no counter-tactic and no causal diagnosis". But **the judge is measuring text that was mechanically templated**, not text that the LLM produced. It is rating the orchestrator's rubber-stamp string. M17's Cohen's-κ gate of ≥0.5 is now unreachable-in-principle: the judge can never show variance because the inputs have no variance.

The M17 effort — ~400 LOC of scoring infrastructure, a 50-sample calibration set, an Opus judge at ~$1.66/50-rows — is **measuring a signal that does not exist**.

## 5. The root-cause cascade

```
The generator LLM has no output channel for reflection
    └─▶ The orchestrator must synthesize reflections itself
         └─▶ It uses a deterministic template (M15c "baseline writer")
              └─▶ All 398 entries become near-identical
                   ├─▶ retrieval is a no-op (nothing distinguishes entries)
                   ├─▶ the next-gen prompt is effectively static
                   │   └─▶ generator draws i.i.d. samples every generation
                   │        └─▶ fitness curve is a step function, not a climb
                   └─▶ M17's judge correctly rates text as flat
                        └─▶ κ-calibration gate is unreachable
                             └─▶ M17 can never unblock M18+
```

Every downstream milestone inherits the same defect: there is no signal entering the system past "did this one shot beat the previous best?". The intricate M15b AAR, the M15c journal schema, the M17 rubric and judge, and the M18 tactic detector are all reading and writing structured data that *describes* a self-improvement process the loop is not actually performing.

## 6. What the champions actually learned (nothing transferable)

Inspection of the gen-91 AAR that produced seed42's fitness-1.0 champion:
- `Outcome: TEAM_A_WIN after 38 ticks`
- `Survivors: us=9 | them=0`
- `Hit rate: us=100% | them=100%`
- `Cooldown utilization: us=79.26% | them=100%`

The fitness = 1.0 "champion" is not a champion in any learned-tactic sense. It's the first random sample that happened to produce a working pursuit-plus-focus-fire that dominates pursuit_v1. It reflects Haiku's prior knowledge of drone-combat patterns, not anything the loop taught it. On seed44 (which exhausted at gen 103), Haiku still hit fitness 0.8 on gen 36 — one lucky early draw, then 67 more unsuccessful rewrites.

**The fitness data from all three seeds is consistent with: "draw N samples from Haiku, keep the best."** The evolutionary structure adds nothing to this.

## 7. Impact on the project's thesis

The project's stated thesis is that LLM-driven evolution can produce *self-improving* software without human evaluation. On the evidence of this run, that thesis is **not supported by the current implementation**, because the current implementation does not exercise any self-improvement loop — only a sampling loop.

What we have actually demonstrated is something narrower and still interesting:
> *"A mid-tier LLM, given the ABI and a frozen baseline opponent, can produce a winning drone AI within ~100 cold-sampled attempts."*

That is a real result, but it is a single-shot capability benchmark, not evidence of evolutionary self-improvement.

## 8. What has to change for the loop to actually learn

The fix is a four-part minimum, ordered by leverage:

### 8.1 Show the parent code to the generator (highest leverage)
Inject `{PARENT_CODE}` into `prompts/evolve_ai.md` with the previous champion's full `candidate.cpp`. Add an explicit instruction:
> *"Here is your last attempt. Produce a modified version that addresses the concrete weakness identified in the AAR below. Do not start from scratch unless the previous code is fundamentally misaligned with the opponent's behavior."*

This single change converts random-restart search into local-neighborhood search. Expected effect: fewer flat plateaus, more incremental fitness deltas, champions reached in fewer generations.

### 8.2 Give the LLM an output channel for reflection
Extend the response format:
```
## Changelog (brief, required)
- diagnosed: <one sentence about the AAR>
- changed: <one sentence about what's different from parent>
- hypothesis: <what metric should move and why>

```cpp
<code>
```
```

Parse both blocks. The changelog block becomes the journal entry's LLM-authored content. The orchestrator still validates that `aar_metrics_cited` keys map to real AAR values, per §M15c spec — that's the grounding guard.

### 8.3 Delete or replace `_deterministic_journal_entry`
It is actively harmful. It writes identical-looking rows that pollute the retrieval layer. At minimum, skip writing it when `journal_enabled` is true but the LLM did not emit a reflection. Better: remove the code path entirely and hard-fail if the LLM's response lacks the reflection block.

### 8.4 Retire M17's κ gate until 8.1–8.3 ship
With templated reflections, M17 is measuring noise. Park the Opus-judge scoring harness until there is semantically non-trivial reflection text to score. The harness itself is reusable — just don't gate on it in the current state.

## 9. What has to change in the docs

- `docs/NEXT_PHASE_PLAN.md` §M17 should explicitly list "§3.X.2 LLM-authored writer" as a **hard prerequisite** for the judge to be meaningful. The current doc ordering implies they are independent; they are not.
- The M15c "baseline writer" language in `evolve.py:518` has become load-bearing by accident. Rename it `_sentinel_journal_entry` and limit its invocation to a fallback path (e.g. the LLM refused or timed out), not the default.
- Add an integration test that asserts **non-trivial diversity** of `advice_to_future_self` and `hypothesis_tested` fields across a lineage — e.g. "≥ 10 unique hypothesis strings in any 50-gen window". This would have caught the current degeneracy on day one.

## 10. What this retrospective does not answer

- Whether Haiku-4.5 is the right model for the *reflecting* role. (Opus/Sonnet may write richer reflections; a 2-model split, small author + big reflector, is the obvious experiment once 8.2 ships.)
- Whether 150 generations is enough for iterative climb to beat cold-sampling once parent-code-injection is on. (Expect yes, but this is empirical.)
- Whether Track B (self-play) will surface the same defect. (It will. Self-play without parent-code injection is *two* cold-sample streams playing each other; nothing compounds.)
- Whether the AAR's metric choice (cooldown util, pairwise distance, focus-fire redundancy) is actually the right basis for strategic reflection. (TBD; at least one round of LLM-authored reflections is needed to observe which metrics the model actually engages with.)

## 11. Summary for review

The SwarmEvolve loop, as of the claude-haiku-4-5 Track A run, is a **random-sampling search dressed in evolutionary and reflective telemetry**. The telemetry itself is well-built: the AAR, journal schema, tactic detector, and reproducibility harness are all functioning and testable. But the active path that would make them *useful* — the LLM-authored reflection writing into the LLM-conditioned next-generation prompt that includes parent code — **was never closed**. M16 shipped the plumbing with a deterministic placeholder. M17 built a quality gate for text the plumbing never filled in.

The fix is mechanical and small: §8.1–8.3 together are probably a 2-day change. Until it lands, the project should not claim self-improvement in results: the 1.0 champions in Track A are evidence of Haiku's prior knowledge, not of the loop teaching Haiku anything.

---
*End of retrospective.*
