# Co-Evolution Proposal: Dual-Team Competitive Evolution

**Date:** 2026-04-29
**Problem:** Current system evolves only Team A against static pursuit_v1, creating a performance ceiling
**Proposal:** Enable both teams to evolve competitively against each other

---

## Current System (Asymmetric Evolution)

```
Generation N:
  Team A: LLM-evolved (changes each generation)
  Team B: pursuit_v1 (static, never changes)

Result: Team A hits ceiling when it beats pursuit_v1 perfectly
```

**Limitations:**
- Once Team A achieves 100% win rate, no further evolution possible
- pursuit_v1 doesn't adapt to Team A's tactics
- No arms race dynamics
- Limited tactical diversity

---

## Proposed System: Co-Evolution

### Architecture: Alternating Evolution

```
Generation 0:
  Team A: init_champion_A (e.g., M22 gen 33)
  Team B: init_champion_B (e.g., pursuit_v1)
  Match: A vs B → fitness_A, fitness_B

Generation 1:
  Team A: Evolve (planner + coder analyze loss to B)
  Team B: Keep champion_B (static this round)
  Match: new_A vs champion_B → fitness_A, fitness_B
  Update: champion_A if accepted

Generation 2:
  Team A: Keep champion_A (static this round)
  Team B: Evolve (planner + coder analyze loss to A)
  Match: champion_A vs new_B → fitness_A, fitness_B
  Update: champion_B if accepted

Generation 3:
  Team A: Evolve against champion_B
  Team B: Keep champion_B
  ... repeat alternating ...
```

**Benefits:**
- Both teams continuously adapt to each other
- Arms race: better Team A → forces Team B to improve → forces Team A to counter
- No static ceiling
- Richer tactical diversity

---

## Implementation Options

### Option 1: Alternating Single-Team Evolution (Simpler)

**New script:** `scripts/evolve_coevolve.py`

```python
def coevolve(
    init_champion_a: Path,
    init_champion_b: Path,
    generations: int,
    n_matches: int,
    # ... other params
):
    champion_a = init_champion_a.read_text()
    champion_b = init_champion_b.read_text()

    for gen in range(generations):
        # Alternate which team evolves
        if gen % 2 == 0:
            # Team A's turn to evolve
            new_a = dual_llm_generate(
                opponent_source=champion_b,
                as_team='A',
                # ...
            )
            fitness_a, fitness_b = evaluate_match(new_a, champion_b)

            if fitness_a > champion_a_fitness - 0.05:
                champion_a = new_a
                champion_a_fitness = fitness_a
        else:
            # Team B's turn to evolve
            new_b = dual_llm_generate(
                opponent_source=champion_a,
                as_team='B',
                # ...
            )
            fitness_a, fitness_b = evaluate_match(champion_a, new_b)

            if fitness_b > champion_b_fitness - 0.05:
                champion_b = new_b
                champion_b_fitness = fitness_b

        # Log both champions
        write_journal_entry(gen, champion_a, champion_b, fitness_a, fitness_b)
```

**Pros:**
- Simple to implement (~200 lines, modifying evolve_dual.py)
- Clear alternation: easy to understand
- Lower cost: only one LLM call per generation

**Cons:**
- Slower convergence (each team evolves every 2 generations)
- Could get stuck if one team plateaus

---

### Option 2: Simultaneous Dual-Team Evolution (More Complex)

```python
def coevolve_simultaneous(
    init_champion_a: Path,
    init_champion_b: Path,
    generations: int,
    n_matches: int,
):
    champion_a = init_champion_a.read_text()
    champion_b = init_champion_b.read_text()

    for gen in range(generations):
        # Both teams evolve simultaneously (parallel LLM calls)
        new_a = dual_llm_generate(opponent_source=champion_b, as_team='A')
        new_b = dual_llm_generate(opponent_source=champion_a, as_team='B')

        # Evaluate all 4 matchups:
        # 1. champion_a vs champion_b (baseline)
        # 2. new_a vs champion_b
        # 3. champion_a vs new_b
        # 4. new_a vs new_b (both evolved)

        results = {
            'baseline': evaluate(champion_a, champion_b),
            'a_evolved': evaluate(new_a, champion_b),
            'b_evolved': evaluate(champion_a, new_b),
            'both_evolved': evaluate(new_a, new_b),
        }

        # Update champions using tournament selection
        if results['both_evolved']['a_wins'] > results['baseline']['a_wins']:
            champion_a = new_a
        if results['both_evolved']['b_wins'] > results['baseline']['b_wins']:
            champion_b = new_b
```

**Pros:**
- Faster convergence (both evolve each generation)
- More realistic arms race
- Interesting dynamics when both evolve together

**Cons:**
- 2× LLM cost per generation
- More complex acceptance logic (which matchup to prioritize?)
- Requires 4× match evaluations (or 1× if only test both_evolved)

---

### Option 3: Population-Based Co-Evolution (Most Complex)

Maintain populations of Team A and Team B candidates:

```python
def coevolve_population(
    init_champion_a: Path,
    init_champion_b: Path,
    generations: int,
    population_size: int = 5,
):
    # Maintain top-5 candidates per team
    population_a = [init_champion_a] * population_size
    population_b = [init_champion_b] * population_size

    for gen in range(generations):
        # Generate new candidates
        new_a = dual_llm_generate(opponent_source=best_b, as_team='A')
        new_b = dual_llm_generate(opponent_source=best_a, as_team='B')

        # Round-robin tournament
        for a in population_a:
            for b in population_b:
                fitness_a, fitness_b = evaluate_match(a, b)
                # Track Elo ratings

        # Evict worst performers, add new candidates
        population_a = top_k_by_elo(population_a + [new_a], k=5)
        population_b = top_k_by_elo(population_b + [new_b], k=5)
```

**Pros:**
- Most robust (maintains diversity)
- Avoids local optima
- Elo ratings provide clear ranking

**Cons:**
- Expensive: 25 matches per generation (5×5 round-robin)
- Complex to implement (~500+ lines)
- Overkill for current scale

---

## Recommended: Option 1 (Alternating Evolution)

**Why:**
- Easiest to implement and debug
- Clear interpretation (one team evolves per round)
- Reasonable cost (~$5 for 50 generations)
- Can upgrade to Option 2 later if needed

**Implementation plan:**

1. **Create `scripts/evolve_coevolve.py`** (copy from `evolve_dual.py`)
2. **Modify main loop:**
   - Add `champion_b` variable
   - Alternate `as_team` parameter: `'A'` on even gens, `'B'` on odd gens
   - Pass opponent as `champion_a` or `champion_b` depending on whose turn
3. **Update journal schema:**
   - Add `champion_a_code`, `champion_b_code` fields
   - Add `fitness_b` field (currently only track Team A fitness)
4. **Update acceptance logic:**
   - Check `fitness_a > champion_a_fitness - 0.05` on Team A rounds
   - Check `fitness_b > champion_b_fitness - 0.05` on Team B rounds
5. **Test with short run:**
   - 10 generations (5 per team)
   - Verify both champions evolve

**Estimated effort:** 2-3 hours

---

## Expected Dynamics

### Phase 1: Initial Adaptation (Gens 0-10)
- Team B (starting from pursuit_v1) learns to counter Team A's kiting
- Team A refines tactics against smarter opponent
- Both improve modestly

### Phase 2: Arms Race (Gens 10-30)
- Team B discovers counter-kiting (predict retreat vectors?)
- Team A develops deceptive kiting (randomized retreat angles?)
- Fitness oscillates as teams leapfrog

### Phase 3: Meta-Game Convergence (Gens 30-50+)
- Tactics stabilize into rock-paper-scissors patterns
- Both teams reach local Nash equilibrium
- Further improvements require fundamental innovation

---

## Alternative: Red Team vs Blue Team Tournaments

Instead of co-evolution, run **separate evolution tracks** then tournament:

```
Track A: Evolve Team A vs pursuit_v1 (current system)
Track B: Evolve Team B vs stationary_v1 (easier opponent)

After 50 gens each:
  Tournament: best_A vs best_B (100 matches)
  Winner: overall champion
```

**Pros:**
- Simpler than co-evolution
- Can parallelize tracks
- Establishes "best of breed" from independent lineages

**Cons:**
- No adaptation between teams
- Less interesting dynamics
- Still requires diverse opponents (pursuit_v1, cluster_v1, etc.)

---

## Code Changes Required

### File: `scripts/evolve_coevolve.py` (NEW)

```python
#!/usr/bin/env python3
"""Co-evolution: both teams evolve competitively against each other."""

from pathlib import Path
import sys

# Copy most logic from evolve_dual.py
# Key differences:

def evolve_coevolve(
    init_champion_a: Path,
    init_champion_b: Path,
    generations: int,
    n_matches: int,
    seed: int,
    out_dir: Path,
    planner_model: str,
    coder_model: str,
    acceptance_mode: str = "relative",
    strict_reflection: bool = False,
):
    # Initialize both champions
    champion_a = init_champion_a.read_text()
    champion_b = init_champion_b.read_text()
    champion_a_fitness = -1.0  # Will be measured in gen 0
    champion_b_fitness = -1.0

    for gen in range(generations):
        # Determine whose turn to evolve
        evolving_team = 'A' if gen % 2 == 0 else 'B'

        if evolving_team == 'A':
            # Team A evolves, Team B stays static
            result = dual_llm_generate(
                opponent_source=champion_b,
                opponent_name="champion_b",
                as_team='A',
                # ... other params
            )
            candidate_a = result.cpp_code
            candidate_b = champion_b
        else:
            # Team B evolves, Team A stays static
            result = dual_llm_generate(
                opponent_source=champion_a,
                opponent_name="champion_a",
                as_team='B',
                # ... other params
            )
            candidate_a = champion_a
            candidate_b = result.cpp_code

        # Evaluate match
        fitness_result = evaluate_fitness(
            team_a_src=candidate_a,
            team_b_src=candidate_b,
            n_matches=n_matches,
            seed_base=seed + gen * 1000,
        )

        fitness_a = fitness_result.mean
        fitness_b = -fitness_result.mean  # Team B's perspective

        # Acceptance check
        if evolving_team == 'A':
            if fitness_a > champion_a_fitness - 0.05:
                champion_a = candidate_a
                champion_a_fitness = fitness_a
                accepted = True
            else:
                accepted = False
        else:
            if fitness_b > champion_b_fitness - 0.05:
                champion_b = candidate_b
                champion_b_fitness = fitness_b
                accepted = True
            else:
                accepted = False

        # Write journal entry
        _write_coevolve_journal_entry(
            journal_path=out_dir / "journal.jsonl",
            generation=gen,
            evolving_team=evolving_team,
            fitness_a=fitness_a,
            fitness_b=fitness_b,
            champion_a_fitness=champion_a_fitness,
            champion_b_fitness=champion_b_fitness,
            accepted=accepted,
            tactic_spec=result.tactic_spec,
        )

        # Save champion snapshots
        (out_dir / f"gen_{gen:04d}" / "champion_a.cpp").write_text(champion_a)
        (out_dir / f"gen_{gen:04d}" / "champion_b.cpp").write_text(champion_b)
```

### File: `scripts/fitness.py` (MODIFY)

Add support for evaluating Team B fitness:

```python
def evaluate_fitness(
    team_a_src: str | Path,
    team_b_src: str | Path,
    n_matches: int = 100,
    seed_base: int = 0,
    return_both_perspectives: bool = False,  # NEW
) -> FitnessResult | tuple[FitnessResult, FitnessResult]:
    """
    Evaluate fitness of team_a vs team_b.

    If return_both_perspectives=True, returns (result_a, result_b)
    where result_b.mean = -result_a.mean (zero-sum)
    """
    # ... existing logic ...

    if return_both_perspectives:
        result_b = FitnessResult(
            mean=-result.mean,
            std_err=result.std_err,
            wins=losses,
            draws=draws,
            losses=wins,
        )
        return result, result_b
    else:
        return result
```

---

## Success Metrics

**After 50 generations of co-evolution:**

1. **Both teams improve:**
   - Final `champion_a_fitness` > initial fitness
   - Final `champion_b_fitness` > initial fitness

2. **Arms race evidence:**
   - Fitness oscillates (not monotonic)
   - New tactics emerge in response to opponent

3. **Higher ceiling:**
   - Final champion (A or B) beats M22 gen 33 in head-to-head

4. **Tactical diversity:**
   - ≥5 distinct tactical paradigms discovered
   - Shannon entropy of tactic tags > 4.0

---

## Cost Estimate

**Alternating co-evolution (50 gens):**
- Planner: 50 gens × $0.04 = $2.00
- Coder: 50 gens × $0.01 = $0.50
- **Total: $2.50** (same as single-team evolution)

**Simultaneous co-evolution (50 gens):**
- Planner: 100 calls × $0.04 = $4.00
- Coder: 100 calls × $0.01 = $1.00
- **Total: $5.00**

---

## Conclusion

**Recommendation:** Implement **Option 1 (Alternating Evolution)** as `scripts/evolve_coevolve.py`.

**Why this fixes the ceiling:**
- pursuit_v1 is static → Team A hits 100% win rate → stuck
- Co-evolution: opponent adapts → Team A must improve → both improve
- Creates open-ended optimization pressure

**Next steps:**
1. Implement `evolve_coevolve.py` (~2-3 hours)
2. Run 50-gen pilot with M22 gen 33 vs pursuit_v1 (~$2.50)
3. Analyze dynamics (fitness oscillations, tactic evolution)
4. Scale to 100+ generations if promising

This should break through the ceiling and enable continuous improvement!
