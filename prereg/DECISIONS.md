# Decisions before the freeze

Design decisions taken before prompt 11, with where the evidence lives.
After the freeze, changes go to `prereg/DEVIATIONS.md` instead.

## 2026-10-22 — after the baseline calibration

Evidence: `reports/calibration/CALIBRATION.md` and the files it cites.

1. **Evaluation size for the pilot: 400 matches** (200 seeds, both side
   assignments). Sizing rule: the smallest size with power >= 0.80 at a
   true difference of 0.1 under every baseline noise model. It gives 200
   matches for match score and 400 for survivor differential; 400 also
   leaves room for LLM-written programs being more decisive than the
   baselines. The pilot re-measures the spread before the size is frozen.
2. **Survivor differential is measured in drones** (own survivors minus
   enemy survivors at the end), not as a share of team size. True
   differences in power analyses are in drones.
3. **Tactic detectors fixed (schema 2) and the AAR metric renamed
   (metrics schema 2).** Schema 1 fired flanking from spawn geometry and
   for a stationary team, could not fire focus-fire discipline or
   message-coded targeting in 10-drone matches, clipped message channels
   to [-1, 1] and counted tick 0's zero-filled actions as targeting; the
   AAR's `kiting_score_them` reported retreat where there was none. New
   definitions are in `scripts/tactic_detector.py`; the metric is now
   `long_range_shot_share_them`. Legacy prompts stay unchanged for arm A1.
4. **E4 grows the arena, and the tick cap grows with it:**
   `fitness.scaled_max_ticks` = ceil(1000 x arena scale). With a fixed
   1000-tick cap, matches at larger arenas were cut off.

## 2026-10-22 — after freezing the M25 loop for arm A1

Evidence: `legacy/m25/CHANGES.md`, `reports/legacy/LEGACY_LOOP.md` and
`tests/test_legacy_m25.py`.

5. **No M22 or M25 run data exists.** The M22 gen-33 champion (E1's fixed
   opponent, C3's Team A start) and all earlier run data are gone; nothing
   from them can be audited or reused. `legacy/m25/` is characterised from
   its code and golden prompts instead, with `cluster_v1` standing in for
   the opponent. The replacement for M22 gen 33 in E1 and C3 is still open.
6. **A1 runs with `--rounds 122`** in fixed-opponent mode (`--evolve-team B`):
   Team B's round 1 evaluates the start program, then 60 LLM rounds
   (rounds 3 to 121), the same LLM-round count as the other E1 arms.
7. **A1's execution changes are approved:** matches in the eval container
   with its compiler and flags (g++ 11.4, `-ffp-contract=off`); a container
   infrastructure failure ends the run instead of stalling the round; the
   injected client may replace the loop's `max_tokens=4096` with the
   arm-wide setting.
8. **A1 can resume** from its last completed round (`--resume`, execution
   change 4). An interrupted round is kept under `interrupted/` and re-run;
   its plan and code are reused when they were saved.

## 2026-10-23 — replacing M22 gen 33

9. **E1's fixed opponent will be evolved, then frozen.** One dedicated
   evolution run produces it before the freeze (prompt 11); its final
   champion becomes the opponent for every E1 arm. The run needs paid calls,
   so it waits for the hardened client and the budget governor, and its plan
   (loop, start program, training opponent, rounds, models, effort, seeds,
   cost cap and the rule that picks the champion) is put to the user before
   it starts. Known threat to record in the paper: the opponent comes from
   an LLM evolution loop, so it is not independent of the arms that use
   one; its run uses seeds disjoint from every training and held-out seed.
10. **C3 is redefined as a partial M25 replication:** Team A starts from the
    new E1 opponent, Team B from `pursuit_v1`; full loop; 5 seeds. It is
    reported as a partial replication, not a replication of M25.
11. **The held-out panel is `pursuit_v1`, `cluster_v1` and 3 to 5
    human-written bots.** The Track A champions are gone. The E1 opponent
    and its lineage stay out of the panel for any run trained against them.

## 2026-10-23 — AI-written opponents (supersedes 9 and 11, amends 10)

12. **AI models from other families write the opponents.** No person, no
    Claude model and not the study's evolution loop writes any opponent or
    panel bot. `bots/generation/PROTOCOL.md` and
    `bots/generation/design_prompt.md`, fixed before any bot is generated
    and hashed in the brief, pick R1 (E1's opponent), the panel bots and a
    same-author set; prompt 8a runs them. This replaces entry 9 (an evolved
    opponent) and entry 11 (human-written panel bots). C3 (entry 10) starts
    Team A from R1 and is descriptive. The held-out panel is `cluster_v1`
    plus the panel bots; R1 joins it in C1 and C2. Seeds 7,000,000 to
    7,999,999 belong to bot generation.
