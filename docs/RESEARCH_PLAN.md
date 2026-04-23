# Research Plan: LLM Self-Evolution in Adversarial Swarm Environments

**Proposed paper title:** *Code as Tactics: Benchmarking LLM Evolutionary
Velocity and Emergent Multi-Agent Coordination in GPU-Accelerated
Environments*

**Status:** Pre-registration draft. Updated as instrumentation lands
on top of the M0–M14 engine baseline.

---

## 1. Core Research Questions

Each RQ is stated as a falsifiable hypothesis so results can be
published whether or not they confirm the prior.

### RQ1 — Evolutionary Velocity
Which foundational LLM demonstrates the steepest learning curve (Elo
gained per generation) when tasked with optimizing multi-agent C++
logic?

- **Candidate models:** Claude (3.5 Sonnet, 3 Opus or successors
  available on the endpoint), Gemini 1.5 Pro, GPT-4o.
- **Primary metric:** slope of Elo over generation index, fit on the
  first 30 generations of Track A.
- **Secondary metric:** absolute Elo of the generation-50 champion.
- **Null hypothesis H0₁:** all three models have indistinguishable
  velocity slopes within a 95 % bootstrap CI.

### RQ2 — Emergent Coordination
Under adversarial pressure, can LLMs spontaneously invent and encode
communication protocols over the 4-float `message_out` bus to solve
the focus-fire cooldown penalty?

- **Instrumentation:** per-generation static analysis of
  `message_out` writes (presence, dimensionality actually used,
  variance across drones) plus behavioural signatures (focus-fire
  redundancy; dispersion under threat).
- **Coordination index (CI):** a scalar in [0, 1] combining message-
  bus utilization and a reduction in wasted-cooldown fraction
  relative to the pursuit\_v1 baseline.
- **H0₂:** CI does not rise above the per-model-generation-0 mean
  under co-evolutionary pressure (Track C).

### RQ3 — Reflection vs. Execution
Is there a measurable correlation between the quality of a model's
verbalized `<strategy>` block (game-theoretic reasoning) and the
fitness of the compiled C++?

- **Reflection score:** LLM-as-judge rubric scored 1–5 on (a) causal
  diagnosis of prior loss, (b) specificity of counter-tactic, (c)
  feasibility within the ABI. Rubric released with the dataset.
- **Pearson r between reflection score and Δfitness at the next
  generation.**
- **H0₃:** r is not distinguishable from zero across models, i.e.
  models hallucinate tactics they fail to implement.

### RQ4 — Evolutionary Friction
How often do different models stall due to compile failures, banned-
token linter hits, goto-based loops (rejected by the injector), or
logic loops?

- **Stall taxonomy:** `compile_failed`, `lint_failed`,
  `inject_failed`, `orchestrator_failed`, `no_improvement`
  (`Δfitness ≤ 0` for N consecutive generations).
- **Metric:** fraction of generations that terminate in each stall
  category, by model.
- **H0₄:** stall rates are equal across models.

---

## 2. The Cognitive Loop (Observation Engine)

LLMs cannot natively digest a raw multi-megabyte `trace.jsonl`. We
build a **Telemetry Translation Layer** that converts physical GPU
simulation data into semantic, tactical observations. Each generation
follows a strict 3-step pipeline enforced by the Python orchestrator.

### 2.1 Observation — After-Action Report

A script (to be added as `scripts/aar.py`) parses the trace and emits
a dense, ~2 KB natural-language summary plus a structured sidecar.

**Example AAR:**
> **Match outcome:** DEFEAT. You survived 120 ticks; enemy survived 800.
> 45 % of your shots overlapped on already-dead targets (failed
> focus-fire coordination). Your drones were highly clustered
> (mean pairwise distance 5.2 units, arena diagonal 1414.2). The
> enemy used kiting tactics — 78 % of their attacks came from > 40 u
> distance, just inside `disable_range`. Your cooldown utilization
> was 31 % (wasted 69 % of attack windows).

**Structured fields** (subset): `outcome`, `ticks`, `alive_a_final`,
`alive_b_final`, `wasted_shot_fraction`, `mean_pairwise_dist`,
`dispersion_index`, `cooldown_utilization`, `message_bus_entropy`,
`kiting_score_enemy`, `focus_fire_redundancy`.

### 2.2 Reflection — Chain of Thought

The LLM is prompted to produce a `<reflection>` block (JSON-framed)
with fields: `diagnosis`, `counter_strategy`, `expected_mechanism`,
`risks`. Reflection is logged verbatim and not re-fed to later
generations except as short summaries, to prevent context collapse.

### 2.3 Mutation — Code Generation

The LLM outputs exactly one fenced `cpp` block containing the full
translation unit. The existing linter (`scripts/lint_ai_tokens.py`),
loop-guard injector (`scripts/inject_guards.py`), and sandbox
compiler (`scripts/sandbox.py`) are reused unchanged. Any stall
category is recorded and counts against RQ4.

All three steps are persisted under `data/runs/<track>/<model>/
genXXXX/` as `aar.json`, `aar.md`, `reflection.json`, `candidate.cpp`,
`candidate.injected.cpp`, `generation.json` (summary).

---

## 3. Experimental Design

We run three evolutionary tracks. Each track is reproducible from a
single command + seed-base; CI gates require byte-identical
`tournament.json` across reruns (see M12 determinism test).

### Track A — Baseline Ascension (absolute capability)

- **Setup:** each LLM starts from a neutral template
  (`src/baselines/stationary_v1.cpp` with AAR examples shown once in
  the system prompt).
- **Opponent:** fixed `pursuit_v1` baseline.
- **Length:** 50 generations per model, seeds {42, 43, 44} (three
  parallel lineages → 150 generations per model).
- **Primary metric — Time-to-Domination:** smallest `g` such that the
  generation-`g` champion achieves ≥ 95 % win rate over 100 matches
  vs. `pursuit_v1`.
- **Secondary:** code size, compile-failure rate, wall-clock per
  generation.
- **Estimated compute:** ~150 LLM calls × 3 models + ~450 match
  compilations + ~45 000 matches (@100 evaluation matches/gen).

### Track B — Monotonic Self-Play (AlphaGo-style)

- **Setup:** each LLM plays only against its own previous best
  (generation `N` vs `N-1`; generation 1 plays `pursuit_v1`).
- **Length:** 100 generations per model, single lineage per model
  (no multi-seed — the whole point is to test a single trajectory).
- **Post-hoc:** 100-entry round-robin tournament per model using
  `scripts/tournament.py --mode round_robin --n-matches 4` plus a
  direct `gen100 vs gen1` head-to-head with `--n-matches 50` to
  quantify absolute improvement.
- **Primary metric:** smoothness of Elo growth (r² of linear fit;
  detect cycles via autocorrelation of Δfitness).
- **Failure mode we want to catch:** local maxima and rock-paper-
  scissors cycles where `gen_N > gen_{N-1}` locally but `gen_{N+k} <
  gen_{N-1}` for some `k`.

### Track C — Adversarial Co-Evolution (the main event)

- **Setup:** Model A (Claude) evolves exclusively vs. Model B
  (Gemini). `A_N` plays `B_N`; both receive the AAR from that
  pairing; both mutate; next generation plays again.
- **Length:** 100 paired generations. 3 independent seed-lineages to
  average out stochastic asymmetry.
- **Primary metric:** Elo drift of the joint population under a
  fixed external yardstick (all generations tournamented against a
  frozen reference set: `pursuit_v1`, `cluster_v1`, and the
  Track-A generation-50 champions).
- **Secondary:** counter-tactic lag. Define a qualitative tactic
  detector (e.g. "flanking" = drones split into two groups with
  angular separation > 90° from enemy centroid sustained for ≥ 50
  ticks). Measure Δgenerations between first appearance in A vs.
  first appearance of a defensive counter in B.

---

## 4. Data, Artefacts, and Reproducibility

Every generation emits a canonical JSON record keyed by
`(track, model, seed, generation)`. The union of these records
forms the analysis dataset (anticipated size: ~5–10 MB JSONL).

Per-generation artefacts:

- `prompt.md`, `response.md` (exact LLM I/O, redacted of secrets)
- `aar.json`, `aar.md`
- `reflection.json`
- `candidate.cpp`, `candidate.injected.cpp`
- `match/results.json` (existing schema; see
  `docs/results_schema.json`)
- `generation.json` (summary: status, fitness, stall category,
  wall-ms, token counts)

Aggregations:

- `data/runs/<track>/summary.csv` — one row per generation.
- `data/runs/<track>/elo_curves.json` — one series per (model, seed).
- `data/runs/<track>/tactic_events.jsonl` — first-appearance log
  for the qualitative tactic detector.

**Release plan:** anonymized dataset + all prompts under the repo's
MIT license when the paper is submitted.

---

## 5. Paper Outline

1. **Abstract & introduction.** Limits of static code generation;
   promise of LLM-driven evolutionary programming in competitive,
   stateful environments; our contributions.
2. **The SwarmEvolve environment.** C++/OpenACC GPU engine; POD
   memory constraints; synchronous deterministic combat; ABI and
   safety layers (linter, loop-guard injector, sandbox).
3. **Methodology.** Observation → Reflection → Mutation pipeline;
   telemetry translation; tracks A/B/C.
4. **Results — evolutionary velocity.** Line graphs of Elo climb
   and stall rates by model; bootstrap CIs; RQ1 / RQ4 findings.
5. **Results — emergent behaviours.** Qualitative analysis of
   generated C++. Highlight specific generations where an LLM
   invented a novel mathematical formation or communication
   protocol. Coordination-index curves for RQ2. Reflection-vs-
   execution correlation for RQ3.
6. **Discussion.** Failure modes (reward hacking, compile-loop
   attractors, over-fitting to a single opponent). Reproducibility
   caveats (model routing aliases, tokenizer drift over time).
7. **Conclusion.** Implications for self-improving software,
   autonomous multi-agent systems, and code-generation benchmarks.
8. **Appendices.** Full prompt templates, AAR schema, linter rules,
   and the complete list of generation-level artefacts.

---

## 6. Implementation Gaps to Close (Backlog)

The current repository (M0–M14) already provides the engine, sandbox,
evolutionary loop, and tournament runner. The following items are
**new** work specific to this paper:

- **M15 — AAR generator (`scripts/aar.py`).** Derives structured
  tactical features from a trace and renders the natural-language
  AAR. Unit-tested on replayed baseline matches.
- **M16 — Multi-provider LLM bench harness.** Generalizes the
  existing `evolve.py` to support Gemini and GPT-4o clients
  behind the `LLMClient` protocol; records provider / model ID /
  response metadata in every generation record.
- **M17 — Reflection rubric scorer.** LLM-as-judge implementation
  with held-out calibration set; inter-model agreement reported.
- **M18 — Tactic detector.** Deterministic feature extractor over
  trace for flanking, kiting, clustering, message-bus entropy.
- **M19 — Track runners.** `scripts/track_a.py`, `track_b.py`,
  `track_c.py` as thin drivers over the existing evolutionary
  loop, plus per-track analysis notebooks.
- **M20 — Reproducibility CI.** Seeded mini-tracks (5 generations
  each) in CI to catch pipeline regressions without burning API
  budget.

Estimated wall-clock for the full paper dataset on a single 8-GPU
Spark node: ~3–5 days, dominated by LLM latency rather than GPU
simulation (matches at `num_drones=10` finish in well under 100 ms
on GPU; see `docs/perf_report.md`).

---

## 7. Risks and Ethical Considerations

- **Model routing instability.** Providers silently remap model IDs;
  we must log and publish the exact `model` string returned in
  every response alongside the request string.
- **Prompt sensitivity.** A 50-generation trajectory can be swung
  by a single word in the system prompt. All prompts are frozen
  per track and released verbatim.
- **Dual-use framing.** This is a *benchmark* for code-generating
  agents under adversarial pressure, not a weapons research
  program. The simulated combat is a proxy for any adversarial
  multi-agent objective (market-making, distributed scheduling,
  etc.); the paper will foreground this framing.
- **API cost.** Track C (~600 generations × 2 models × 3 seeds ≈
  3 600 LLM calls) is the dominant spend; budget cap enforced in
  the orchestrator (see `scripts/experiment_log.py`).

---

## 8. Links

- [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) — engineering
  milestones M0–M14 that this research plan builds on.
- [ARCHITECTURE.md](../ARCHITECTURE.md) — system design and sandbox
  layers.
- [SPECIFICATION.md](../SPECIFICATION.md) — game rules, ABI, and
  deterministic combat resolution.
- [docs/perf_report.md](perf_report.md) — GPU scaling results
  (M13) that bound the match throughput assumed in §6.
