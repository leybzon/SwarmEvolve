# M22: RQ1 - Emergent Complexity Experiment

**Research Question 1:** Can LLM-guided evolution discover tactics beyond baseline behavior?

**Milestone Goal:** Run 100-generation dual-LLM experiment to observe emergent tactical patterns and validate reflection-driven improvement.

**Status:** READY TO START (M21 complete, infrastructure validated)
**Estimated Duration:** 2 days (1 day compute + 1 day analysis)
**Estimated Cost:** $20 (100 gens × $0.20/gen dual-LLM)

---

## Motivation

M21 validated that dual-LLM achieves 5/5 reflection quality. Now we need to test whether **high-quality reflections translate to tactical innovation** over longer timescales.

**Core hypothesis:** With proper reflection infrastructure, LLMs will:
1. Identify weaknesses in pursuit_v1 (greedy nearest-enemy targeting)
2. Discover counter-tactics (kiting, baiting, flanking, claim-based coordination)
3. Iterate and refine tactics based on AAR feedback
4. Show fitness improvement over 100 generations

**Success criteria:**
- Observe ≥2 distinct tactical patterns in journal entries
- Fitness improvement trend (linear regression slope > 0)
- Reflection quality maintains ≥3.5/5 throughout
- Tactical diversity increases over time (measured by unique tactic_tags)

---

## Experimental Design

### Configuration

**Single long run:** 1 seed, 100 generations (vs. M21's 2 seeds × 30 gens)

**Rationale:**
- Longer horizon reveals emergent patterns (30 gens too short for iteration)
- Single seed reduces cost while still validating RQ1
- Can run 3 seeds later if needed for statistical significance

**Parameters:**
```bash
python3 scripts/evolve_dual.py \
  --opponent src/baselines/pursuit_v1.cpp \
  --as-team A \
  --planner-model claude-opus-4-7 \
  --coder-model claude-haiku-4-5 \
  --generations 100 \
  --n-matches 10 \
  --seed 42 \
  --out-dir data/runs/m22_rq1_100gen \
  --strict-reflection \
  -v
```

**Estimated resources:**
- **Wall time:** ~50 hours (100 gens × 30 min/gen)
- **API cost:** ~$20 (100 gens × $0.20/gen)
- **Disk:** ~10 GB (100 gens × ~100 MB/gen for code + traces)

### Opponent

**pursuit_v1.cpp** - Greedy nearest-enemy baseline

**Known weaknesses:**
- Zero kiting (always charges, never retreats)
- No coordination (focus-fire redundancy)
- Deterministic (exploitable patterns)
- Corners allies against arena edges

**Why this opponent:**
- Simple enough for LLM to analyze
- Exploitable enough to allow innovation
- Frozen baseline (no moving target)
- Already used in M21 calibration

### Success Metrics

**Primary:**
1. **Tactical diversity:** Count unique values in `tactic_tags` over 100 gens
   - Baseline: ~1 tag ("accept_if_better")
   - Target: ≥5 distinct tags (e.g., "kiting", "baiting", "claim_coordination", "flanking", "edge_avoidance")

2. **Fitness trend:** Linear regression of `fitness` vs. `generation`
   - Baseline: slope ≈ 0 (no learning)
   - Target: slope > 0.005 (5% improvement over 100 gens)

**Secondary:**
3. **Reflection quality:** Median reflection score (manual or M17 automated)
   - Target: ≥3.5/5 maintained throughout

4. **AAR-grounded mechanisms:** Fraction of journal entries citing ≥2 AAR metrics
   - Baseline: ~0% (generic advice)
   - Target: ≥80% (data-driven reasoning)

5. **Tactical iteration:** Evidence of refinement (e.g., "v1 → v2" in hypothesis_tested)
   - Count entries with "refine", "improve", "version", "iteration" keywords
   - Target: ≥10% of entries show iteration

---

## Analysis Plan

### Phase 1: Automated Metrics (Day 1 - During Run)

**During the 50-hour run, monitor:**
- Fitness progression (plot every 10 gens)
- Tactic tag accumulation (unique count over time)
- Compilation success rate (should be 100%)
- API retry rate (should be <5%)

**Tools:**
```bash
# Monitor progress
tail -f data/runs/m22_rq1_100gen/journal.jsonl | jq '.generation, .fitness, .tactic_tags[0]'

# Plot fitness (every 10 gens)
python3 scripts/analysis.py plot-fitness data/runs/m22_rq1_100gen/journal.jsonl
```

### Phase 2: Qualitative Analysis (Day 2 - Post-Run)

**Manual review of journal entries:**
1. Sample 10 entries (gens 0, 10, 20, ..., 90)
2. Score against M17 rubric (causal diagnosis, tactic specificity, ABI feasibility)
3. Identify emergent patterns:
   - Kiting vs. aggression
   - Claim-based coordination vs. greedy targeting
   - Edge-avoidance vs. centering
   - Message protocols (what's in message_out[0..3]?)

**Tactical timeline:**
- Gen 0-20: Discovery phase (exploring mechanisms)
- Gen 20-50: Iteration phase (refining tactics)
- Gen 50-100: Optimization phase (parameter tuning)

**Expected outcomes:**
- **Best case:** Clear progression from "naive pursuit" → "claim coordination" → "kiting" → "bait-and-flank"
- **Worst case:** Stuck in local optimum (e.g., always tries claim coordination, never tries kiting)
- **Realistic:** 2-3 major tactical innovations, with refinement cycles

### Phase 3: Quantitative Analysis (Day 2 - Post-Run)

**Statistical tests:**
1. **Fitness trend:** Linear regression
   ```python
   import numpy as np
   from scipy import stats

   gens = np.array([entry['generation'] for entry in journal])
   fitness = np.array([entry['fitness'] for entry in journal])
   slope, intercept, r_value, p_value, std_err = stats.linregress(gens, fitness)

   print(f"Slope: {slope:.4f} (p={p_value:.4f})")
   # Significant if p < 0.05 and slope > 0
   ```

2. **Tactical diversity:** Shannon entropy of tactic_tags
   ```python
   from collections import Counter
   import math

   tags = [tag for entry in journal for tag in entry['tactic_tags']]
   counts = Counter(tags)
   total = sum(counts.values())
   entropy = -sum((count/total) * math.log2(count/total) for count in counts.values())

   print(f"Tag entropy: {entropy:.2f} bits")
   # Higher = more diverse tactics
   ```

3. **AAR metric grounding:** Fraction citing ≥2 metrics
   ```python
   grounded = sum(1 for e in journal if len(e.get('aar_metrics_cited', {})) >= 2)
   print(f"Grounded reflections: {grounded}/{len(journal)} ({100*grounded/len(journal):.1f}%)")
   ```

---

## Exit Criteria

**M22 succeeds if:**

1. ✅ **100 generations complete** (no stalls, no crashes)
2. ✅ **Fitness improvement:** Linear regression slope > 0 with p < 0.05
3. ✅ **Tactical diversity:** ≥5 unique tactic_tags observed
4. ✅ **Reflection quality:** Median score ≥3.5/5 (manual or automated)
5. ✅ **Evidence of iteration:** ≥10 journal entries mention refinement/improvement

**Partial success if:**
- 4/5 criteria met (e.g., no fitness improvement but high tactical diversity)
- Can proceed to RQ2-RQ4 with caveats

**Failure if:**
- <80 generations complete (infrastructure issue)
- Fitness regression (slope < -0.01)
- Reflection quality degrades (<3.0/5 median)

---

## Risks & Mitigations

### Risk 1: Local Optimum (No Innovation)

**Symptom:** LLM discovers claim-based coordination in gen 5, never tries anything else

**Likelihood:** Medium (LLMs can get stuck in "first working idea")

**Mitigation:**
- Enhanced prompt includes anti-pattern list ("don't always refine the same tactic")
- Journal recall includes diversity bonus (recall entries with dissimilar tags)
- If detected at gen 50, could inject "try a fundamentally different approach" constraint

### Risk 2: API Rate Limits

**Symptom:** Test stalls after 40 gens due to rate limit errors

**Likelihood:** Low (Opus has 2000 req/min limit, we're doing ~2 req/30min)

**Mitigation:**
- Built-in retry with exponential backoff
- Checkpoint every 10 gens (can resume from journal.jsonl)
- Monitor rate limit headers during run

### Risk 3: Reflection Quality Degrades

**Symptom:** Gen 0-20 scored 5/5, gen 80-100 scored 2/5

**Likelihood:** Low (prompts are deterministic, TacticSpec validation prevents degradation)

**Mitigation:**
- Sample and score every 10 gens during run
- If quality drops, halt and investigate (prompt issue? validation bug?)

### Risk 4: Compilation Failures

**Symptom:** 10+ consecutive gens fail to compile (coder hallucinates invalid C++)

**Likelihood:** Very Low (M21 had 0% failure rate over 180 gens)

**Mitigation:**
- Evolve_dual.py has compile-retry loop (max 3 attempts)
- Coder prompt includes ABI constraints and examples
- If >5% failure rate, halt and review coder prompt

---

## Deliverables

**Code:**
- `scripts/m22_analysis.py` - Automated analysis script (fitness plots, tag entropy, etc.)

**Data:**
- `data/runs/m22_rq1_100gen/journal.jsonl` - 100 reflection entries
- `data/runs/m22_rq1_100gen/gen_*/tactic_spec.json` - 100 tactical specifications
- `data/runs/m22_rq1_100gen/champion.cpp` - Final evolved AI

**Documentation:**
- `docs/M22_RESULTS.md` - Full analysis report with:
  - Fitness progression plot
  - Tactic timeline (what emerged when)
  - Reflection quality scores (sampled)
  - Statistical tests (regression, diversity)
  - Manual assessment of top 5 tactics
  - Conclusion: RQ1 answered? (Yes/No/Partial)

**Visualization:**
- Fitness over time (line plot with confidence intervals)
- Tactic tag word cloud (sized by frequency)
- Reflection score distribution (histogram)

---

## Timeline

### Day 0 (Prep, 1 hour)
- ✅ Write M22_PLAN.md
- ✅ Create analysis script skeleton
- ✅ Verify evolve_dual.py checkpoint/resume works
- Launch 100-gen run in background

### Day 1 (Monitoring, 2 hours spread over 50 hours)
- Check progress every 6 hours
- Sample and score 1 journal entry per check
- Plot fitness curve at 25, 50, 75 gens
- Verify no stalls or errors

### Day 2 (Analysis, 6 hours)
- Automated metrics (fitness regression, tag entropy, AAR grounding)
- Manual review (10 sampled entries, tactical timeline)
- Write M22_RESULTS.md with conclusions
- Commit all artifacts

**Total effort:** ~9 hours human time over 3 calendar days (mostly waiting for compute)

---

## Next Steps (M23-M25)

**If M22 succeeds:**
- **M23 (RQ2):** Stability - Does the best tactic converge or keep oscillating?
- **M24 (RQ3):** Transfer - Does evolved AI beat novel opponents (cluster_v1, pursuit_v2)?
- **M25 (RQ4):** Scaling - Does complexity increase with swarm size (10 → 50 drones)?

**If M22 fails:**
- Debug reflection quality (re-score with M17 rubric)
- Adjust prompts (more diversity encouragement?)
- Re-run with different opponent (cluster_v1 instead of pursuit_v1)

---

## Open Questions

1. **Should we checkpoint more frequently?**
   - Current: Implicit (journal.jsonl written after each gen)
   - Proposal: Add `--checkpoint-every 10` flag to evolve_dual.py
   - **Decision:** Not needed yet (journal-based resume works)

2. **Should we run 3 seeds instead of 1?**
   - Pro: Statistical significance, can average out noise
   - Con: 3× cost ($60 vs. $20), 3× time (150 hours vs. 50 hours)
   - **Decision:** Start with 1 seed for RQ1, add seeds for RQ2-RQ4 if needed

3. **Should we use a different opponent?**
   - pursuit_v1: Simple, exploitable (good for innovation)
   - cluster_v1: More complex (better for testing robustness)
   - **Decision:** Stick with pursuit_v1 for RQ1 (consistency with M21 calibration)

4. **Should we automate M17 reflection scoring?**
   - Pro: Continuous quality monitoring, no manual review
   - Con: Adds cost (~$0.05/gen for Opus judge)
   - **Decision:** Manual sampling for M22 (10 entries × 5 min = 50 min), automate for M23+

---

## References

- **M21_PLAN.md** - Dual-LLM architecture design
- **M21_RESULTS.md** - Baseline reflection quality (5/5 validated)
- **IMPLEMENTATION_PLAN.md** - Original RQ1-RQ4 research questions
- **CURRENT_STATUS.md** - Infrastructure readiness assessment

---

**Status:** Ready to launch. Awaiting approval to start 100-gen run.

**Command to start:**
```bash
nohup python3 scripts/evolve_dual.py \
  --opponent src/baselines/pursuit_v1.cpp \
  --as-team A \
  --planner-model claude-opus-4-7 \
  --coder-model claude-haiku-4-5 \
  --generations 100 \
  --n-matches 10 \
  --seed 42 \
  --out-dir data/runs/m22_rq1_100gen \
  --strict-reflection \
  -v \
  > data/runs/m22_rq1_100gen.log 2>&1 &
```
