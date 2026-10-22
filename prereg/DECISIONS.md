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
