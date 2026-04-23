# Next Phase Engineering Plan: Telemetry → Cognitive Loop

**Scope:** everything required to turn the current M0–M14 infrastructure
into a system that can deliver the research described in
[RESEARCH_PLAN.md](RESEARCH_PLAN.md). Roughly covers milestones M15–M20.

**Status:** design document. No code yet. This plan exists to surface
the non-obvious risks in the naïve "just write `scripts/telemetry_aar.py`"
framing and to produce a dependency-ordered list of deliverables.

---

## 0. TL;DR

The proposed next step is to build `scripts/telemetry_aar.py` that
ingests `trace.jsonl` and emits a natural-language After-Action
Report for the LLM.

**This cannot be built as specified today.** The current trace format
(`docs/trace_schema.json`) records only per-drone `{id, x, y, cooldown,
alive}` per tick. It does **not** record `target_id`, `message_out`,
or velocity. Therefore:

- **Focus-fire / wasted-cooldown counting is impossible** from the
  trace alone — we cannot know who shot at whom.
- **Message-bus utilization is impossible** — the `message_out[4]`
  array is never persisted.
- **Kiting / offensive posture attribution is impossible** — we do not
  know which drone initiated an attack, only that a cooldown was set
  or a death occurred.

The AAR script is therefore blocked on **M15a: extend the trace
format to record actions**, which is an engine + schema change with
determinism, file-size, and GPU-path implications. The AAR script
itself (M15b) then becomes straightforward.

A second addition from the owner: a **persistent learning journal**
per lineage, where the LLM records what tactics it tried, what
worked, what didn't, and reads its own prior entries when mutating
the next generation. This is landed as **M15c** after M15b,
because the journal's one defence against self-flattery is
grounding every entry in the M15b AAR metrics.

This document walks through the full gap analysis, an explicit
questions-for-the-owner list, the revised milestone breakdown
(M15a → M15b → **M15c** → M16 → M17 → M18 → M19 → M20), and the
acceptance criteria for each.

---

## 1. Gap Analysis: What the Trace Does Not Say

### 1.1 What `trace.jsonl` contains today

Per tick, one JSON object:

```json
{"tick": 47, "team_a": [{"id":0,"x":123.45,"y":678.90,"cooldown":3,"alive":true}, ...],
 "team_b": [...], "outcome"?: "TEAM_A_WIN"}
```

Positions are rounded to 2 decimals. Cooldown is an integer. `alive`
is a boolean. No attack events, no messages, no velocities, no
`target_id`.

### 1.2 What the proposed AAR requires

| AAR metric | Data requirement | Available today? |
|---|---|---|
| Win/Loss/Draw | `outcome` on last line | ✅ Yes |
| Final survivor counts | `alive` on last tick | ✅ Yes |
| Match duration | `tick` on last line | ✅ Yes |
| Wasted cooldowns / focus-fire | Per-tick `{attacker_id, target_id, hit?}` | ❌ Not recorded |
| Friendly clustering | Positions across ticks | ✅ Yes (derivable) |
| Message-bus usage / entropy | Per-tick `message_out[4]` per drone | ❌ Not recorded |
| Kiting score (enemy attacks from distance) | Attack events with positions | ⚠️ Indirect (inferable) |
| Cooldown utilization | Per-tick cooldown series | ✅ Yes |

### 1.3 What is *partially* inferable

- **Attack events** can be reconstructed heuristically: a drone whose
  cooldown goes from 0 to `max_cooldown` in one tick fired that tick.
  The target is whichever enemy died within `disable_range` and was
  alive the previous tick. **This is ambiguous under focus-fire** —
  exactly the case we want to measure. It is also fragile under
  mutual-destruction ties.
- **Kiting** can be inferred from enemy velocity direction relative
  to our centroid at the moment of their attack, but "their attack"
  is itself heuristic.

**Conclusion:** inferential attribution will produce noisy, biased
metrics that systematically under-count focus-fire (because the
heuristic cannot distinguish simultaneous shots). Since focus-fire is
one of the two named RQ2 signals, this is not acceptable for a
peer-reviewed paper.

---

## 2. Open Questions (Owner Input Needed)

These decisions cannot be made unilaterally and block implementation.
I'll proceed with the defaults marked **(D:…)** unless overridden.

1. **Trace format: extend in place or add a sidecar?**
   (D: **extend in place**, add optional `actions` array per tick. Schema
   version bump to 2; visualizer kept backwards-compatible; AAR and
   analysis tooling require v2.)

2. **Attack event recording: reconstruct or log directly?**
   (D: **log directly from engine** in the combat phase — cheap, exact,
   and removes the ambiguity under focus-fire. Adds an `attacks`
   array to each tick record.)

3. **Message bus recording: log full 4-float vectors or just entropy?**
   (D: **log full vectors**. File-size hit is ~10 % worst-case at
   `num_drones=10`, trivially compressible by gzip. Entropy can be
   recomputed later; raw vectors cannot.)

4. **Observation scope: what does each side "see"?**
   The AAR is per-side. A Team-A AAR should not leak enemy cooldowns
   (because the game hides them). But the AAR *summarizes* data the
   AI didn't have at decision time. Question: do we redact enemy
   cooldowns from the AAR to preserve the game's information
   asymmetry, or show them because the AI is already dead and
   "reading the post-mortem"?
   (D: **show everything in the AAR** — it is strictly a learning
   signal, not a state observation. The AI's runtime ABI is
   untouched.)

5. **AAR determinism vs. LLM-as-judge enrichment.**
   A pure deterministic AAR is reproducible. An LLM-enriched
   narrative ("the enemy appeared to be using a pincer formation") is
   vivid but non-reproducible.
   (D: **two-layer AAR**: deterministic structured block (JSON +
   rendered markdown) always; optional LLM-judge layer behind a
   feature flag, off by default in the paper's main results.)

6. **Trace size budget.**
   At `num_drones=20`, 1000 ticks, v2 schema (positions + cooldowns
   + messages + actions), uncompressed line is ~1.5 KB × 1000 ≈ 1.5
   MB. Track C (~600 generations × 3 seeds × say 10 evaluation
   matches per generation) = 18 000 traces = ~27 GB.
   (D: **gzip on the fly**, emit `trace.jsonl.gz`; keep `trace.jsonl`
   supported for single-run debugging.)

7. **How does AAR feed into evolve.py — full text injection or
   structured?**
   The next-step prompt says "inject the AAR string directly". But
   LLMs benefit from both a structured machine-readable block and a
   prose summary. Prompt length also matters (RQ3 correlates
   reflection quality with fitness — we must not let prompt bloat
   distort that).
   (D: **both**: Markdown prose (~1 KB) followed by a fenced JSON
   block (~1 KB). Token budget: ≤ 1000 tokens of AAR.)

8. **Which side is the "you" in the AAR?**
   Evolution always has a protagonist (the model under training).
   (D: **the AAR is parameterized by `perspective: "A" | "B"`**. All
   pronouns and first-person metrics resolve relative to that side.)

9. **Should we also AAR *wins*?**
   Losing games need diagnosis. Winning games need "what held up"
   so the next generation doesn't regress.
   (D: **yes, symmetric AARs for win/loss/draw**, with tone
   differences noted in the template.)

10. **Multi-match fitness already exists (M9). Do we AAR per match or
    aggregate?**
    (D: **both**. Per-match AARs for a bounded sample (e.g. best
    loss + worst loss + median match) plus an aggregate summary over
    all evaluation matches. This keeps prompt size bounded while
    still surfacing variance.)

11. **Learning-journal scope: per seed, per track, or global per model?**
    A journal tied to a single (track, model, seed) lineage keeps
    experimental conditions independent; a global journal would let
    Track C benefit from Track A experience, confounding RQ3.
    (D: **per (track, model, seed)**. No cross-seed or cross-track
    bleed. `data/runs/<track>/<model>/<seed>/journal.jsonl`.)

12. **Does the LLM author journal entries, or does a rule-based
    summarizer?**
    (D: **LLM authors, orchestrator validates**. The journal is
    meaningful precisely because it contains self-assessment the
    AAR cannot produce mechanically. Validation grounds it: cited
    metrics must match the AAR.)

13. **Does the LLM see the opponent's journal in Track C?**
    (D: **no**. Each side sees only its own journal, matching the
    runtime information asymmetry and preventing copy-cat collapse.)

14. **Stall generations (compile-failed, lint-failed) — do they get
    journal entries?**
    (D: **yes, with `fitness_delta: null` and `verdict: "stalled"`.**
    Failure modes are the most valuable learning signal; hiding
    them would create survivorship bias in the journal.)

15. **Journal retention when an evolutionary run is resumed from a
    checkpoint.**
    (D: **journal is the checkpoint-of-truth**. Resuming reconstructs
    the in-memory state from `journal.jsonl` + the last code
    artefact. Deleting the journal resets the lineage.)

---

## 3. Revised Milestone Breakdown

Replaces §6 of RESEARCH_PLAN.md with dependency-ordered engineering
milestones. Each has explicit acceptance criteria.

### M15a — Trace Schema v2: Action & Message Recording
**Blocker for everything downstream.**

- **Engine changes** (`src/engine.cpp`):
  - After the query phase, before the combat phase, capture each
    alive drone's `Action` (target_id, velocity, message_out) into
    a per-tick scratch buffer.
  - In the combat phase, record each resolved attack as
    `{attacker_team, attacker_id, target_team, target_id, hit}`.
  - Extend `write_trace_line` to emit optional `actions` and
    `attacks` arrays. Guard behind a `--record-actions` flag so the
    M11 GPU benchmark path stays zero-overhead.
- **Schema changes** (`docs/trace_schema.json`):
  - Bump `schema_version` to 2. Add `actions[]` and `attacks[]`
    array definitions, both optional for v1 compatibility.
- **Visualizer** (`scripts/visualizer.py`):
  - Read the new fields if present, ignore otherwise. No visual
    change required in this milestone.
- **Determinism:** the enriched trace must remain byte-identical
  across reruns for a fixed seed. Add a CI test.
- **Performance:** benchmark mode (`--benchmark`) must not regress
  (verify with M11 harness). The `--record-actions` path may cost
  up to 10 % without alarm.
- **Acceptance:**
  1. Existing traces continue to parse (backwards compat).
  2. A round-trip test compiles a trivial AI, runs a match with
     `--record-actions`, and asserts every attack visible in the
     trace matches a cooldown transition in the state snapshot.
  3. File-size regression < 3× the v1 trace for `num_drones=10`.

### M15b — `scripts/telemetry_aar.py`
Only meaningful after M15a.

- **Inputs:** `--trace PATH`, `--perspective {A,B}`,
  `--format {markdown,json,both}`, `--max-tokens INT` (soft cap).
- **Derived metrics** (deterministic, documented formulas):
  - `outcome`, `ticks`, `alive_final_{us,them}`.
  - `shots_fired_{us,them}` — count of attack events per side.
  - `shots_hit_{us,them}` — where `hit: true`.
  - `focus_fire_redundancy` — extra attackers on the same
    `(tick, target_team, target_id)` beyond the first. Directly
    answers "wasted cooldowns".
  - `cooldown_utilization_{us,them}` — fraction of alive-ticks with
    `cooldown == 0`.
  - `mean_pairwise_distance_{us,them}(t)` → averaged over ticks;
    plus dispersion index (std / mean).
  - `message_bus_used` — any non-zero message vector? Any
    cross-drone variance?
  - `message_bus_entropy` — Shannon entropy of quantized messages
    (8 buckets per float, joint over drones).
  - `kiting_score_them` — fraction of their attacks launched from
    distance > 0.8 × `disable_range`.
  - `engagement_range_mean` — mean distance at attack resolution.
- **Exports a Python API:** `render_aar(trace_path: Path, *,
  perspective: str, fmt: str="both") -> AARReport` where
  `AARReport` bundles `structured: dict`, `markdown: str`,
  `token_estimate: int`.
- **Not allowed:** any metric that requires inference beyond the
  recorded events. If M15a is incomplete, M15b fails loudly rather
  than silently estimating.
- **Tests** (`tests/test_telemetry_aar.py`):
  - Handcrafted 20-tick trace fixture with known events → asserts
    each derived metric exactly.
  - Baseline replays: `pursuit_v1` vs `stationary_v1` → AAR should
    mark `stationary_v1` perspective as 100 % shots-missed or 100 %
    wipe-out, depending on direction.
  - Determinism: AAR JSON bit-identical across repeated calls.
- **Acceptance:**
  1. `python3 scripts/telemetry_aar.py --trace fixture.jsonl
     --perspective A` writes markdown to stdout and structured JSON
     to a sidecar.
  2. `from telemetry_aar import render_aar` works without side
     effects.
  3. Prompt-token estimate is within 15 % of `tiktoken` ground
     truth for a `num_drones=10` trace.

### M15c — Learning Journal (Long-Term Self-Reflection Memory)
Depends on M15b.

**Motivation.** The M15b AAR is hot, reactive, and per-generation. It
answers *what happened this match*. The learning journal answers
*what have I learned across my entire lineage* — which tactics have
been tried, which failed, which strategic themes were promising but
abandoned. Without it, monotonic self-play (Track B) cannot
diagnose regressions where gen 50 rediscovers the same failure mode
gen 12 already encountered.

**Key design principle.** The LLM authors journal entries but the
orchestrator **validates them against the AAR before accepting**.
Metric citations that don't match the AAR numbers are rejected and
the model is told so — this prevents self-flattering hallucinated
narratives ("I elegantly pivoted to a pincer formation") from
polluting its own long-term memory.

#### 3.X.1 Storage layout
- `data/runs/<track>/<model>/<seed>/journal.jsonl` — append-only.
  One line per generation, including stall generations.
- Scope: strictly per-lineage. No cross-seed, cross-track, or
  cross-model bleed (see §2 Q11).
- Resume semantics: `journal.jsonl` + the last committed
  `candidate.cpp` are the canonical checkpoint.

#### 3.X.2 Entry schema (`docs/journal_schema.json`, new)
```json
{
  "generation": 17,
  "timestamp_utc": "2026-04-23T20:11:44Z",
  "parent_generation": 16,
  "track": "A",
  "model": "claude-opus-4-7",
  "seed": 42,
  "status": "ok",
  "fitness": 0.42,
  "fitness_delta": -0.12,
  "outcome_summary": "lost 8/10 evaluation matches vs pursuit_v1",
  "hypothesis_tested": "tight formation to overwhelm at close range",
  "mechanism_expected": "concentrated fire wins exchanges",
  "mechanism_observed": "formation clustered correctly but died to focus-fire",
  "verdict": "rejected | confirmed | partial | stalled",
  "tactic_tags": ["tight_formation", "close_range",
                   "focus_fire_vulnerability"],
  "advice_to_future_self": "avoid mean_pairwise_distance < 40 vs pursuit",
  "aar_metrics_cited": {
    "focus_fire_redundancy": 0.58,
    "mean_pairwise_distance_us": 12.3,
    "cooldown_utilization_us": 0.31
  },
  "validation": {
    "metrics_match_aar": true,
    "schema_valid": true,
    "rewrites": 0
  }
}
```

`verdict` taxonomy:
- `confirmed` — hypothesis worked, fitness improved.
- `partial` — fitness improved but not by the expected mechanism.
- `rejected` — hypothesis did not hold; fitness flat or worse.
- `stalled` — generation failed to produce a viable candidate
  (compile / lint / inject failed). `fitness` and `fitness_delta`
  are `null`.

#### 3.X.3 Write path (authored by LLM, validated by orchestrator)
After a generation lands (or stalls):
1. Orchestrator renders M15b AAR.
2. Orchestrator prompts the LLM with: the AAR, the previous-
   generation journal entry, the current generation's reflection
   block, and the schema. Asks for a single JSON entry.
3. Orchestrator validates:
   - **Schema:** conforms to `journal_schema.json`.
   - **Metric grounding:** every `aar_metrics_cited` key exists in
     the AAR with values within 1 % relative tolerance.
   - **Field sanity:** `verdict` is in enum; `tactic_tags` are
     lowercase snake_case, ≤ 6 tags.
4. On failure, orchestrator sends a single "rewrite with these
   corrections" message, up to 2 retries. After 2 failures, the
   entry is written with `validation.metrics_match_aar: false` and
   a deterministic fallback summary replaces the prose fields.
   The LLM is not granted unlimited rewrite attempts — this keeps
   the per-generation API budget bounded.

`scripts/journal.py` exposes:
- `append_entry(path, entry, aar) -> ValidationResult` (validates
  then writes).
- `read_entries(path) -> list[Entry]` (ordered).
- `recall(path, *, recency_k=3, extremes_k=3, tag_overlap=0.3,
          max_entries=10, max_bytes=3000) -> list[Entry]`
  (deterministic retrieval — see §3.X.4).
- `render_for_prompt(entries) -> str` (Markdown + structured
  JSON, token-capped at ~1500).

#### 3.X.4 Retrieval strategy (read path)
Deterministic, embedding-free. For each new generation N we select
up to `max_entries` from prior journal entries:
1. **Recency.** Always include the last `recency_k` entries.
2. **Extremes.** Top `extremes_k` entries by `|fitness_delta|`.
3. **Tag overlap.** Any entry whose `tactic_tags` overlap with the
   *current* planned direction (inferred from the current
   generation's reflection block, keyword-matched against prior
   tags) at ≥ `tag_overlap` Jaccard.
4. **Stall inclusion.** At least one stall entry if any exist in
   history (so the model is reminded of known-broken patterns).
5. **Cap.** Truncate to `max_entries` / `max_bytes` by dropping the
   least-recent non-extreme non-stall entries first.

Embeddings are explicitly *avoided* to keep the pipeline
deterministic and auditable for the paper. The trade-off is
acknowledged in §7 Risks.

#### 3.X.5 Fairness across models (RQ1 integrity)
The journal is injected into *all* models' prompts under identical
policy. Because Claude's prose may be more eloquent than Gemini's,
a raw-text journal confounds "writes better prose" with "writes
better code". Mitigations:
- Prompt-template requires **structured fields** first; prose
  advice is length-capped (≤ 400 chars).
- Evaluators performing qualitative RQ3 analysis are blinded to
  `model` when reading journal prose.
- The validation step normalizes `tactic_tags` to a controlled
  vocabulary (new tags allowed but canonicalised case/spacing).

#### 3.X.6 New metrics enabled
Recorded per generation in the aggregation layer:
- **Recall ratio.** Fraction of the current entry's `tactic_tags`
  that also appear in any prior entry. High recall = the model is
  building on prior experience; low recall = the model is
  forgetting / rediscovering.
- **Recall hit rate.** Among the `recall()`-selected entries
  injected into the current prompt, what fraction are referenced
  (by substring or tag) in the current journal entry / reflection?
- **Journal entropy over time.** Shannon entropy of the
  `tactic_tags` distribution in a sliding 10-generation window.
  Falling entropy = tactical convergence (possibly collapse).
- **Self-correction events.** Consecutive pair `(N-1, N)` where
  gen N's `hypothesis_tested` explicitly negates gen N-1's verdict.

These plug directly into RQ2 (emergence), RQ3 (reflection vs.
execution), and RQ4 (evolutionary stalls).

#### 3.X.7 Acceptance criteria
1. `scripts/journal.py append_entry` rejects entries whose
   `aar_metrics_cited` differ from the AAR; rewrite loop caps at 2.
2. `recall()` is deterministic: identical input → identical output
   byte-for-byte.
3. Journal survives a mid-run kill: replaying from
   `journal.jsonl` + `candidate.cpp` reproduces the next prompt
   exactly.
4. Unit tests on a 30-entry fixture journal verify recency,
   extremes, tag-overlap, and cap policies.
5. Integration test: 5-generation `evolve.py` run against a mock
   LLM produces a well-formed 5-line `journal.jsonl` with all
   validation fields set.

### M16 — Evolve Loop Integration
Wire AAR and journal recall into the generation prompt.

- **Changes to `scripts/evolve.py`:**
  - After each evaluation batch (M9 already produces multiple
    matches), pick a representative subset (see §2 Q10) and call
    `render_aar`.
  - Call `journal.recall(...)` to retrieve the selected historical
    entries for this lineage.
  - Concatenate both into the next prompt:
    `<prior_lessons>` (journal) → `<aar>` (current) → code prompt.
  - After generation N's code lands, call
    `journal.append_entry(...)` with the validation loop (M15c).
  - Keep pre-AAR and pre-journal paths behind flags for ablation
    runs (RQ3 will need AAR-on/off × journal-on/off, 2×2 design).
- **Prompt template update** (`prompts/evolve_ai.md`):
  - Add `{AAR}` and `{PRIOR_LESSONS}` placeholders. Both render as
    `(none)` on generation 0.
- **Budget enforcement:** combined prompt capped at 12 k tokens;
  over-budget drops journal first, then AAR per-match sample,
  keeping aggregate AAR and the code skeleton intact.
- **Acceptance:**
  1. Running `evolve.py --generations 3 --aar-on --journal-on`
     produces generation 2 and 3 prompts that contain both blocks
     and reference concrete metrics/tags from earlier entries.
  2. Four-way ablation matrix (`--aar-{on,off} --journal-{on,off}`)
     each produces distinct, reproducible prompt streams.
  3. Mid-run kill + resume reproduces the next prompt exactly from
     `journal.jsonl` + `candidate.cpp` alone.

### M17 — Reflection Rubric Scorer
Needed for RQ3 (reflection vs. execution correlation).

- **`scripts/reflection_score.py`:** LLM-as-judge scorer with a
  frozen rubric:
  - 1–5 on causal diagnosis of prior loss.
  - 1–5 on specificity of counter-tactic.
  - 1–5 on feasibility within the ABI (judge is shown `ai_abi.h`).
- **Calibration:** 50 hand-scored reflections as a held-out set;
  report inter-rater agreement (human vs. judge, and judge-A vs.
  judge-B where judge is a *different* model).
- **Output:** one row per generation in
  `data/runs/<track>/reflection_scores.csv`.
- **Acceptance:** Cohen's κ ≥ 0.5 between judge and human on the
  calibration set. If not, downgrade to a rule-based heuristic
  scorer and document the limitation.

### M18 — Tactic Detector
Needed for RQ2 (emergent coordination) and the co-evolution
counter-tactic lag in Track C.

- **`scripts/tactic_detector.py`:** deterministic feature
  extractor over v2 traces. Detects:
  - **Flanking:** two ally clusters with ≥ 90° angular separation
    from enemy centroid sustained ≥ 50 ticks.
  - **Kiting:** sustained retreat velocity aligned with the enemy
    centroid-to-ally vector, ≥ 100 ticks.
  - **Focus-fire discipline:** dead-enemy → simultaneous shot
    ratio below a threshold.
  - **Message-coded targeting:** mutual information between a
    drone's `target_id` and some message bus channel (requires
    M15a message logging).
- **Output:** `tactic_events.jsonl` — one row per first-appearance
  per (track, model, seed, generation).
- **Acceptance:** each detector fires on a known fixture replay
  and does not fire on a handcrafted counter-example.

### M19 — Track Runners
Thin drivers over `evolve.py` to encode the three experimental
setups.

- `scripts/tracks/track_a.py` — LLM vs. `pursuit_v1` for N
  generations, fixed system prompt, multi-seed.
- `scripts/tracks/track_b.py` — monotonic self-play; Gen N vs.
  Gen N-1; final round-robin via `tournament.py`.
- `scripts/tracks/track_c.py` — A vs. B co-evolution; both
  receive AARs; external-yardstick eval every K generations.
- **Resume semantics:** every run produces a checkpoint allowing
  `--resume data/runs/<track>/<model>/seed42/` to continue from
  the last completed generation. Critical given the multi-day
  wall-clock of Track C.

### M20 — Reproducibility & Budget CI
- **Mini-track smoke tests** in CI: 5-generation Track-A run
  against a frozen mock LLM replaying canned responses. Must be
  byte-identical across reruns.
- **Cost guard:** `experiment_log.py` already tracks token usage;
  add a hard cap per-track that raises if exceeded, to prevent a
  runaway evolutionary loop from bankrupting the API budget.

---

## 4. Dependency Graph

```
  M15a (trace v2) ──┬── M15b (AAR) ── M15c (Journal) ──┐
                    │                                    │
                    ├── M18 (tactic detector)            │
                    │                                    │
                    └── visualizer overlay (optional)    │
                                                          │
                                                          ▼
                       M16 (evolve.py AAR+journal wiring) ─── M19 (tracks) ── M20 (CI)
                                          │
                                          └── M17 (reflection scorer, parallel)
```

M15a is the critical path. M15b unblocks M15c (journal validation
requires AAR metrics to ground against). M16 cannot ship without
both M15b and M15c.

---

## 5. Concrete Deviations from the User's Proposed Step

The user proposed a single deliverable:

> Create a new Python script `scripts/telemetry_aar.py`. It must
> ingest a trace.jsonl file and output a clean, concise, textual
> After-Action Report...

### 5.1 What must change
1. **Split into M15a + M15b.** Writing the AAR before the trace
   contains action events would require noisy heuristics and
   systematically bias RQ2 results. The trace extension is cheap
   (~half a day of engine work) and removes the ambiguity.
2. **Structured output alongside prose.** "Textual AAR" is the
   right LLM-facing surface, but we also need a machine-readable
   structured form for §3 analyses and for the paper's CSVs.
3. **Perspective parameter.** An AAR is always from a side. The
   script must be symmetric.
4. **Redaction / information-asymmetry decision.** See §2 Q4.

### 5.2 What stays the same
- Script lives at `scripts/telemetry_aar.py`.
- Exports an importable function callable from `evolve.py`.
- Output is concise enough to drop directly into a prompt (target
  ≤ 1000 tokens).
- No new runtime dependencies beyond what the orchestrator already
  imports.

---

## 6. Risks Specific to This Phase

- **Silent metric bias.** If M15a ships with a subtle bug (e.g.
  counting self-destruct ties as focus-fire), every downstream RQ2
  number is corrupted. Mitigation: fixture-based metric tests with
  hand-computed expected values.
- **Prompt pollution.** Every AAR token is a token the LLM spends
  on observation instead of reasoning. Too-verbose AARs could
  *reduce* code quality and mask RQ1 differences. Mitigation:
  ablation track (AAR-on vs. AAR-off) on Track A.
- **Model routing.** We already observed the endpoint silently
  aliasing model IDs. RQ1 comparisons across models require
  logging `response.model` (the server-echoed ID), not just the
  request string. This needs to land before any multi-model run.
- **Trace-size blowup.** v2 traces with message recording are ~3×
  larger. Mitigation: gzip by default; integrate into the existing
  `data/runs/` artefact layout with retention policy.
- **Information leakage in the AAR.** If the AAR exposes enemy
  cooldown in a way that the runtime ABI doesn't, fitness comparisons
  across AAR-on/AAR-off become incomparable. Mitigation: §2 Q4
  decision is logged in the paper and the AAR schema, not tunable
  per run.

### 6.1 Risks specific to the learning journal (M15c)

- **Self-justification bias.** LLMs writing self-assessments tend
  to flatter themselves. Mitigation: metric-grounded validation
  (§3 M15c.3) rejects any entry whose cited AAR numbers disagree
  with reality within 1 % tolerance.
- **Context collapse / echo chamber.** Reading your own previous
  journal and writing more of the same can converge the tactical
  vocabulary. Mitigation: track journal entropy over time (§3
  M15c.6); if entropy collapses, escalate to an out-of-band
  diversity prompt in Track B/C. Collapse is itself a *result* worth
  reporting — do not "fix it away" silently during experiments.
- **Deterministic retrieval limits.** Keyword-based tag overlap
  will miss paraphrastic similarity that embeddings would catch
  (e.g. "loose formation" vs. "spread deployment"). Mitigation:
  publish the controlled tag vocabulary; report how often recall
  misses on the fixture set.
- **Fairness.** Prose quality varies between models. Mitigation:
  structured-first prompt, length cap on advice, blinded human
  evaluation for qualitative analysis.
- **Resume correctness.** A corrupted last line (partial write on
  kill) must not poison the journal. Mitigation: append-only with
  `fsync` per line; loader truncates trailing invalid JSON and
  logs the fact.
- **Privacy / data release.** The journal contains LLM prose that
  may inadvertently reveal prompt wording or model quirks. The
  dataset release pipeline (M20) must pass journals through the
  same redaction that the existing `llm_client.redact_secrets`
  applies to prompts.

---

## 7. Suggested First Two Weeks

If this plan is approved, a concrete short-horizon schedule:

| Day | Work | Artefact |
|---|---|---|
| 1 | Engine action-recording, schema v2 draft | branch `m15a/trace-v2` |
| 2 | Determinism + fixture tests for v2 traces | PR: M15a |
| 3 | `telemetry_aar.py` skeleton + metrics lib | branch `m15b/aar` |
| 4 | AAR fixture tests, markdown renderer | PR: M15b |
| 5 | `journal.py` schema + append_entry + validation | branch `m15c/journal` |
| 6 | `journal.recall()` + fixture tests + resume semantics | PR: M15c |
| 7 | `evolve.py` AAR + journal wiring + 2×2 ablation flags | PR: M16 |
| 8 | Reflection rubric + 50-sample calibration | PR: M17 |
| 9 | Tactic detector + fixture replays | PR: M18 |
| 10 | Track A runner, 10-generation smoke run | PR: M19 (A) |
| 11 | Tracks B & C runners, checkpoint/resume | PR: M19 (B, C) |
| 12 | CI mini-tracks + budget guard | PR: M20 |

Track A full 150-generation run can start once M19(A) lands. Tracks
B and C begin after their runners merge. Analysis notebooks and
paper drafting proceed in parallel once data starts flowing.

---

## 8. Open Question Summary (Action Items)

All decisions listed below default to the values in §2. The owner has
indicated "no overrides", so these are the operative decisions for
implementation. Listed here as a single quick-reference checklist.

**AAR / Trace (Q1–Q10):**
- **Q1** extend trace in place (schema v2).
- **Q2** log attack events directly from engine.
- **Q3** log full 4-float message vectors.
- **Q4** AAR shows enemy cooldowns (post-mortem learning signal).
- **Q5** deterministic-only AAR in main paper results.
- **Q6** gzip traces by default.
- **Q7** AAR output format: markdown + structured JSON.
- **Q8** AAR `perspective: "A" | "B"` parameterised.
- **Q9** symmetric AARs for win/loss/draw.
- **Q10** per-match sample + aggregate.

**Learning Journal (Q11–Q15):**
- **Q11** journal per (track, model, seed); no cross-lineage bleed.
- **Q12** LLM authors, orchestrator validates against AAR metrics.
- **Q13** Track C: each side sees only its own journal.
- **Q14** stall generations get entries with `verdict: "stalled"`.
- **Q15** journal is the canonical checkpoint for resume.
