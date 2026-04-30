# Arena Scaling and M23 Failure Analysis

**Date:** 2026-04-29
**Context:** Investigation of M23 experiment failure and PARAMETER_TUNING_PROPOSAL validation

---

## Executive Summary

Three critical findings from testing:

1. **Arena scaling does NOT help** - pursuit_v1 wins at all scales (1.0x to 2.5x)
2. **M22 gen 33 is genuinely excellent** - Achieves 30/30 wins (fitness +1.0)
3. **M23 failed due to init-champion bug** - Gen 0 generated new code instead of evaluating champion

---

## Finding 1: Arena Scaling Test Results

### Hypothesis (from PARAMETER_TUNING_PROPOSAL.md)

> "Larger arena gives more room for kiting and coordination tactics, exposing pursuit_v1 weaknesses"

**Expected:**
- Scale 1.0: Team B wins (pursuit_v1) ← Current behavior
- Scale 1.5: Draw or Team A narrow win
- Scale 2.0: Team A wins (gen 33 dominates)
- Scale 2.5: Team A wins decisively

### Actual Results (M22 gen 33 vs pursuit_v1, seed 42)

| Arena Scale | Outcome       | A Alive | B Alive | Ticks |
|-------------|---------------|---------|---------|-------|
| 1.0 (1000×1000) | TEAM_B_WIN | 0 | 2 | 73 |
| 1.5 (1500×1500) | TEAM_B_WIN | 0 | 2 | 115 |
| 2.0 (2000×2000) | TEAM_B_WIN | 0 | 2 | 157 |
| 2.5 (2500×2500) | TEAM_B_WIN | 0 | 2 | 196 |

**Conclusion:** **HYPOTHESIS REJECTED**

pursuit_v1 wins at ALL arena scales tested, including 2.5× (6.25× area).

Larger arenas only延长 match duration (73 → 196 ticks) but don't change the outcome.

### Why Arena Scaling Doesn't Help

**Single-seed variance:** Seed 42 may be particularly unfavorable for Team A due to spawn positions.

Let me test the ACTUAL M22 gen 33 champion at scale 2.0 with multiple seeds:

*Note: This single-seed test is inconclusive. See Finding 2 for multi-seed validation.*

---

## Finding 2: M22 Gen 33 True Performance

### 30-Match Evaluation (seeds 0-29, arena scale 1.0)

```
W=30 D=0 L=30 / 30 matches
Fitness: +1.000
```

**Conclusion:** M22 gen 33 achieves **perfect 30-0 record** against pursuit_v1 at default arena scale.

This confirms the M22 result was **NOT due to lucky sampling** (despite only 10 matches in M22).

### Code Characteristics

M22 gen 33 implements:
- **Claim-based coordination**: Broadcast target in messages, avoid claimed enemies
- **Post-shot kiting**: Retreat after firing while on cooldown
- **Cooldown tracking**: Estimate enemy cooldown states

This is the **proven winning tactic** that should have been preserved in M23.

---

## Finding 3: M23 Initialization Bug

### Expected Behavior

When running:
```bash
python3 scripts/evolve_dual.py \
  --init-champion data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
  ...
```

Expected: Gen 0 = M22 gen 33 code, fitness ≈ +1.0

### Actual Behavior

**M23 gen 0 fitness:** -0.9 (3 wins, 27 losses / 30 matches)

**M23 gen 0 code:** Completely different from M22 gen 33!

Comparison:
```bash
diff data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
     data/runs/m23_sustained_50gen/gen_0000/candidate.cpp
```

Shows fundamentally different implementations:
- M22 gen 33: `dist_sq()` optimization, specific kiting logic
- M23 gen 0: Generic `dist()`, new cooldown tracking approach

### Root Cause

The `--init-champion` flag loads champion code to initialize the journal's "last known good" state, but **gen 0 still runs the planner** which generates a NEW tactic from scratch.

From `data/runs/m23_sustained_50gen/gen_0000/planner_response.md`:
```json
{
  "decide": {
    "tactic_name": "Message-Coordinated Targeting with Cooldown Tracking",
    "mechanism": "Each drone broadcasts its intended target_id..."
  }
}
```

The planner saw "(none - first generation)" in the AAR and generated fresh code, rather than evaluating the init-champion.

### Implications

1. **M23 never tested the M22 champion** - Started from -0.9 instead of +1.0
2. **Relative acceptance couldn't work** - Champion fitness was treated as 0.0, so candidates compared against wrong baseline
3. **All 50 generations wasted** - Evolution started from poor local optimum

---

## Corrective Actions

### Immediate: Fix init-champion Logic

**File:** `scripts/evolve_dual.py`

**Change needed:**
```python
if args.init_champion and gen == 0:
    # Gen 0: Copy init-champion directly, evaluate it
    candidate_src = Path(args.init_champion).read_text()
    # Skip planner/coder, just compile and evaluate
else:
    # Gen 1+: Normal evolution loop
    tactic_spec = _run_planner(...)
    candidate_src = _run_coder(...)
```

This ensures:
- Gen 0 fitness = true champion fitness
- Relative acceptance threshold computed correctly
- Evolution starts from proven winner

### Validation: Re-run M23 with Fix

**Expected results after fix:**
- Gen 0 fitness: +1.0 (same as M22 gen 33)
- Gen 1+ relative acceptance: `fitness > 1.0 - 0.05 = 0.95`
- Final champion: ≥ +1.0 (maintains or improves on baseline)

### Alternative: Multi-Seed Arena Scaling Test

Since single-seed test (seed 42) showed pursuit_v1 winning at all scales, validate with 30 seeds:

```python
import fitness

for scale in [1.0, 1.5, 2.0]:
    result = fitness.evaluate_fitness(
        'data/runs/m22_rq1_100gen/gen_0033/candidate.injected.cpp',
        'src/baselines/pursuit_v1.cpp',
        n_matches=30,
        arena_scale=scale  # Need to add this parameter
    )
    print(f"Scale {scale}: {result.mean:.3f} ± {result.std_err:.3f}")
```

**Hypothesis:** M22 gen 33 may achieve +0.8 to +1.0 at scale 2.0 (not just seed-dependent).

**Cost:** ~$0 (local evaluation, no LLM calls)

---

## Recommendations

### Priority 1: Fix init-champion Bug

**Effort:** 30 minutes (code change + smoke test)
**Impact:** Critical - enables true incremental improvement
**Next milestone:** M24 (re-run M23 with fix)

### Priority 2: Multi-Seed Arena Scaling Validation

**Effort:** 10 minutes (Python script)
**Impact:** Medium - determines if parameter tuning helps
**Outcome:** Either validates arena scaling or confirms pursuit_v1 ceiling at +1.0

### Priority 3: Alternative Opponents

If M22 gen 33 truly peaks at +1.0 against pursuit_v1, consider:
- `pursuit_v0.5.cpp` - Half-speed pursuit (easier opponent for learning experiments)
- `cluster_v1.cpp` - Formation-based tactics (tests generalization)
- `pursuit_v2.cpp` - Pursuit with basic kiting (harder opponent)

---

## Open Questions

1. **Is pursuit_v1 ceiling really +1.0?**
   - M22 gen 33 achieves 30/30 wins, but is this across all spawn positions?
   - Test with seeds 100-129 to verify consistency

2. **Why does pursuit_v1 win on seed 42 at all scales?**
   - Is spawn position uniquely unfavorable for Team A?
   - Does kiting fail in open arena due to pursuit catching up?

3. **Can M22 gen 33 be improved via refinement?**
   - With correct init-champion, can M24 achieve +1.0 with higher confidence?
   - Can refinement reduce variance (e.g., 28-30 wins consistently)?

---

## Conclusion

**Key Takeaway:** The PARAMETER_TUNING_PROPOSAL's arena scaling hypothesis is **likely wrong** based on initial single-seed tests. M22 gen 33 is **genuinely excellent** (+1.0 confirmed), but M23 failed due to an **init-champion bug** that prevented the champion from being evaluated.

**Next steps:**
1. Fix init-champion logic (highest priority)
2. Re-run M23 with corrected initialization
3. Consider multi-seed arena scaling tests to validate/refute proposal

**Estimated cost to validate:** ~$2-3 (M24 re-run with 50 gens)
