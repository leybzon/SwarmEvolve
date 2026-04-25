# Milestone M21: Reflection Quality Improvement

**Status:** ACTIVE
**Priority:** CRITICAL PATH (blocks full RQ1–RQ4 experiments)
**Owner:** TBD
**Estimated Duration:** 5 engineering days
**Dependencies:** M15b (AAR), M15c (journal), M16 (evolve integration)

---

## Problem Statement

Calibration data (`data/calibration/m17_haiku_opusjudge/`) shows **very low reflection quality**:
- Median `causal_diagnosis`: 1–2 / 5
- Median `counter_tactic_specificity`: 1 / 5
- Median `abi_feasibility`: 3 / 5 (default, no tactics to assess)

**Representative failure mode:**
```json
{
  "advice_to_future_self": "try a different mechanism next generation",
  "hypothesis_tested": "accept-if-better candidate",
  "mechanism_observed": "mean=+0.000 ci=[+0.000,+0.000]",
  "verdict": "partial"
}
```

**Judge justification:**
> "Reflection only restates metrics without explaining why draws occurred;
> advice is 'try a different mechanism', wholly generic."

**Impact:** If full experiments run with these prompts, RQ3 (reflection vs
execution correlation) will simply confirm "models hallucinate tactics they
fail to implement" — a negative but uninformative result.

**Goal:** Improve median reflection score to **≥ 3.5 / 5** on all three
rubrics before starting RQ1–RQ4 data collection.

---

## Hypothesis

Low reflection quality stems from three causes:
1. **Prompt does not explicitly require structured thinking** — current
   `prompts/evolve_ai.md` says "return one cpp block" with no tactical
   reasoning scaffold.
2. **No few-shot examples** — LLM has no reference for what "good" looks like.
3. **No enforcement** — journal validation checks metric citations but not
   reasoning depth; LLM can write generic prose and pass validation.

---

## Approach

Three parallel interventions, A/B tested:

### Intervention A: Enhanced Prompt (Structured Thinking)

**Deliverable:** `prompts/evolve_ai_v2_structured.md` ✅ (CREATED)

**Key additions:**
1. **OODA Loop Protocol** (Observe, Orient, Decide, Act)
   - OBSERVE: extract 5–8 metrics from AAR
   - ORIENT: answer 3 diagnostic questions with causal links
   - DECIDE: state ONE concrete counter-tactic
   - ACT: predict which metrics will change

2. **Anti-patterns list** with examples of score-1 responses:
   - "Try a different mechanism" ❌
   - "Carry forward" ❌
   - "Improve target selection" (too generic) ❌

3. **Two few-shot examples**:
   - High-quality (5/5/5): specific metrics → causal diagnosis → measurable tactic
   - Low-quality (1/1/3): restates outcome, no mechanism, generic advice

4. **Explicit rubric scoring criteria** shown in prompt

**Hypothesis:** Forcing structured output will eliminate generic reflections.

### Intervention B: Dual-LLM Architecture (Planner + Coder)

**Deliverable:** `scripts/evolve_dual_llm.py` (NEW)

**Design:**

```
┌──────────────────────────────────────────────────────┐
│ Generation N Prompt                                  │
│  - AAR (from M15b)                                   │
│  - Journal (from M15c)                               │
│  - Opponent source                                   │
└────────────┬─────────────────────────────────────────┘
             │
             ▼
      ┌────────────────┐
      │ Planner LLM    │  (e.g., Claude Opus 4-7)
      │                │  Prompt: "Analyze AAR, diagnose failure,
      │                │           propose ONE tactic as structured JSON"
      └────────┬───────┘
               │
               │ Output: TacticSpec JSON
               │  {
               │    "diagnosis": "...",
               │    "counter_tactic": "...",
               │    "target_metrics": {"cooldown_util": 0.6, ...},
               │    "abi_constraints": "message[0]=target_id, memory[0..9]=cooldown_est"
               │  }
               ▼
         ┌──────────────┐
         │ Validation   │  Check:
         │              │  - target_metrics are valid AAR keys
         │              │  - abi_constraints parse correctly
         └──────┬───────┘
                │ (reject + retry if invalid)
                ▼
         ┌──────────────────┐
         │ Coder LLM        │  (e.g., Claude Sonnet 4-5 or Haiku 4-5)
         │                  │  Prompt: "Implement this tactic spec in C++.
         │                  │           No AAR, no journal — just the spec."
         │                  │  Input: TacticSpec + opponent source + ABI
         └──────┬───────────┘
                │
                ▼ Output: candidate.cpp
         ┌────────────────────┐
         │ Compile + Evaluate │
         │ (existing pipeline)│
         └──────┬─────────────┘
                │
                ▼
         ┌────────────────────────┐
         │ AAR (next generation)  │
         │ + Journal Entry        │
         │                        │
         │ Now planner sees:      │
         │  - Its own TacticSpec  │
         │  - Actual AAR metrics  │
         │  - Gap analysis        │
         └────────────────────────┘
```

**Key insight:** Separating reasoning (planner) from coding (coder) prevents
the coder from "explaining away" failures in prose. The planner is **forced
to confront** AAR metrics vs its predictions.

**Validation step:** Planner's `target_metrics` are compared to actual AAR
in the next journal entry. If `|predicted - actual| > 0.2` for any metric,
the planner sees a "prediction_error" field.

### Intervention C: Journal Validation Enhancement

**Deliverable:** Update `scripts/journal.py` validation

**New checks:**
1. **Reasoning depth heuristic:**
   - `hypothesis_tested` must be > 10 words
   - `mechanism_observed` must cite ≥ 1 AAR metric by name
   - `advice_to_future_self` cannot match banned phrases:
     - "try a different mechanism"
     - "carry forward" (unless win rate ≥ 0.8)
     - "improve [X]" without specificity

2. **Tactic tag enforcement:**
   - Reject entries with only generic tags (`["accept_if_better"]`)
   - Require ≥ 2 tags, at least one domain-specific
   - Controlled vocab: `tight_formation`, `loose_formation`, `kiting`,
     `flanking`, `message_coordination`, `cooldown_tracking`, etc.

3. **Reflection scoring gate:**
   - After LLM writes journal entry, run it through the M17 rubric scorer
   - If median score < 3.0, append a "reflection_feedback" field with the
     judge's justification and **force a rewrite**
   - Cap at 2 retries (same as metric validation)

**Hypothesis:** Closing the validation loop on reasoning quality (not just
metric accuracy) will train the LLM to write better reflections over time.

---

## Experimental Design: A/B/C Test

Run **30 generations** across **3 conditions × 3 seeds = 9 lineages**:

| Condition | Prompt | LLM Architecture | Journal Validation |
|-----------|--------|------------------|-------------------|
| Baseline  | `evolve_ai.md` (current) | Single LLM (Haiku 4-5) | Metric-only |
| A | `evolve_ai_v2_structured.md` | Single LLM (Haiku 4-5) | Metric-only |
| B | `evolve_ai.md` | Dual (Opus planner + Haiku coder) | Metric-only |
| C | `evolve_ai_v2_structured.md` | Single LLM (Haiku 4-5) | **Enhanced** |

**Evaluation:**
1. **Reflection quality** (M17 rubric scorer on all journal entries)
2. **Fitness delta** (mean improvement per generation)
3. **Correlation** (reflection score vs Δfitness)

**Fixed variables:**
- Opponent: `pursuit_v1` (Track A setup)
- Seeds: 1, 2, 3
- Generations: 30 per lineage
- Matches per generation: 10 (reduced from 100 to save budget)

**Duration:** ~6 hours LLM latency (9 lineages × 30 gens × ~1 min/gen)

**Cost:** ~$30–50 (dominated by Opus calls in Condition B)

---

## Deliverables

### D1: Enhanced Prompt ✅
**File:** `prompts/evolve_ai_v2_structured.md`
**Status:** CREATED
**Changes:**
- OODA loop structure
- Few-shot examples (score-5 and score-1)
- Anti-pattern list
- Rubric criteria embedded

### D2: Dual-LLM Driver
**File:** `scripts/evolve_dual_llm.py`
**Status:** PENDING
**API:**
```python
def evolve_dual_llm(
    opponent: Path,
    planner_client: LLMClient,
    coder_client: LLMClient,
    generations: int,
    **kwargs
) -> ExperimentResult
```

**Components:**
1. `prompts/planner_analyze_aar.md` — tactical analysis prompt
2. `prompts/coder_implement_tactic.md` — implementation prompt
3. `TacticSpec` schema validation
4. Prediction error tracking in journal

### D3: Enhanced Journal Validation
**File:** `scripts/journal.py` (update)
**Status:** PENDING
**Changes:**
- `_validate_reasoning_depth()` heuristic
- Banned phrase detection
- Tactic tag enforcement
- Optional rubric scoring gate (behind `--strict-reflection` flag)

### D4: A/B/C Test Runner
**File:** `scripts/m21_ab_test.py`
**Status:** PENDING
**API:**
```bash
python3 scripts/m21_ab_test.py \
    --conditions baseline,A,B,C \
    --seeds 1,2,3 \
    --generations 30 \
    --out-dir data/runs/m21_ab_test
```

**Outputs:**
- `data/runs/m21_ab_test/{baseline,A,B,C}/seed{1,2,3}/` (9 lineages)
- `m21_results.csv` (reflection scores + fitness by condition)
- `m21_report.md` (automatic analysis)

### D5: Analysis Report
**File:** `scripts/analysis/m21_report.py`
**Status:** PENDING
**Generates:**
1. Box plots: reflection score by condition
2. Scatter: reflection score vs Δfitness
3. Statistical tests: Kruskal-Wallis (conditions differ?)
4. Recommendation: which condition to use for RQ1–RQ4

---

## Acceptance Criteria

M21 is **complete** when:

- [ ] **D1**: Enhanced prompt committed ✅
- [ ] **D2**: Dual-LLM driver passes 3-generation smoke test
- [ ] **D3**: Enhanced validation rejects 3/3 low-quality fixture entries
- [ ] **D4**: A/B/C test completes all 9 lineages without manual intervention
- [ ] **D5**: Analysis report shows **at least one condition achieves median
      reflection score ≥ 3.5 / 5** on all three rubrics
- [ ] **Gate decision:** If all conditions fail ≥ 3.5 threshold, escalate to
      owner for fallback plan (options: switch model, loosen threshold,
      accept negative RQ3 result)

**Performance gate:** Dual-LLM condition (B) may not regress fitness vs
baseline by > 0.2 mean score over 30 generations. If it does, dual-LLM is
considered "too expensive" and dropped from consideration.

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Enhanced prompt confuses LLM, lowers fitness | Medium | High | A/B test detects this; revert if fitness drops |
| Dual-LLM doubles cost, no quality gain | Medium | Medium | Cap planner budget; compare cost/quality ROI |
| Rubric scorer itself is unreliable | Low | High | Calibrate on 10-sample golden set first |
| All conditions fail ≥ 3.5 threshold | Medium | High | Fallback: run RQ1–RQ4 anyway, report low reflection as finding |
| Journal validation too strict, causes stalls | Low | Medium | Make strict mode opt-in via flag |

---

## Timeline (5 Days)

| Day | Work | Deliverable |
|-----|------|-------------|
| 1 | Implement dual-LLM driver (D2) | `evolve_dual_llm.py` smoke-tested |
| 2 | Enhance journal validation (D3) | `journal.py` updated, tests pass |
| 3 | Build A/B/C test harness (D4) | `m21_ab_test.py` runs 1 lineage |
| 4 | **Run full A/B/C test** (overnight) | 9 lineages × 30 gens complete |
| 5 | Analysis + report (D5), decision | `m21_report.md` committed |

**Critical path:** D2 and D3 can run in parallel (days 1–2). D4 depends on
both. Analysis (day 5) is mostly automated.

---

## Success Metrics

### Primary (Gate)
- **Median reflection score ≥ 3.5 / 5** in at least one condition
- **Reflection → Δfitness correlation ≥ 0.3** (Pearson r)

### Secondary
- Fraction of reflections with concrete counter-tactics: ≥ 50% (currently < 10%)
- Tactic tag diversity: ≥ 8 unique tags per 30-generation lineage
- Journal validation rejection rate: < 20% (avoid stall loops)

### Efficiency
- Dual-LLM cost per generation: < 2× single-LLM
- A/B/C test wall-clock: < 8 hours (target 6 hours)

---

## Fallback Plan (If M21 Fails)

If no condition achieves ≥ 3.5 median after the A/B/C test:

**Option 1: Lower Bar (Conservative)**
- Proceed to RQ1–RQ4 with best-performing condition
- Acknowledge low reflection quality in paper
- Frame RQ3 as "negative result: models do not learn from self-reflection
  under these prompts"

**Option 2: Model Swap (Aggressive)**
- Retry A/B/C with Opus 4-7 as coder (not just planner)
- Budget hit: ~3× cost increase
- Only viable if Opus shows ≥ 4.0 median in initial 5-gen smoke test

**Option 3: Human-in-Loop (Research Pivot)**
- Add manual reflection curation: human rewrites low-scoring reflections
- Defeats automation goal but salvages RQ3
- Requires ≤ 5 min/generation, feasible for Track A only

**Decision point:** End of day 5. Owner chooses fallback or approves
winning condition for RQ1–RQ4.

---

## Links

- [CURRENT_STATUS.md](CURRENT_STATUS.md) — context for this milestone
- [RESEARCH_PLAN.md](../RESEARCH_PLAN.md) — RQ3 hypothesis
- [NEXT_PHASE_PLAN.md](NEXT_PHASE_PLAN.md) — original M15–M20 roadmap
- Calibration data: `data/calibration/m17_haiku_opusjudge/`
- Enhanced prompt: `prompts/evolve_ai_v2_structured.md` ✅

---

## Next Steps

1. **Immediate:** Implement D2 (dual-LLM driver)
   ```bash
   # Start here:
   touch scripts/evolve_dual_llm.py
   touch prompts/planner_analyze_aar.md
   touch prompts/coder_implement_tactic.md
   ```

2. **Day 1 goal:** 3-generation smoke test of dual-LLM passes
   ```bash
   python3 scripts/evolve_dual_llm.py \
       --planner-model claude-opus-4-7 \
       --coder-model claude-haiku-4-5 \
       --opponent src/baselines/pursuit_v1.cpp \
       --generations 3 --seed 99 \
       --out-dir data/runs/m21_dual_smoke
   ```

3. **Day 2:** Update `journal.py` validation, run fixture tests

4. **Day 3:** Wire A/B/C harness, test with 1-gen dry-run

5. **Day 4 AM:** Launch full 9-lineage × 30-gen run (overnight)

6. **Day 5:** Analyze, decide, document in `m21_report.md`
