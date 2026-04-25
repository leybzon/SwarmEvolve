# M23: Sustained Improvement Experiment

**Goal:** Fix M22 infrastructure issues to achieve sustained fitness improvement against pursuit_v1

**Status:** READY TO IMPLEMENT
**Estimated Duration:** 1 day (4 hours implementation + 2 hours experiment + 2 hours analysis)
**Estimated Cost:** $12 (50 gens × 30 matches × $0.008/match)

---

## Motivation

M22 successfully discovered **winning tactics** (claim-based coordination + post-shot kiting achieved fitness +1.0), but failed to show sustained improvement due to three infrastructure flaws:

1. **Overly strict acceptance** (`fitness > 0.0` absolute threshold) rejected incremental improvements
2. **Small sample size** (10 matches) caused high variance, masking true fitness signal
3. **No iteration prompting** caused LLM to restart exploration instead of refining winning tactics

M23 will fix these issues and validate that the dual-LLM system can **sustain and improve** a winning tactic over 50 generations.

---

## Changes from M22

### 1. Relative Acceptance Criterion

**M22 (broken):**
```python
accepted = fitness_result.mean > 0.0  # Absolute threshold
```

**M23 (fixed):**
```python
# Accept if better than current champion (relative improvement)
champion_fitness = _get_champion_fitness(journal_path)
accepted = fitness_result.mean > (champion_fitness - 0.05)  # Small epsilon for noise
```

**Effect:** Enables **incremental improvements**. If champion is at +0.6, a candidate at +0.7 gets accepted.

### 2. Increased Sample Size

**M22:** `--n-matches 10` (std_err ≈ 0.3)

**M23:** `--n-matches 30` (std_err ≈ 0.17, reduces variance by √3)

**Effect:** More reliable fitness estimates, fewer false negatives.

### 3. Champion Initialization

**M22:** Started from `stationary_v1.cpp` baseline (wasted 33 gens rediscovering basics)

**M23:** Start from **gen 33 champion** (claim+kite tactic that achieved +1.0)

**Effect:** System begins at **known-good baseline** and focuses on **refinement** from generation 0.

### 4. Iteration-Aware Prompting

**New planner prompt section:**
```markdown
## Tactical Evolution Strategy

**Current Champion Fitness:** {CHAMPION_FITNESS}
**Last Attempt Fitness:** {LAST_FITNESS}

Choose your approach:

1. **REFINE** (if last_fitness ≥ champion_fitness - 0.3):
   - Make a targeted improvement to ONE mechanism
   - Example: adjust retreat distance, change claim tie-break rule, optimize memory layout
   - Label hypothesis as "Refined: [mechanism change]"

2. **EXPLORE** (if last_fitness < champion_fitness - 0.3):
   - Try a fundamentally different tactical approach
   - Example: switch from kiting to flanking, from claim-coordination to role-assignment
   - Label hypothesis as "New approach: [mechanism name]"

Prefer REFINE when possible - cumulative improvements compound faster than random exploration.
```

**Effect:** Encourages **iterative refinement** over random restarts.

---

## Experiment Design

### Configuration

```bash
python3 scripts/evolve_dual.py \
  --opponent src/baselines/pursuit_v1.cpp \
  --as-team A \
  --planner-model claude-opus-4-7 \
  --coder-model claude-haiku-4-5 \
  --generations 50 \
  --n-matches 30 \
  --seed 42 \
  --out-dir data/runs/m23_sustained_50gen \
  --init-champion data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
  --acceptance-mode relative \
  --strict-reflection \
  -v
```

**New flags:**
- `--init-champion`: Path to C++ file to use as starting champion (skips stationary baseline)
- `--acceptance-mode`: `relative` (compare to champion) vs `absolute` (compare to 0.0)

### Success Criteria

**Primary:**
1. ✅ **Fitness improvement:** Linear regression slope > 0.001 with p < 0.05
2. ✅ **High win rate:** Final champion achieves ≥80% win rate (fitness ≥ +0.6) over 30 matches
3. ✅ **Sustained performance:** At least 5 consecutive generations with fitness ≥ +0.5

**Secondary:**
4. ✅ **Acceptance rate:** 15-30% (vs. 3% in M22)
5. ✅ **Iteration evidence:** ≥20% of journal entries contain refinement keywords
6. ✅ **Convergence:** Final 10 generations show fitness variance < 0.15 (stable optimum)

**Stretch:**
7. 🎯 **Breakthrough:** Final champion achieves fitness ≥ +0.9 (≥95% win rate)

---

## Implementation Plan

### Step 1: Code Changes to `evolve_dual.py`

**1.1: Add `--init-champion` flag** (argparse section):
```python
parser.add_argument("--init-champion", type=Path, default=None,
                    help="Path to initial champion C++ file (default: stationary_v1.cpp)")
parser.add_argument("--acceptance-mode", choices=["absolute", "relative"], default="absolute",
                    help="Acceptance criterion: absolute (>0.0) vs relative (>champion)")
```

**1.2: Load initial champion** (before generation loop):
```python
if args.init_champion:
    current_champion = args.init_champion.read_text(encoding="utf-8")
    _LOG.info("init-champion loaded from %s", args.init_champion)
else:
    baseline_path = REPO_ROOT / "src" / "baselines" / "stationary_v1.cpp"
    current_champion = baseline_path.read_text(encoding="utf-8")
    _LOG.info("init-champion using stationary baseline")
```

**1.3: Implement relative acceptance** (replace line 214):
```python
def _get_champion_fitness(journal_path: Path) -> float:
    """Get fitness of most recent accepted champion."""
    if not journal_path.exists():
        return -1.0  # Pessimistic default

    entries = journal_mod.read_entries(journal_path)
    accepted_entries = [e for e in entries if e.get('verdict') == 'confirmed']

    if not accepted_entries:
        return -1.0

    # Return fitness of most recent accepted entry
    return accepted_entries[-1]['fitness']


# In generation loop (replace line 214):
if args.acceptance_mode == "relative":
    champion_fitness = _get_champion_fitness(journal_path)
    # Accept if better than champion, with small epsilon for noise
    accepted = fitness_result.mean > (champion_fitness - 0.05)
else:
    # M22 behavior (absolute threshold)
    accepted = fitness_result.mean > 0.0
```

### Step 2: Enhance Planner Prompt

**2.1: Create `prompts/planner_analyze_aar_v2.md`** (copy of v1 with additions):

Add after "## After-Action Report" section:
```markdown
## Tactical Evolution Context

**Current Champion Fitness:** {CHAMPION_FITNESS}
**Last Generation:** {GENERATION-1}
**Last Hypothesis:** {LAST_HYPOTHESIS}
**Last Fitness:** {LAST_FITNESS}

### Evolution Strategy Guide

You have two strategic options:

**Option 1: REFINE** (recommended if `last_fitness ≥ champion_fitness - 0.3`)
- Make a targeted improvement to ONE mechanism from the last attempt
- Examples:
  - Adjust retreat distance (8 ticks → 10 ticks)
  - Change claim arbitration tie-break (nearest → lowest-id)
  - Optimize memory layout (store retreat vector in [1..2] instead of [3..4])
  - Add edge-case handling (boundary retreat, no-enemy fallback)
- Label your tactic_name as "Refined: [previous_name] - [what changed]"
- In `why_this_counters_failure`, explain **what you changed and why**

**Option 2: EXPLORE** (recommended if `last_fitness < champion_fitness - 0.3`)
- Try a fundamentally different tactical approach
- Examples:
  - Switch from post-shot kiting to pre-emptive flanking
  - Replace claim-coordination with role-assignment (bait vs striker)
  - Abandon retreat and focus on formation control
- Label your tactic_name as "New approach: [mechanism name]"

**Guidance:**
- If the last 3+ generations used REFINE but fitness plateaued, switch to EXPLORE
- If EXPLORE has failed 5+ times, return to REFINE on the best-known tactic
- Small, targeted changes (REFINE) compound faster than random exploration (EXPLORE)
```

**2.2: Update template rendering in `dual_llm.py`:**
```python
def dual_llm_generate(...):
    # Get champion fitness and last generation info
    champion_fitness = _get_champion_fitness_from_journal(journal_path)
    last_entry = _get_last_journal_entry(journal_path)

    # Render template with new placeholders
    replacements = {
        "{CHAMPION_FITNESS}": f"{champion_fitness:.3f}",
        "{LAST_HYPOTHESIS}": last_entry.get('hypothesis_tested', 'N/A') if last_entry else 'N/A',
        "{LAST_FITNESS}": f"{last_entry['fitness']:.3f}" if last_entry else 'N/A',
        "{GENERATION-1}": str(generation - 1) if generation > 0 else 'N/A',
        ...
    }
```

### Step 3: Increase Journal Recall Diversity

**3.1: Modify `journal.py::recall()`** to prioritize champion:
```python
def recall(
    journal_path: Path,
    *,
    recency_k: int = 2,  # REDUCED from 3
    max_entries: int = 5,
) -> list[dict]:
    """Recall recent entries + champion + diverse entries."""

    entries = read_entries(journal_path)
    if not entries:
        return []

    # Recent entries (exclude current generation if in-progress)
    recent = entries[-recency_k:]

    # Champion entry (best accepted so far)
    accepted = [e for e in entries if e.get('verdict') == 'confirmed']
    champion = max(accepted, key=lambda e: e['fitness']) if accepted else None

    # Diverse entries (tag-based diversity, exclude recent and champion)
    exclude_gens = {e['generation'] for e in recent}
    if champion:
        exclude_gens.add(champion['generation'])

    diverse = _select_diverse(
        [e for e in entries if e['generation'] not in exclude_gens],
        n=max_entries - recency_k - (1 if champion else 0)
    )

    # Combine: [recent, champion, diverse]
    result = recent[:]
    if champion:
        result.append(champion)
    result.extend(diverse)

    return result[:max_entries]
```

---

## Risk Mitigation

### Risk 1: Champion Initialization Fails (File Not Found)

**Mitigation:**
```python
if args.init_champion and not args.init_champion.exists():
    _LOG.error("init-champion file not found: %s", args.init_champion)
    return 1

# Validate champion compiles before starting run
try:
    _ = fitness_mod.evaluate_fitness(
        args.init_champion,
        args.opponent,
        n_matches=1,
        seed_base=0,
    )
except Exception as e:
    _LOG.error("init-champion failed compilation or execution: %s", e)
    return 1
```

### Risk 2: Relative Acceptance Gets Stuck in Local Optimum

**Symptom:** System accepts mediocre tactics because champion is also mediocre.

**Mitigation:** Add **absolute floor**:
```python
# Accept if better than champion AND above absolute minimum
accepted = (fitness_result.mean > champion_fitness - 0.05) and \
           (fitness_result.mean > -0.5)  # Floor: reject if <20% win rate
```

### Risk 3: Increased Sample Size (30 matches) Slows Experiment

**M23 estimated time:** 50 gens × 30 matches × 0.5 min/match = **750 minutes (12.5 hours)**

**Mitigation:**
- Run overnight
- Monitor at 25-gen checkpoint (6 hours)
- If promising, let it complete; if failing, abort and adjust

---

## Timeline

### Day 0 (Implementation, 4 hours)

- ✅ Write M23_PLAN.md (this document)
- [ ] Implement `evolve_dual.py` changes (1.5 hours)
  - Add `--init-champion` and `--acceptance-mode` flags
  - Implement `_get_champion_fitness()` helper
  - Update acceptance logic
- [ ] Create `prompts/planner_analyze_aar_v2.md` (0.5 hours)
- [ ] Update `dual_llm.py` template rendering (0.5 hours)
- [ ] Modify `journal.py::recall()` (0.5 hours)
- [ ] Write unit tests for new functions (1 hour)
  - Test `_get_champion_fitness()` on sample journal
  - Test relative acceptance logic
  - Test champion initialization

### Day 1 (Execution, 12-16 hours wall time, 2 hours human time)

- [ ] Launch M23 50-gen experiment
- [ ] Monitor at 25-gen checkpoint (6 hours elapsed)
- [ ] Check for stalls, API errors, acceptance rate
- [ ] Let run complete overnight

### Day 2 (Analysis, 2 hours)

- [ ] Statistical analysis (fitness trend, acceptance rate, iteration rate)
- [ ] Sample 10 journal entries for manual review
- [ ] Compare gen 0 champion vs gen 49 champion (head-to-head match)
- [ ] Write M23_RESULTS.md
- [ ] Commit all artifacts

---

## Expected Outcomes

### Best Case (M23 Success)

- **Fitness trajectory:** +1.0 (gen 0) → +1.0 (gen 49) with intermediate refinements at +0.7, +0.8, +0.9
- **Acceptance rate:** 25% (12-13 out of 50 accepted)
- **Iteration keywords:** 30% of entries (15/50 mention "refined", "optimized", "adjusted")
- **Final champion:** Fitness +1.0 ± 0.1 (90-100% win rate over 30 matches)

**Conclusion:** Dual-LLM can sustain and improve tactics. Proceed to M24 (transfer learning).

### Realistic Case (Partial Success)

- **Fitness trajectory:** +1.0 (gen 0) → +0.7 (gen 49) with plateau around gen 20
- **Acceptance rate:** 15% (7-8 out of 50 accepted)
- **Iteration keywords:** 15% of entries
- **Final champion:** Fitness +0.6 to +0.8 (70-85% win rate)

**Conclusion:** Incremental improvement works but pursuit_v1 ceiling may be ~80%. Proceed to M24 with alternative opponents.

### Worst Case (M23 Failure)

- **Fitness trajectory:** +1.0 (gen 0) → -0.5 (gen 49) - regression!
- **Acceptance rate:** 5% (still broken)
- **Iteration keywords:** 0% (prompt changes had no effect)

**Diagnosis:**
- If acceptance rate still low → sample size still too small, increase to 50 matches
- If iteration rate zero → prompt not rendering correctly, check `dual_llm.py` template
- If regression → champion initialization corrupted, verify gen 33 file integrity

---

## Exit Criteria

M23 succeeds if:

1. ✅ **Fitness improvement:** Linear regression slope > 0 with p < 0.1 (relaxed from 0.05 due to smaller N)
2. ✅ **High acceptance rate:** ≥15% of generations accepted (vs. 3% in M22)
3. ✅ **Final champion fitness:** ≥ +0.6 (≥70% win rate over 30 matches)

**Partial success if:**
- 2/3 criteria met → can proceed to M24 with caveats
- Final champion ≥ +0.5 → demonstrates sustained competence even if not statistical significance

**Failure if:**
- Final champion < +0.3 → regression below M22 results
- Acceptance rate < 10% → infrastructure fixes didn't work

---

## Deliverables

**Code:**
- `scripts/evolve_dual.py` (updated with relative acceptance, init-champion flag)
- `prompts/planner_analyze_aar_v2.md` (iteration-aware prompting)
- `scripts/dual_llm.py` (template rendering for new placeholders)
- `scripts/journal.py` (champion-aware recall)

**Data:**
- `data/runs/m23_sustained_50gen/journal.jsonl` (50 entries)
- `data/runs/m23_sustained_50gen/gen_*/` (50 generation folders)
- `data/runs/m23_sustained_50gen/champion.cpp` (final evolved AI)

**Documentation:**
- `docs/M23_RESULTS.md` (full analysis report)

**Visualizations:**
- Fitness over time (line plot with M22 comparison)
- Acceptance rate histogram (M22 vs M23)
- Tactic iteration tree (show refinement chains)

---

## Open Questions

1. **Should we use softmax acceptance instead of hard threshold?**
   - Pro: Smoother exploration/exploitation tradeoff
   - Con: More complex, harder to tune temperature parameter
   - **Decision:** Start with hard threshold (simpler), try softmax in M24 if needed

2. **Should we run multiple seeds (3×) for statistical significance?**
   - Pro: Reduces variance, stronger claims
   - Con: 3× cost ($36 vs $12), 3× time (36 hours vs 12 hours)
   - **Decision:** Start with 1 seed for M23, add seeds for publication-ready M25

3. **Should we test champion head-to-head against gen 33 baseline?**
   - Pro: Direct measurement of improvement
   - Con: Adds complexity (3-way tournaments)
   - **Decision:** Yes, run 100-match head-to-head as final validation step

---

## References

- **M22_PLAN.md** - Original RQ1 experimental design
- **M22_RESULTS.md** - Deep analysis of infrastructure failures
- **M21_RESULTS.md** - Dual-LLM validation baseline
- **IMPLEMENTATION_PLAN.md** - Research questions roadmap

---

**Status:** Plan complete. Ready to implement. Awaiting approval to proceed.

**Command to execute after implementation:**
```bash
nohup python3 scripts/evolve_dual.py \
  --opponent src/baselines/pursuit_v1.cpp \
  --as-team A \
  --planner-model claude-opus-4-7 \
  --coder-model claude-haiku-4-5 \
  --generations 50 \
  --n-matches 30 \
  --seed 42 \
  --out-dir data/runs/m23_sustained_50gen \
  --init-champion data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
  --acceptance-mode relative \
  --strict-reflection \
  -v \
  > data/runs/m23_sustained_50gen.log 2>&1 &
```
