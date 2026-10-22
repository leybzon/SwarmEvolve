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
