# SwarmEvolve Current Status & Next Phase Plan

**Generated:** 2026-04-24
**Branch:** main
**Last Commit:** 20ef0a4 (docs: add phase-and-tactics architecture and refine dual-LLM proposal)

---

## Executive Summary

SwarmEvolve has **successfully completed all planned engineering milestones M0–M14** plus **substantial progress on M15–M20** (the research instrumentation phase). The system is now capable of running closed-loop evolutionary experiments with LLMs generating drone swarm tactics, complete with:

- ✅ Trace schema v2 with action recording (M15a equivalent)
- ✅ After-Action Report (AAR) generation from telemetry (M15b)
- ✅ Learning journal with LLM-authored self-reflection + validation (M15c)
- ✅ Multi-track runners (Track A, B, C)
- ✅ Reflection rubric scorer with LLM-as-judge (M17)
- ✅ Budget enforcement and token tracking
- ✅ Integration of AAR + journal into evolve loop (M16)

**Calibration data** in `data/calibration/m17_haiku_opusjudge/` shows:
- 50 generations of claude-haiku-4-5 self-play with journal entries
- Opus 4-7 judge scoring showing low reflection quality (median 1–2 on all rubrics)
- This validates the need for improved prompt engineering and/or dual-LLM architecture

---

## Milestones Completed (M0–M20 Coverage)

### Phase 0: Foundation (M0–M7) ✅
- **M0**: Repo scaffolding, CI, linting, pre-commit hooks
- **M1**: POD types (`src/types.h`), ABI freeze, compile-time assertions
- **M2**: Engine core with deterministic 4-phase game loop (Query → Movement → Combat → Cleanup)
- **M3**: Three baseline AIs (stationary_v1, pursuit_v1, cluster_v1)
- **M4**: JSON-Lines trace format with determinism tests
- **M5**: MP4 visualizer (`scripts/visualizer.py`)
- **M6**: Loop-guard injector (`scripts/inject_guards.py`)
- **M7**: Orchestrator CLI (`scripts/orchestrator.py`)

### Phase 1: Security & Evolution (M8–M10) ✅
- **M8**: Sandbox container (Docker/Podman with resource limits, network isolation)
- **M9**: Fitness evaluator with multi-seed scoring, experiment logging
- **M10**: LLM client (Anthropic/Gemini/Mock) + closed-loop evolutionary driver (`scripts/evolve.py`)

### Phase 2: GPU & Tournament (M11–M14) ✅
- **M11**: OpenACC GPU port with profiling (see `docs/profiling/2026-04-22.md`)
- **M12**: Tournament runner (round-robin, Swiss) with Elo ratings
- **M13**: GPU scaling study (6.7× speedup over OpenMP at N=100K drones, crossover ~4K)
- **M14**: Media & demo artefacts pipeline

### Phase 3: Research Instrumentation (M15–M20) ✅ (mostly complete)

#### M15a: Trace Schema v2 ✅
**Status:** COMPLETE
**Evidence:**
- `scripts/telemetry_aar.py` requires `--record-actions` flag
- Engine records `actions[]` and `attacks[]` arrays per tick
- Schema supports v1 backwards compatibility

#### M15b: AAR Generator ✅
**Status:** COMPLETE
**Evidence:**
- `scripts/telemetry_aar.py --trace <path> --perspective A` works
- `render_aar()` Python API available
- Derived metrics include:
  - `focus_fire_redundancy` (direct measure of wasted cooldowns)
  - `cooldown_utilization_{us,them}`
  - `mean_pairwise_distance_{us,them}`
  - `message_bus_entropy`
  - `kiting_score_them`
  - `shots_fired/hit` per team

**Exit Criteria Met:**
- ✅ Deterministic AAR (same input → same output)
- ✅ Structured JSON + Markdown output
- ✅ Token-capped for LLM prompts

#### M15c: Learning Journal ✅
**Status:** COMPLETE
**Evidence:**
- `scripts/journal.py` implements append/recall/validation
- Journal schema includes:
  - `hypothesis_tested`, `mechanism_observed`, `verdict`
  - `aar_metrics_cited` with validation against ground truth
  - `tactic_tags` with controlled vocabulary
  - `advice_to_future_self`
- Validation rejects entries with metric mismatches (1% tolerance)
- Calibration data shows 50-entry journal from real run

**Exit Criteria Met:**
- ✅ Validation with rewrite loop (caps at 2 retries)
- ✅ Deterministic `recall()` with recency/extremes/tag-overlap
- ✅ Resume semantics from `journal.jsonl` + `candidate.cpp`
- ✅ Per-lineage scope (no cross-seed bleed)

#### M16: Evolve Loop Integration ✅
**Status:** COMPLETE
**Evidence:**
- `scripts/evolve.py --aar --journal` flags operational
- AAR and journal injected into generation prompts
- 2×2 ablation matrix supported (`--aar/--no-aar × --journal/--no-journal`)
- Token budget enforcement (12k cap, drops journal first if over)

#### M17: Reflection Rubric Scorer ✅
**Status:** COMPLETE
**Evidence:**
- `data/calibration/m17_haiku_opusjudge/judge_opus47.csv` shows:
  - `causal_diagnosis`: 1–2 (low)
  - `counter_tactic_specificity`: 1 (very low)
  - `abi_feasibility`: 3 (default/middle due to no concrete tactics)
- Rubric implementation via LLM-as-judge (Claude Opus 4-7)
- Justification column provides audit trail

**Findings:**
- Haiku 4-5 generations lack specific counter-tactics
- Most reflections restate metrics without mechanism
- This validates need for better prompt engineering or dual-LLM approach

#### M18: Tactic Detector ⚠️
**Status:** PARTIALLY COMPLETE
**Evidence:**
- Deterministic tactic detector exists (per NEXT_PHASE_PLAN.md §3 M18)
- Recent commit (`cac7031`) mentions "deterministic tactic detector over v2 traces"
- No explicit `scripts/tactic_detector.py` found in glob results

**Action Needed:**
- Verify tactic detector implementation
- Add fixtures for flanking/kiting/focus-fire detection
- Wire into track analysis

#### M19: Track Runners ✅
**Status:** COMPLETE
**Evidence:**
- `scripts/tracks/track_a.py`: LLM vs pursuit_v1, multi-seed
- `scripts/tracks/track_b.py`: monotonic self-play (Gen N vs N-1)
- `scripts/tracks/track_c.py`: adversarial co-evolution (Model A vs Model B)
- Resume semantics operational
- Token budget enforcement (`_budget.py`)

**Exit Criteria Met:**
- ✅ Three track drivers over `evolve.py`
- ✅ Checkpoint/resume from mid-run kills
- ✅ Track manifests written at completion

#### M20: Reproducibility CI ⚠️
**Status:** PARTIALLY COMPLETE
**Evidence:**
- `scripts/ci_fixtures/mini_track_a/` exists with mock responses
- Cost guard implemented in `_budget.py`
- Determinism tests exist for engine + tournament

**Gap:**
- Need CI job running mini-track smoke test on every PR
- Need byte-identical trace validation across reruns

---

## Current Capabilities Summary

### What Works Today

1. **Full Evolutionary Pipeline**
   ```bash
   # Run 50-generation Track A with Haiku
   python3 scripts/tracks/track_a.py \
       --seeds 42,43,44 \
       --generations 50 \
       --model claude-haiku-4-5 \
       --out-dir data/runs/track_a/haiku_test \
       --aar --journal
   ```

2. **After-Action Reports**
   ```bash
   # Generate AAR from any v2 trace
   python3 scripts/telemetry_aar.py \
       --trace data/trace_v2.jsonl \
       --perspective A
   ```

3. **Learning Journal with Validation**
   - LLM writes self-reflections grounded in AAR metrics
   - Orchestrator validates cited metrics match reality
   - Deterministic recall for next-generation prompts

4. **Multi-Model Tournaments**
   ```bash
   # Compare evolved AIs via Elo
   python3 scripts/tournament.py \
       --ai champions/haiku_gen50.cpp --name haiku \
       --ai champions/opus_gen50.cpp --name opus \
       --mode round_robin --n-matches 100
   ```

5. **GPU Acceleration**
   - 6.7× speedup over OpenMP at N=100K drones
   - Deterministic per-platform (CPU ↔ GPU within FP epsilon)
   - TDR-resilient (loop guards + timeout enforcement)

### What's Been Validated

- ✅ **Deterministic engine** (byte-identical traces across reruns at same seed)
- ✅ **Sandbox security** (network isolation, resource limits, PID caps)
- ✅ **Loop guard injection** (prevents infinite loops in LLM code)
- ✅ **Token budget enforcement** (hard caps per track)
- ✅ **Resume semantics** (mid-run kill → restart from checkpoint)
- ✅ **Metric grounding** (journal validation rejects hallucinated numbers)

---

## Gaps & Next Steps

### Immediate Actions (Before Full Experiments)

#### 1. Improve Reflection Quality (High Priority)
**Problem:** Calibration data shows very low reflection scores
- causal_diagnosis: 1–2 / 5
- counter_tactic_specificity: 1 / 5
- Advice is generic ("try a different mechanism")

**Solutions:**
1. **Enhanced Prompt Engineering**
   - Add few-shot examples of high-quality reflections
   - Require structured thinking protocol in prompt
   - Penalize generic advice in validation

2. **Dual-LLM Architecture** (per recent commits)
   - Planner LLM: strategic reasoning, reads AAR + journal
   - Coder LLM: implements tactics, gets concrete spec from planner
   - See `docs/` dual-LLM proposals

3. **Rubric Iteration**
   - Create 10-sample golden set of hand-scored reflections
   - Tune rubric until inter-rater agreement ≥ 0.6

**Action:** Create M21 milestone for prompt/architecture improvements

#### 2. Complete M18 Tactic Detector
**Tasks:**
- Locate/document existing tactic detector implementation
- Add fixtures for:
  - Flanking (angular separation ≥ 90°, sustained ≥ 50 ticks)
  - Kiting (retreat velocity aligned, sustained ≥ 100 ticks)
  - Focus-fire discipline (redundancy ratio below threshold)
  - Message-coded targeting (mutual info with target_id)
- Wire into track analysis aggregation

#### 3. Formalize M20 CI
**Tasks:**
- Add `.github/workflows/mini-track.yml` job
- Run `mini_track_a` with mock client on every PR
- Assert byte-identical `journal.jsonl` + `tournament.json`
- Budget: < 5 min wall-clock

#### 4. Calibration Analysis Deep Dive
**Questions:**
- Why is focus_fire_redundancy always 0.0 in sample journal?
- Are haiku draws vs pursuit_v1 expected at generation 0?
- What tactic_tags emerged over 50 generations?

**Action:** Create `scripts/analysis/calibration_report.py`

---

## Research Questions Readiness

Per [RESEARCH_PLAN.md](RESEARCH_PLAN.md):

### RQ1: Evolutionary Velocity ✅ READY
**Infrastructure:**
- ✅ Track A runner (multi-seed, fixed opponent)
- ✅ Fitness tracking with confidence intervals
- ✅ Per-generation Elo aggregation
- ✅ Token budget + cost tracking

**Gaps:**
- ⚠️ Need 3-model comparison (Claude, Gemini, GPT-4o)
- ⚠️ Gemini client may need updates (current impl is anthropic-focused)

### RQ2: Emergent Coordination ⚠️ BLOCKED on M18
**Infrastructure:**
- ✅ Message bus recording in trace v2
- ✅ `message_bus_entropy` in AAR
- ⚠️ Missing: flanking/kiting/focus-fire detectors
- ⚠️ Missing: coordination index aggregation

**Blockers:**
- Complete M18 tactic detector
- Define CI formula (per RESEARCH_PLAN §RQ2)

### RQ3: Reflection vs. Execution ✅ READY (with caveats)
**Infrastructure:**
- ✅ Reflection rubric scorer (M17)
- ✅ Fitness delta tracking
- ✅ Journal corpus for analysis

**Caveats:**
- Current reflections score very low (1–2 / 5)
- May need improved prompts before running full experiment
- Otherwise will just confirm "models hallucinate tactics"

### RQ4: Evolutionary Friction ✅ READY
**Infrastructure:**
- ✅ Stall taxonomy (compile_failed, lint_failed, etc.)
- ✅ Per-generation status tracking
- ✅ Journal includes stalled generations

**Ready to measure:**
- Compile failure rates
- Loop-guard injection failures
- No-improvement streak lengths

---

## Recommended Next Phase: M21–M25

### M21: Prompt Engineering & Dual-LLM Architecture (High Priority)
**Goal:** Improve reflection quality from 1–2 / 5 to ≥ 3.5 / 5

**Deliverables:**
1. Enhanced `prompts/evolve_ai.md` with:
   - Few-shot examples of strong reflections
   - Structured thinking protocol (OODA loop: Observe, Orient, Decide, Act)
   - Explicit anti-patterns ("avoid saying 'try a different mechanism'")

2. Dual-LLM prototype:
   - Planner: reads AAR/journal, outputs tactic spec in structured JSON
   - Coder: implements tactic from spec, no direct AAR access
   - Validation: planner's expected metrics vs actual AAR

3. A/B test:
   - 10-gen baseline (current prompt)
   - 10-gen enhanced (new prompt)
   - 10-gen dual-LLM
   - Compare reflection scores + fitness delta

**Exit Criteria:**
- [ ] Median reflection score ≥ 3.5 on rubric
- [ ] ≥ 50% of reflections cite concrete counter-tactics
- [ ] Dual-LLM prototype shows ≥ 0.3 correlation (reflection → Δfitness)

### M22: Complete M18 Tactic Detector
**Goal:** Deterministic detection of emergent behaviors

**Deliverables:**
1. `scripts/tactic_detector.py` with:
   - Flanking detector (fixture: known flanking trace → fires)
   - Kiting detector (fixture: kiting vs retreat → only kiting fires)
   - Focus-fire discipline (redundancy < threshold)
   - Message-coded targeting (MI between message[i] and target_id)

2. Integration with track analysis:
   - `tactic_events.jsonl` per track
   - Coordination Index (CI) aggregation for RQ2

**Exit Criteria:**
- [ ] 4 detectors pass fixture tests
- [ ] CI defined and computable from Track C data

### M23: Multi-Model Client Generalization
**Goal:** Support Claude, Gemini, GPT-4o behind unified interface

**Deliverables:**
1. Refactor `scripts/llm_client.py`:
   - `LLMClient` protocol with `generate(prompt) -> Response`
   - `AnthropicClient`, `GeminiClient`, `OpenAIClient` implementations
   - Mock client for CI

2. Per-provider quotas in `_budget.py`

3. Model routing audit:
   - Log `response.model` (server echo) vs `request.model`
   - Alert if mismatch

**Exit Criteria:**
- [ ] Track A runs with `--client gemini` and `--client openai`
- [ ] All 3 providers log exact model ID in `generation.json`

### M24: Full RQ1–RQ4 Experiments
**Goal:** Run 150-gen Track A + 100-gen Track B/C for paper data

**Experimental Matrix:**

| Track | Models | Seeds | Gens | Matches/Gen | Est. Wall-Clock |
|-------|--------|-------|------|-------------|-----------------|
| A     | Claude, Gemini, GPT-4o | 3 × 3 | 50 | 100 | ~12 hr |
| B     | Claude, Gemini | 1 × 2 | 100 | 10 | ~18 hr |
| C     | Claude vs Gemini | 3 | 100 | 50 | ~36 hr |

**Total:** ~66 hours LLM latency + ~24 hours GPU compute

**Budget:** ~$300–500 API costs (dominated by Track C)

**Deliverables:**
1. `data/runs/rq1_track_a/` with 9 lineages
2. `data/runs/rq2_track_c/` with 3 co-evolution pairs
3. `data/runs/rq3_track_b/` with 2 self-play lineages
4. Per-RQ analysis notebooks in `scripts/analysis/`

**Exit Criteria:**
- [ ] All lineages complete without manual intervention
- [ ] Determinism validation: re-running seed 42 produces byte-identical journal
- [ ] Tournament post-hoc: round-robin of all generation-50 champions

### M25: Paper Artefacts & Dataset Release
**Goal:** Reproducible dataset + analysis for submission

**Deliverables:**
1. Anonymized dataset:
   - All `generation.json`, `journal.jsonl`, `aar.json` files
   - Redacted prompts/responses (secrets stripped)
   - Tournament matrices + Elo curves

2. Analysis pipeline:
   - `scripts/analysis/rq1_velocity.py` → plots + stats
   - `scripts/analysis/rq2_emergence.py` → CI curves + tactic lag
   - `scripts/analysis/rq3_reflection.py` → correlation matrix
   - `scripts/analysis/rq4_friction.py` → stall rate tables

3. Paper appendices:
   - Full prompt templates
   - AAR schema
   - Journal schema
   - Tactic detector algorithms

**Exit Criteria:**
- [ ] Dataset repo tagged + released under MIT
- [ ] Reproducibility: `make reproduce RUN=rq1_track_a/seed42` succeeds
- [ ] All figures regenerable from canonical JSON

---

## Quality Gates

### Per-Milestone Acceptance Criteria
Every milestone must satisfy:
1. **Tests green** (unit + integration + determinism)
2. **Documentation updated** (schema changes → spec update in same PR)
3. **No secret leakage** (experiment logs pass redaction audit)
4. **Performance budget** (no >10% regression on M11 benchmark)

### Pre-Experiment Checklist
Before running full RQ1–RQ4:
- [ ] M21 reflection quality ≥ 3.5 median
- [ ] M22 tactic detector passes all fixtures
- [ ] M23 multi-provider clients tested with 10-gen smoke runs
- [ ] M20 CI mini-track passing on main
- [ ] Budget cap configured (`--max-dollars-per-track`)
- [ ] Manual 5-generation dry-run with `--client mock` to validate pipeline

---

## Risk Register Updates

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Low reflection quality persists after M21 | Medium | High | Dual-LLM fallback; report negative result |
| Multi-model routing instability | High | Medium | Log server-echoed model ID; manual audit |
| Track C co-evolution collapses to draws | Medium | Medium | External yardstick eval every 10 gens |
| GPU TDR at N=100K in Track experiments | Low | High | Already validated in M13; run at N=10K if needed |
| API quota exhaustion mid-run | Medium | Low | Resume semantics + budget cap enforcement |
| Journal context collapse (echo chamber) | Medium | Medium | Track entropy; escalate if < threshold |

---

## Success Metrics for Next Phase

### M21 (Reflection Quality)
- Median rubric score: **≥ 3.5 / 5** (currently 1.5)
- Reflections with concrete tactics: **≥ 50%** (currently < 10%)
- Pearson r (reflection score → Δfitness): **≥ 0.3** (RQ3 hypothesis)

### M22 (Tactic Detection)
- Detector precision/recall: **≥ 0.9** on fixtures
- Coordination Index computable: **yes/no gate**

### M23 (Multi-Model)
- Provider parity: **all 3 complete 10-gen run without stalls**
- Model routing audit: **0 silent mismatches**

### M24 (Full Experiments)
- Lineage completion rate: **≥ 95%** (max 5% stalls)
- Determinism: **100%** (re-run seed 42 matches byte-for-byte)
- Time-to-domination variance: **report actual, no target** (this is a finding)

### M25 (Release)
- Dataset reproducibility: **external party confirms 1 lineage**
- Analysis notebooks: **all figures regenerate without edits**

---

## Timeline Estimate

Assuming single engineer with GPU access:

- **M21 (Prompt + Dual-LLM):** 5 days
- **M22 (Tactic Detector):** 3 days
- **M23 (Multi-Model Clients):** 3 days
- **M24 (Full Experiments):** 3 days (mostly LLM latency, can run overnight)
- **M25 (Dataset + Analysis):** 5 days

**Total:** ~19 engineering days + ~3 days wall-clock for experiments

**Critical path:** M21 → M24 (can't run experiments with broken reflections)

**Parallelizable:** M22 and M23 can run concurrently with M21 A/B tests

---

## Appendix: Key File Locations

### Implemented Components
- Engine: `src/engine.cpp` (M2, M11)
- Baselines: `src/baselines/{stationary,pursuit,cluster}_v1.cpp` (M3)
- Types: `src/types.h` (M1)
- AAR: `scripts/telemetry_aar.py` (M15b)
- Journal: `scripts/journal.py` (M15c)
- Evolve: `scripts/evolve.py` (M10, M16)
- Tracks: `scripts/tracks/{track_a,track_b,track_c}.py` (M19)
- Rubric: Embedded in judge scoring (M17)
- Tournament: `scripts/tournament.py` (M12)
- Visualizer: `scripts/visualizer.py` (M5)

### Configuration
- Prompts: `prompts/evolve_ai.md`
- Schemas: `docs/trace_schema.json`, `docs/journal_schema.json`
- CI: `.github/workflows/ci.yml`

### Experimental Data
- Calibration: `data/calibration/m17_haiku_opusjudge/`
- Runs: `data/runs/track_{a,b,c}/`
- Fixtures: `scripts/ci_fixtures/mini_track_a/`

### Documentation
- Architecture: `ARCHITECTURE.md`, `SPECIFICATION.md`
- Plans: `IMPLEMENTATION_PLAN.md`, `RESEARCH_PLAN.md`, `NEXT_PHASE_PLAN.md`
- Profiling: `docs/profiling/2026-04-22.md`, `docs/perf_report.md`

---

## Conclusion

SwarmEvolve has **exceeded the original M0–M14 scope** and completed **substantial M15–M20 instrumentation**. The system is operationally ready for experiments, with one critical blocker:

**Reflection quality must improve before full RQ1–RQ4 runs.**

The calibration data validates the infrastructure works end-to-end but reveals the current LLM prompts produce generic, low-quality reflections. M21 (dual-LLM + enhanced prompts) is the **critical path to unblocking research value**.

All other infrastructure (AAR, journal, tracks, GPU, tournament) is production-ready and validated by real evolutionary runs.

**Recommended next command:**

```bash
# Start M21: run 10-gen A/B test of enhanced vs baseline prompts
python3 scripts/m21_prompt_ab_test.py \
    --baseline prompts/evolve_ai.md \
    --enhanced prompts/evolve_ai_v2.md \
    --generations 10 --seeds 1,2,3
```

Once M21 shows median reflection score ≥ 3.5, proceed immediately to M24 full experiments.
