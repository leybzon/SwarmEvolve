# SwarmEvolve v2 study: research brief
Question: which ingredients of an LLM evolution loop produce cumulative improvement beyond best-of-N sampling when the opponent fights back?
Target: IEEE Computational Intelligence Magazine plus an arXiv preprint. Pre-registered; confirmatory numbers come only from frozen code on the canonical platform.

Known defects in the M22/M25 harness (verify each):
D1 No inheritance: scripts/dual_llm.py prompts carry the opponent's source but never the evolving team's own champion code.
D2 AAR never delivered: scripts/evolve_coevolve.py and scripts/evolve_dual.py read trace_sample.jsonl, which nothing writes, so the planner always saw '(none - first generation)'.
D3 Stale acceptance bar: evolve_coevolve.py never re-measures a champion after the opponent changes.
D4 Prompt-seeded tactics: prompts/planner_analyze_aar_v2.md suggests formation control, spreading and bait/striker roles.
D5 Scoring loophole: scripts/fitness.py scores a crash or timeout 0, the same as a draw, and per-match results are not saved.
Also: 10 matches per evaluation, a single run, new seeds every round, LLM-written code run on the host. The M25 planner (claude-sonnet-4-20250514) was retired on June 15, 2026.

Hypotheses
H1 Inheritance: showing the parent's code raises the final champion's held-out score over fresh rewrites (A2 > A0).
H2 Grounded feedback: delivering the AAR adds improvement (A3 > A2) and raises the share of planner-cited metrics that match the AAR.
H3 Opponent access: seeing the opponent's source raises the score against that opponent (A3 > A4) but lowers held-out generalization (A4 > A3).
H4 Co-evolution: co-evolution with an archive generalizes better than a fixed opponent at equal LLM rounds per team (C2 > A3 at 50 rounds).
H5 Null: the full loop beats best-of-N (A3 > A0), and its acceptance pattern differs from best-of-N's.

Experiments (default sizes until the pilot in prompt 10)
E1 Fixed opponent: the M22 gen-33 champion; start program pursuit_v1; 10 seeds x 60 rounds per arm.
  A0 Best-of-N: neutral prompt, no parent, no AAR, no lessons, the same acceptance rule as A2 to A4.
  A1 Legacy: the M25 loop as it ran (v2 prompt, no parent, no AAR, 10 matches, legacy acceptance) with current models.
  A2 Parent: neutral prompt plus the parent's code.
  A3 Full loop: neutral prompt, the parent's code and the AAR.
  A4 Black-box: A3 with the opponent's source withheld.
E2 Co-evolution: 100 rounds, 50 per team.
  C1 Both teams start from pursuit_v1; full loop; no archive; 10 seeds.
  C2 C1 plus a hall-of-fame archive; 10 seeds.
  C3 M25 replication: A starts from M22 gen 33, B from pursuit_v1; full loop; 5 seeds.
E3 Model generality: A0 and A3 on one other frontier model and one open-weight model, each model playing both roles; 5 seeds x 60 rounds. Fixed in advance, not chosen after E1.
E4 Swarm size: final champions evaluated at 5, 10, 20 and 50 drones per team; no LLM calls.

Evaluation rules
- All matches run in the canonical container. Candidate and champion play the same seeds, each with both side assignments; default 400 matches per evaluation.
- Compile or guard failures, crashes and timeouts are losses for the program under test.
- Acceptance in every arm except A1: mean paired difference above 0 and a one-sided sign-flip permutation p below 0.10.
- Held-out panel: pursuit_v1, cluster_v1, the Track A champions (seed 42 gen 91, seed 43 gen 106, seed 44 gen 36) and 3 to 5 human-written bots. A run never counts its own lineage or its training opponent, and held-out seeds never overlap training seeds.

Measures
Primary: the final champion's held-out panel score after a fixed number of rounds; interquartile mean across seeds with stratified bootstrap 95% CIs; six Holm-corrected tests (H1, H2, H3 twice, H4, H5).
Secondary: held-out score against LLM calls and dollars; score against the training opponent; survivor differential; CIAO plots, a master tournament and dominance cycles for E2; parent-child similarity; planner citation accuracy; stall categories; cost per unit of held-out gain.
Emergence: a tactic counts only if a trace detector finds it in held-out matches, it is absent at round 0 and from best-of-N round-0 samples, no prompt sent in that run names it, and it recurs in at least 3 of 10 seeds.

M25 claims retired until re-tested: the 66-line underdog beat the champion; co-evolution was 35% faster; Formation Spread emerged spontaneously; a Red Queen effect with 47 rejected mutations; a power law with alpha near 1.5; r near 0.92 for lines of code against fitness; one consumer GPU; the AlphaStar comparison.
