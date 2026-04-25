# M22 → M23 Transition Summary

**Date:** 2026-04-25

---

## M22 Results: What We Learned

### ✅ Success: LLM Can Discover Winning Tactics

**Discovered:** "Claim-Arbitrated Targeting with Post-Shot Kiting"
- Gen 33: fitness +1.0 (perfect win, 10-0 record)
- Gen 51: fitness +0.6 (6-4 record)
- Gen 81: fitness +1.0 (perfect win, rediscovered same tactic)

**Core Mechanism:**
1. **Claim-based coordination:** Broadcast target_id in message[2], read allies' claims before selecting target → eliminates focus-fire redundancy (0.50 → 0.18)
2. **Post-shot kiting:** Retreat for 8-10 ticks after firing → exploits pursuit_v1's zero kiting behavior

**This tactic was NEVER hand-coded - pure LLM innovation** ✨

### ❌ Failure: Infrastructure Prevented Sustained Improvement

**3 Critical Bugs:**

1. **Overly strict acceptance:** `fitness > 0.0` absolute threshold
   - After gen 33 won at +1.0, system required EVERY future gen to beat +1.0
   - Result: 60+ generations rejected despite using same winning tactic
   - **Fix:** Use relative acceptance (`fitness > champion - 0.05`)

2. **Small sample size:** 10 matches per generation
   - Standard error ~0.3 → true +0.7 tactic measured as +0.4 to +1.0 (huge variance)
   - Result: Good tactics rejected due to noise, lucky tactics accepted
   - **Fix:** Increase to 30 matches (reduces std_err by √3)

3. **No iteration prompting:** LLM restarted from scratch each generation
   - Tried 82 unique tactics but 0% mentioned "refine" or "improve"
   - Result: Rediscovered claim+kite 10+ times instead of refining it
   - **Fix:** Add REFINE vs EXPLORE guidance to planner prompt

### 📊 Final Statistics

- **Generations:** 95 (out of 100 target)
- **Mean fitness:** -0.600 (losing on average, but 3 wins!)
- **Unique tactics:** 82 (excellent diversity)
- **Acceptance rate:** 3.2% (broken - should be 15-30%)
- **Best fitness:** +1.0 (gen 33, 81)

---

## M23 Plan: Fix Infrastructure, Sustain Wins

### Changes

| Issue | M22 (Broken) | M23 (Fixed) |
|-------|--------------|-------------|
| Acceptance | `fitness > 0.0` (absolute) | `fitness > champion - 0.05` (relative) |
| Sample size | 10 matches (std_err ~0.3) | 30 matches (std_err ~0.17) |
| Initial champion | stationary_v1 (weak) | gen 33 champion (proven +1.0) |
| Prompting | Generic exploration | REFINE vs EXPLORE guidance |
| Journal recall | 3 recent + 2 diverse | 2 recent + 1 champion + 2 diverse |

### Expected Outcomes

**Target:** Sustain +0.6 to +1.0 win rate over 50 generations

**Success criteria:**
- ✅ Fitness trend: slope > 0 (even if small) with p < 0.1
- ✅ Acceptance rate: 15-30% (vs. 3% in M22)
- ✅ Final champion: fitness ≥ +0.6 over 30 matches
- ✅ Iteration rate: ≥20% of entries mention refinement

**Estimated cost:** $12 (50 gens × 30 matches × $0.008/match)
**Estimated time:** 12 hours wall time

---

## Key Insights for Next Experiment

### Why pursuit_v1 Is Hard to Beat

**Their advantages:**
- Simple (66 lines), robust (no bugs, no edge cases)
- Deterministic multi-drone focus-fire → mutual destruction trades
- Even our best tactic (claim+kite) only achieves ~70-80% win rate

**Our disadvantages:**
- Complex (186 lines for gen 33 champion)
- State machine bugs (uninitialized retreat vectors, memory layout errors)
- Message coordination overhead (parsing, claim arbitration)

**Hypothesis:** pursuit_v1 ceiling may be ~70-80% win rate due to:
- Variance in spawn positions (sometimes we start surrounded)
- Forced engagements (10 drones in 1000×1000 arena → inevitable clashes)
- Fundamental combat math (mutual destruction when both in range)

**Test in M23:** Run gen 33 champion for 100 matches to measure true win rate. If <75%, consider alternative opponents for M24.

### Alternative Opponents for M24 (If pursuit_v1 Too Hard)

1. **stationary_v1:** Drones don't move (100% win expected, trivial)
2. **pursuit_v0.5:** Half-speed pursuit (90% win expected, easier learning target)
3. **random_walk_v1:** Stochastic movement (60% win expected, tests robustness)
4. **cluster_v1:** Coordinate to stay together (70% win expected, tests different tactics)

**Strategy:** Use easier opponent to validate infrastructure fixes (M23), then return to pursuit_v1 for final validation (M24).

---

## Implementation Checklist for M23

### Code Changes

- [ ] `scripts/evolve_dual.py`:
  - [ ] Add `--init-champion` flag (argparse)
  - [ ] Add `--acceptance-mode` flag (argparse)
  - [ ] Implement `_get_champion_fitness()` helper
  - [ ] Update acceptance logic (line 214)
  - [ ] Validate init-champion compiles before run

- [ ] `prompts/planner_analyze_aar_v2.md`:
  - [ ] Add "Tactical Evolution Strategy" section
  - [ ] Add REFINE vs EXPLORE guidance
  - [ ] Add placeholders for {CHAMPION_FITNESS}, {LAST_HYPOTHESIS}, {LAST_FITNESS}

- [ ] `scripts/dual_llm.py`:
  - [ ] Implement `_get_champion_fitness_from_journal()` helper
  - [ ] Implement `_get_last_journal_entry()` helper
  - [ ] Update template rendering with new placeholders

- [ ] `scripts/journal.py`:
  - [ ] Modify `recall()` to include champion entry
  - [ ] Reduce recency_k from 3 to 2 to make room for champion

### Testing

- [ ] Unit test `_get_champion_fitness()` on M22 journal
- [ ] Unit test relative acceptance logic with mock fitness results
- [ ] Integration test: run 3-gen smoke test with init-champion
- [ ] Verify prompt rendering: check planner sees champion fitness

### Execution

- [ ] Copy gen 33 champion to safe location (don't lose it!)
- [ ] Launch M23 50-gen experiment
- [ ] Monitor at 25-gen checkpoint (6 hours)
- [ ] Analyze results and write M23_RESULTS.md

---

## Success Metrics (How to Know It Worked)

### M23 vs M22 Comparison

| Metric | M22 (Broken) | M23 (Target) | Interpretation |
|--------|--------------|--------------|----------------|
| Mean fitness | -0.60 | +0.50 | Sustained winning |
| Acceptance rate | 3% | 20% | Incremental progress |
| Fitness slope | -0.0005 | +0.002 | Positive trend |
| Iteration rate | 0% | 20% | Refinement happening |
| Final champion | +1.0 (gen 33) | +0.7 to +1.0 | Maintained or improved |

**If M23 achieves these targets:** Infrastructure fixes worked, dual-LLM can sustain wins ✅

**If M23 still fails:** Root cause deeper than acceptance/sampling (investigate: prompt rendering bugs, LLM capability limits, opponent too hard)

---

## Longer-Term Roadmap

### M23 (Current): Fix Infrastructure
- Goal: Sustain +0.6+ win rate over 50 gens
- Opponent: pursuit_v1
- If success → M24 (transfer learning)
- If failure → M23b (easier opponent)

### M24: Transfer Learning
- Goal: Test if claim+kite generalizes to novel opponents
- Opponents: cluster_v1, pursuit_v2, random_walk_v1
- Success = ≥60% win rate on ≥2 out of 3 novel opponents

### M25: Scaling
- Goal: Test if complexity increases with swarm size
- Sizes: 10 drones (baseline), 20 drones, 50 drones
- Success = maintained win rate + new emergent tactics at larger scale

---

## Open Questions

1. **What is pursuit_v1's true ceiling?**
   - Run gen 33 champion for 100 matches → measure variance-reduced win rate
   - If <75%, may need alternative opponents for learning experiments

2. **Does iteration prompting actually work?**
   - M23 will test this - look for "Refined:", "v2", "optimized" in hypotheses
   - If still 0% iteration rate → LLM not reading prompt, investigate rendering

3. **Is 30 matches enough to reduce noise?**
   - M23 std_err will be ~0.17 (vs 0.30 in M22)
   - If still too noisy, try 50 matches in M24 (expensive but definitive)

4. **Should we use softmax acceptance for smoother exploration?**
   - Hard threshold (M23): `accept if fitness > champion - 0.05`
   - Softmax (M24?): `accept with prob 1 / (1 + exp(-10 * delta))`
   - Start simple, add complexity only if needed

---

## Conclusion

**M22 proved the science works** - dual-LLM discovered a winning tactic that was never hand-coded.

**M23 will prove the engineering works** - infrastructure fixes enable sustained improvement.

**If both succeed:** We have a production-ready system for LLM-guided tactical evolution. 🚀

---

**Ready to proceed to M23 implementation.**
