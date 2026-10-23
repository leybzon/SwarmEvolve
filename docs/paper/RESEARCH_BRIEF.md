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
E1 Fixed opponent: R1 (see Reference opponents); start program pursuit_v1; 10 seeds x 60 rounds per arm.
  A0 Best-of-N: neutral prompt, no parent, no AAR, no lessons, the same acceptance rule as A2 to A4.
  A1 Legacy: the M25 loop as it ran (v2 prompt, no parent, no AAR, 10 matches, legacy acceptance) with current models; the frozen copy in legacy/m25/, run with --evolve-team B and --rounds 122.
  A2 Parent: neutral prompt plus the parent's code.
  A3 Full loop: neutral prompt, the parent's code and the AAR.
  A4 Black-box: A3 with the opponent's source withheld.
E2 Co-evolution: 100 rounds, 50 per team.
  C1 Both teams start from pursuit_v1; full loop; no archive; 10 seeds.
  C2 C1 plus a hall-of-fame archive; 10 seeds.
  C3 Underdog start: Team A starts from R1, Team B from pursuit_v1, on the fixed harness so Team A can adapt; full loop; 5 seeds. Descriptive, not one of the six tests.
E3 Model generality: A0 and A3 on one other frontier model and one open-weight model, each model playing both roles; 5 seeds x 60 rounds. The other frontier model comes from a family that did not write R1. Fixed in advance, not chosen after E1.
E4 Swarm size: final champions evaluated at 5, 10, 20 and 50 drones per team; no LLM calls.

Reference opponents
- The M22 gen-33 champion, the Track A champions and all other earlier run data are lost. The study uses no data from earlier runs; the M25 case study comes from legacy/m25/ and arm A1.
- AI models from other families write the opponents. No person, no Claude model and not the study's evolution loop writes any opponent or panel bot, and a fixed procedure picks them before any arm runs: bots/generation/PROTOCOL.md, with the prompt bots/generation/design_prompt.md (authors gemini-4-argon and gpt-6.1-sol; $25 cap). Prompt 8a runs it.
- R1, the E1 opponent: the top-ranked bot under the protocol (§4 ranking, §5 selection) that beats pursuit_v1 and cluster_v1 with a mean score of at least 0.8 against each, over 1,000 seeds with both side assignments. It is frozen with its hash before any pilot run.
- Panel bots: the protocol's 3 top-ranked bots from each family other than R1's. The next 3 bots from R1's family form a same-author set, used only for an exploratory near-transfer check and never in the panel.
- Frozen procedure (2026-10-23): bots/generation/PROTOCOL.md sha256 8b740b31db9abdef99ebedf079c14cc764a944fd66197c71b51af7f07cf45d8c; bots/generation/design_prompt.md sha256 366ba3a65a8b62670fa00ab8d282d14231b03c5ad53bf9b7039cdb8b864403ff.

Evaluation rules
- All matches run in the canonical container. Candidate and champion play the same seeds, each with both side assignments; default 400 matches per evaluation.
- Compile or guard failures, crashes and timeouts are losses for the program under test.
- Acceptance in every arm except A1: mean paired difference above 0 and a one-sided sign-flip permutation p below 0.10.
- Held-out panel: cluster_v1 plus the protocol's panel bots, none of them from R1's author family; R1 joins the panel in C1 and C2. Results are reported per bot and pooled, and for arms driven by a model of family F (E3) also without F-authored bots. A run never counts its own lineage or its training opponent, and held-out seeds never overlap training seeds or the protocol's seed block (7,000,000 to 7,999,999).

Measures
Primary: the final champion's held-out panel score after a fixed number of rounds; interquartile mean across seeds with stratified bootstrap 95% CIs; six Holm-corrected tests (H1, H2, H3 twice, H4, H5).
Secondary: held-out score against LLM calls and dollars; score against the training opponent; survivor differential; CIAO plots, a master tournament and dominance cycles for E2; parent-child similarity; planner citation accuracy; stall categories; cost per unit of held-out gain.
Emergence: a tactic counts only if a trace detector finds it in held-out matches, it is absent at round 0 and from best-of-N round-0 samples, no prompt sent in that run names it, and it recurs in at least 3 of 10 seeds.

M25 claims retired until re-tested: the 66-line underdog beat the champion; co-evolution was 35% faster; Formation Spread emerged spontaneously; a Red Queen effect with 47 rejected mutations; a power law with alpha near 1.5; r near 0.92 for lines of code against fitness; one consumer GPU; the AlphaStar comparison.
