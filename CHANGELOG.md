# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- M13 GPU scaling study scaffolding: `--benchmark` mode in the engine
  (one JSON line per match, no file output), compile-time
  `MAX_DRONES_OVERRIDE` switch, heap-allocated `World` for large N,
  `--arena-scale` CLI flag for constant-density scaling, and
  `_OPENMP`-guarded query-phase pragma for honest CPU-OMP baseline.
- `scripts/bench_gpu.py` — driver that sweeps (backend, N, repeats),
  emits byte-stable `bench_results.json` + human-readable
  `bench_results.csv`, and regenerates the Markdown report between
  `<!-- BENCH_DATA_START -->` / `<!-- BENCH_DATA_END -->` markers.
- `scripts/bench_plot.py` — matplotlib plotter for wall-time,
  per-tick, and speedup charts; gracefully skips when matplotlib is
  absent (JSON remains the source of truth).
- Makefile: `build-linux-cpu-omp`, `bench-build-{cpu1,cpu-omp,gpu}`,
  `bench-{cpu1,cpu-omp,gpu}`, and `bench-all` targets.
- `tests/test_bench.py` — smoke test for bench mode, no-side-effects
  guard, CPU1/OMP trace-byte-equality guard, plotter regression tests,
  and perf-report marker test.
- `docs/perf_report.md` — methodology + hardware-placeholder skeleton
  with `<!-- CONCLUSION_PENDING -->` exit marker.
- M13: GPU scaling study — GPU (OpenACC) delivers 6.7× over 20-core
  OpenMP at N=100K; crossover at N≈4K. Measured on NVIDIA GB10
  (Grace-Blackwell) with nvc++ 25.11. Data: `data/bench/bench_results.json`,
  plots: `data/bench/plots/*.png`, report: `docs/perf_report.md`.
- `docs/m13_handoff.md` — full Spark-Claude runbook: prerequisites,
  build commands, expected wall-clock times, TDR escalation, and the
  honest-conclusion template.
- Repository scaffolding (M0): `Makefile`, `pyproject.toml`, `.clang-format`,
  `.editorconfig`, `.gitignore`, `.pre-commit-config.yaml`, `.env.example`.
- GitHub Actions CI workflow: lint, macOS/Linux builds, C++ and Python test
  jobs, docs-link checker.
- `scripts/lint_ai_tokens.py` — pre-commit linter enforcing SPECIFICATION §2.2
  Forbidden Operations for AI source files.
- Governance files: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  `CODEOWNERS`.
- POD types and AI ABI (M1): `src/types.h`, `src/ai_abi.h` with compile-time
  layout assertions.
- `tests/test_types.cpp` — ABI golden test and trivially-copyable assertions.
- Engine core (M2): `src/engine.cpp` (CLI, four-phase game loop, JSONL trace
  writer) and `src/engine.h` (pure-function phase helpers).
- Stub AIs `src/a/team_a_ai.cpp` and `src/b/team_b_ai.cpp` (no-op, replaced
  by baselines in M3).
- `tests/test_engine_phases.cpp` — 13 dependency-free tests covering
  velocity clamping, boundary clamp, combat-range boundary, mutual
  destruction, focus-fire, invalid target IDs, dead-drone message zeroing,
  cooldown decrement, and termination.
- Opt-in `SANITIZE=1` Makefile switch (Linux CI uses it; macOS defaults to
  off to avoid Homebrew-LLVM ASan dyld issues).
- Baseline AIs (M3): `src/a/team_a_ai.cpp` (nearest-enemy pursuit) and
  `src/b/team_b_ai.cpp` (weighted cluster + focus-fire with opportunistic
  in-range fallback).
- Frozen baseline corpus under `src/baselines/` (`pursuit_v1.cpp`,
  `cluster_v1.cpp`, `stationary_v1.cpp`) using the `TEAM_NS_PLACEHOLDER`
  namespace token so one source can be rendered into either `TeamA` or
  `TeamB` by the test harness.
- `src/baselines/README.md` documents the freeze policy (version-bump,
  never edit-in-place).
- `tests/test_baselines.py` — pytest integration suite: pursuit vs
  stationary ≥ 95/100, pursuit vs cluster no side > 80%, determinism
  (same seed → byte-identical trace; different seed → different trace),
  banned-token lint applied to all frozen baselines.
- Trace schema & determinism (M4):
  - `docs/trace_schema.json` (JSON Schema draft-07) formally describing
    one JSONL trace line.
  - `tests/fixtures/golden/seed42_pursuit_vs_cluster.jsonl` — frozen
    reference trace for `pursuit_v1 vs cluster_v1` at seed=42 (117 lines,
    DRAW @ tick 116), pinned SHA-256 enforced by
    `test_golden_trace_matches_pinned_sha`.
  - `tests/_build_helper.py` — shared render+compile pipeline extracted
    from `test_baselines.py` so the determinism suite reuses it.
  - `tests/test_trace_schema.py` — schema is well-formed (meta-validated);
    every golden line validates; malformed fixtures are rejected.
  - `tests/test_determinism.py` — 10 same-seed runs hash identically,
    different seeds produce different traces, fresh engine reproduces
    the golden trace byte-for-byte.
  - Added `jsonschema>=4.21,<5` to project dependencies (already pinned
    in `pyproject.toml`).
- Visualizer (M5):
  - `scripts/visualizer.py` — render any valid trace JSONL to an MP4 via
    `cv2.VideoWriter` (mp4v codec, no ffmpeg dependency). Draws each
    drone as a filled circle (blue Team A, red Team B), a faint
    disable-range ring, and a cyan cooldown bar. Dead drones render as
    grey X markers. Coordinate system is Y-down to match the engine
    (SPECIFICATION §1.2) — OpenCV is natively Y-down so no axis flip.
  - HUD overlay with tick / alive counts / final outcome.
  - CLI flags: `--fps`, `--resolution WxH`, `--arena WxH`,
    `--disable-range`, `--no-range-ring`, `-v`.
  - `tests/test_visualizer.py` — 8 tests: golden-trace round-trip
    matches frame count, resolution honoured, Y-down correctness
    (drone at y=50 renders in top half), distinct team colors, dead
    drones suppress the range ring, cooldown bar scales with cooldown,
    CLI rejects missing trace (exit 2, structured stderr), malformed
    JSON raises `ValueError`.
  - `make visualize-demo` target renders `data/traces/demo.jsonl` →
    `data/videos/demo.mp4`.
- Loop-guard injector (M6):
  - `scripts/inject_guards.py` — regex-based C++ loop-guard injector with
    comment/string scrubbing, idempotence marker
    (`/* @swarmevolve:guards-injected */`), do-while tail detection, and
    `#line` preservation. Transforms every `while` / `for` / `do` loop to
    bail out after 1000 iterations, preventing GPU TDR crashes from
    LLM-authored infinite loops.
  - Exit codes: `0` success, `1` CLI/IO error, `2` `goto`-based loop
    rejected, `3` idempotence failure, `4` regex backend refused
    (single-statement body, macro-hidden loop with
    `--fail-on-macro-loops`). libclang backend reserved as a stub.
  - CLI flags: `--check`, `--stdout`, `--backend {regex,libclang}`,
    `--allow-regex`, `--fail-on-macro-loops`, `-v`.
  - `tests/fixtures/inject/` — 15-fixture corpus covering while/for/
    do-while/range-for/nested/lambda/continue-break-return/already-injected
    plus an adversarial subdirectory (`goto_based_loop`, `macro_loop`,
    `single_statement_body`, `comment_with_while`, `string_with_while`).
  - `tests/test_inject_guards.py` — 27 tests: idempotence, guard-count,
    comment/string false-positive prevention, goto rejection, do-while
    tail handling, parameterized fixture sweep (every fixture injects +
    compiles `-Werror`-clean), runtime-termination tests for infinite
    fixtures (subprocess with 1s timeout), integration test injecting
    into `pursuit_v1` / `cluster_v1` and compiling them against the
    engine, CLI `--check` mode exit codes.
- Orchestrator CLI (M7):
  - `scripts/orchestrator.py` — `run` sub-command renders both AI sources
    (with `TEAM_NS_PLACEHOLDER` substitution), compiles them into a
    single engine binary under `<out-dir>/build/`, executes one match
    with configurable `--seed` / `--max-ticks` / `--drones-a` / `--drones-b`
    / `--timeout`, and writes a structured `results.json`. Optional
    `--video` hands the trace to `scripts/visualizer.py`. Stub
    sub-commands `evaluate` (M9), `evolve` (M10), `tournament` (M12)
    return `EXIT_INVALID_INPUT` with a `not-implemented` log record.
  - Exit codes: `0` success, `2` invalid input / missing AI, `3`
    compilation failed, `4` engine crash / timeout, `10` internal error.
  - JSON-formatted structured logs (`-v` info, `-vv` debug).
  - `docs/results_schema.json` (JSON Schema draft-07) — normative
    description of `results.json` (`schema_version=1`, `status`,
    `team_{a,b}` with SHA-256, `compile`, `match`, `artifacts`, `host`).
  - `scripts/llm_client.py` — `LLMClient` protocol + `LLMResponse`
    dataclass + `StubClient` / `MockClient` (M10 replaces the stub
    with real Anthropic / Gemini adapters).
  - `scripts/fitness.py` — `FitnessResult` dataclass + `evaluate_fitness`
    API stub (M9 replaces the body).
  - `tests/test_orchestrator_run.py` — end-to-end: happy path matches
    golden outcome (`DRAW` @ tick 116, `pursuit_v1 vs cluster_v1`
    seed=42); `--no-record` suppresses trace; missing AI → exit 2;
    compile failure → exit 3 + structured `results.json`; forced timeout
    → exit 4; no artifact paths ever escape `--out-dir`.
  - `tests/test_orchestrator_cli.py` — argument parsing, `--help`,
    stub sub-command exits, in-process tests for `detect_compiler`,
    `JsonFormatter`, and the `MockClient` queue.
- Live LLM integration (early slice of M10):
  - `scripts/llm_client.py` extended with a real `AnthropicClient`
    wrapping `anthropic.Anthropic.messages.create`. Reads
    `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` from the environment so
    secrets never appear on the command line or in persisted files.
    Exponential-backoff retry for transient errors
    (`APIConnectionError`, `APITimeoutError`, `RateLimitError`,
    `InternalServerError`); fatal errors are redacted via
    `redact_secrets` before surfacing.
  - `LLMError`, `extract_cpp_block(text)` (prefers ``` ```cpp ``` ``` /
    ``` ```c++ ``` ``` fences, falls back to any fenced block), and a
    frozen `LLMResponse` dataclass with token counts + stop reason.
  - `prompts/evolve_ai.md` — single-turn prompt template. Placeholders:
    `{TEAM_LETTER}`, `{NAMESPACE}`, `{OPPONENT_NAME}`,
    `{OPPONENT_SOURCE}`, `{TYPES_HEADER}`, `{ABI_HEADER}`. Enumerates
    hard rules (no heap / no STL / no I/O / no `goto` loops /
    braced bodies only / pure function of inputs).
  - `scripts/evolve_once.py` — single-shot driver: render prompt →
    `AnthropicClient.generate` (or `--client=mock` for offline tests)
    → `extract_cpp_block` → banned-token lint → guard injection →
    delegate to `scripts/orchestrator.py run` → consolidate
    `evolve_once.json` summary beside the match's `results.json`.
    Exit codes 20/21/22/23/24 for LLM / parse / lint / inject /
    orchestrator failures respectively.
  - `tests/test_llm_client.py` — 15 tests: fence extraction (tagged,
    untagged, case-insensitive, cpp-preferred), secret redaction,
    `MockClient` queue semantics, and an `AnthropicClient` stub-SDK
    triple (happy path, transient-error retry, fatal-error
    redaction).
  - `tests/test_evolve_once.py` — 4 offline pipeline tests using
    `--client=mock`: dry-run, mock-pursuit vs stationary end-to-end
    (pursuit wins), banned-token rejection (`std::vector`), and
    parse-fail on a fenceless response.
  - `anthropic>=0.34,<1` moved from optional `[llm]` to actively
    exercised; install with `pip install swarmevolve[llm]` or a
    plain `pip install anthropic`.
- Sandbox container (M8):
  - `docker/Dockerfile.sandbox` — multi-stage image (Ubuntu 22.04)
    carrying g++, `libstdc++-11-dev`, Python 3, the engine + headers,
    the frozen baselines, and `scripts/inject_guards.py` baked into
    `/opt/swarmevolve/`. Runs as uid 65534:65534.
  - `docker/entrypoint.sh` — inject loop-guards → compile with
    `-Werror -Wno-unknown-pragmas` → run the engine; writes
    `/work/out/sandbox_status.json` with `status ∈ {ok,
    invalid_input, inject_failed, compile_failed, engine_crashed}`.
  - `scripts/sandbox.py` — host-side Python wrapper with the
    spec-exact flag tuple (`--rm --network=none --cap-drop=ALL
    --security-opt no-new-privileges --read-only
    --tmpfs /tmp:size=64m,mode=1777,exec,nosuid,nodev
    --memory=512m --pids-limit=64 --cpus=2 --user 65534:65534`),
    runtime auto-detection (`$CONTAINER_RUNTIME` → docker → podman),
    host wall-clock timeout, and a `SandboxResult` dataclass.
  - `Makefile` — `docker-build` and `docker-test` targets; the
    latter runs only the sandbox test modules.
  - `tests/test_sandbox_ok.py` — 7 tests: spec-exact flag pin,
    command-shape, missing-team guard, byte-identical baseline
    trace inside the sandbox vs the golden, missing-input structured
    failure, compile-failure status capture, and CLI
    image-missing exit code (21).
  - `tests/test_sandbox_escape.py` — 5 containment tests planting
    synthetic adversarial payloads (network egress, read-only
    rootfs breach, fork bomb, infinite match, 2-GiB memory bomb);
    each asserts the sandbox contains the attempt without harming
    the host. Payloads are scoped to the first AI-query tick
    (`current_tick > 1` early-return) to keep runs bounded.
  - Tests skip cleanly when no container runtime or sandbox image is
    present. On macOS with Colima, tests stage temp dirs under
    `~/.pytest-sandbox` because Colima's virtiofs only shares `$HOME`.
- Fitness evaluator & experiment logging (M9):
  - `scripts/fitness.py` — promoted from stub to real implementation.
    `evaluate_fitness(team_a, team_b, *, n_matches, seed_base, workers,
    ...)` compiles each pairing once per worker, dispatches matches
    across a `ProcessPoolExecutor` with round-robin seed partitioning,
    and returns a frozen `FitnessResult` dataclass (wins/draws/invalid,
    mean, stdev, 95 % percentile bootstrap CI, per-match list). Scores
    are `+1 A_WIN / -1 B_WIN / 0 DRAW`; TIMEOUT/CRASH count as
    `invalid` rather than `draws`. The bootstrap's RNG seed is derived
    from `sha256("ci:<seed_base>:<n_matches>")` so CIs reproduce
    bit-identically without an extra CLI knob. Raises `CompileError`
    on pipeline-level compile failures; single-worker path is
    synchronous to dodge the pickle boundary.
  - `scripts/experiment_log.py` — new append-only JSONL log
    (`<run_dir>/events.jsonl`) with monotonic `seq`, ISO-8601 UTC
    timestamps, and recursive secret redaction (`sk-ant-…`,
    `sk-…{20+}`, `AIza…`, `Bearer …`). `ExperimentLog` is a context
    manager that guarantees `experiment_error` + `experiment_end`
    events even on exception. `build_environment_snapshot()` captures
    git SHA, dirty flag, `pyproject.toml` hash, Python version,
    platform, CPU count, hostname, and SHA-256 of each AI source.
  - `scripts/orchestrator.py evaluate` — new sub-command replacing the
    prior stub. Writes `fitness.json` (validated against
    `docs/fitness_schema.json`) and `events.jsonl`; per-match results
    are logged individually so `replay` can walk the log; exits 4 if
    more than half the matches are invalid.
  - `scripts/orchestrator.py replay <run_dir>` — new sub-command that
    reads a prior `events.jsonl`, extracts the experiment config, and
    re-runs `evaluate_fitness`; compares `wins_a/b/draws/invalid/mean/
    stdev/ci_low/ci_high` field-by-field and exits 4 on any divergence.
  - `docs/fitness_schema.json` — JSON Schema draft-07 for
    `fitness.json`; used by the test suite to pin the output format.
  - `tests/test_fitness.py` — 13 tests: bootstrap CI determinism +
    seed-variance + single-sample rejection + degenerate-collapse,
    arg validation, module-scoped `short_run` fixture sharing the
    compile cost, field population, CI bracketing, stability (same
    inputs → identical aggregates), workers=1 vs 2 aggregate
    equivalence, self-play `|mean| ≤ 0.5`, JSON round-trip, and
    `CompileError` propagation.
  - `tests/test_experiment_log.py` — 18 tests covering all four
    secret-key patterns, dict/list recursion, git-SHA false-positive
    guard, non-string pass-through, environment snapshot keys,
    monotonic `seq`, write-outside-context error, payload redaction,
    `_raw=True` escape hatch, `write_start` shape,
    `FileNotFoundError` on missing log, malformed-line detection,
    exception → error+end, and append-only on reopen.
  - `tests/test_orchestrator_evaluate.py` — 10 end-to-end CLI tests:
    schema well-formed, `fitness.json` + `events.jsonl` shape, summary
    matches `fitness.json`, missing `--team-a/-b` exits 2, compile
    failure exits 3 with `compile_failed` event, replay parity exits
    0, missing/empty run dir exit 2.
- Closed-loop evolutionary driver (M10):
  - `scripts/evolve.py` — multi-generation evolutionary loop that iterates
    prompt → LLM → parse → lint → inject → compile → `evaluate_fitness` →
    accept-if-better, keeping the whole pipeline in-process (≈10× faster
    than a subprocess-per-gen variant). `LoopConfig` and `LoopState`
    dataclasses capture the full run; `GenSummary` records every
    attempted generation with status ∈ `{accepted, rejected, llm_failed,
    parse_failed, lint_failed, inject_failed, compile_failed, eval_failed}`.
    Team-letter sign flip on `mean`/`ci_low`/`ci_high` so "higher is
    better for the evolving team" holds uniformly for Team A and Team B.
    Deterministic seeds via `seed_base_root + gen * n_matches`.
    `--max-compile-failures` caps consecutive pipeline failures (exit 30);
    `--accept-margin` makes the accept rule strict-`>`.
  - Atomic JSON checkpointing (`os.replace(tmp, path)`) into
    `<run_dir>/checkpoints/NNNN.json` with a mirror at
    `checkpoints/latest.json` written every `--checkpoint-every` gens.
    Resume via `--resume <run_dir>` reconstructs `LoopState` from the
    latest checkpoint (falling back to `state.json`) and replays the
    MockClient queue to the correct cursor.
  - Optional matplotlib fitness-plot PNG (`plots/fitness.png`) per
    checkpoint: errorbars for CI, scatter for accept/reject, step-plot
    for the champion trace. Headless `Agg` backend; silently skipped
    when matplotlib is missing so mandatory-dep surface stays small.
  - `docs/checkpoint_schema.json` — JSON Schema draft-07 (`$id`
    `https://swarmevolve.io/schemas/checkpoint.m10.v1.json`,
    `schema_version` const `"m10.v1"`) pinning the checkpoint layout;
    references `docs/fitness_schema.json` via `$ref` for the embedded
    champion fitness record.
  - `scripts/orchestrator.py evolve` — promoted from stub to a thin
    wrapper that forwards all 15 flags (`--opponent`, `--as-team`,
    `--generations`, `--n-matches`, `--workers`, `--client`,
    `--mock-response-dir`, `--model`, `--seed`, `--accept-margin`,
    `--max-compile-failures`, `--checkpoint-every`, `--out-dir`,
    `--resume`, `--seed-ai`, `--prompt`) into `evolve.main()` and emits
    an `evolve-start` JSON log record.
  - Exit codes: `0` success, `2` invalid input, `30` compile-failure cap
    exceeded, `31` unrecoverable LLM error, `32` schema/checkpoint write
    failure, `33` corrupt resume state.
  - `tests/test_evolve.py` — 7 tests: secret-redaction preserves
    non-secret text, `STATUS_PARSE_FAILED` when no ``` ```cpp ``` ```
    block, `--max-compile-failures` cap hits exit 30, API-key never
    leaks into run artefacts (synthetic `LeakyClient`), three-generation
    mock loop produces monotone champion + fitness plot, checkpoint
    validates against `docs/checkpoint_schema.json` via pre-seeded
    `RefResolver` store, and resume-after-crash reproduces the
    uninterrupted run bit-identically (same champion mean, wins_a,
    per-gen aggregates).
  - `tests/test_orchestrator_cli.py` — updated
    `test_evolve_requires_opponent` asserting the evolve sub-command is
    now real (exits `EXIT_INVALID_INPUT` with `--opponent` in stderr
    rather than the pre-M10 `not-implemented` stub message).
- GPU port & profiling (M11):
  - OpenACC parallelisation of all four game-loop phases in
    `src/engine.h` (`movement_phase`, `combat_phase_one_side`,
    `apply_cooldowns`, `apply_deaths`, `decrement_cooldowns`,
    `route_messages`) and `src/engine.cpp` (`project_enemies`,
    `query_phase<TeamAAiCallable>`, `query_phase<TeamBAiCallable>`).
    Each `#pragma acc parallel loop` carries explicit `[0:n]` data-clause
    bounds — critical because nvc++ defaults to 1-byte transfers for
    unbounded pointer parameters (earlier drafts corrupted 9 of 10
    death flags on round-trip).
  - Template functors `TeamAAiCallable` / `TeamBAiCallable` (with
    `#pragma acc routine seq` on `operator()`) replace the function-
    pointer call path in `query_phase`, since nvc++ cannot lower
    indirect calls to device code.
  - `copy` (not `copyout`) for `attacker_cooldowns_out`: the combat
    kernel writes only successful-attacker slots; `copyout` would
    transfer uninitialized device memory back for the untouched slots.
    Caller contract (host-zero before call) documented inline.
  - `Makefile`: split `CXXFLAGS_GPU` without `-Wno-unknown-pragmas`
    (nvc++ rejects that flag and natively understands `#pragma acc`);
    `-gpu=mem:managed` replaces deprecated `-gpu=managed`.
  - `tests/test_gpu_equivalence.py` — two tests verifying per-platform
    determinism (SPEC §7.6): byte-identical traces at seed=42 × 200
    ticks, and outcome+tick-count parity across seeds 0..4 at 300
    ticks. Skips on hosts without nvc++.
  - `tests/test_gpu_tdr_stress.py` + `tests/fixtures/tdr_stress_ai.cpp`
    — 50-drone × 300-tick match with a hard-bounded 5 000-iter trig-
    accumulate loop per drone per tick (≈ 5 Gflops for the whole
    match). Verifies the GB10 TDR watchdog is never tripped; skips
    without nvc++.
  - `scripts/profile.py` — Nsight Systems wrapper emitting a
    `.nsys-rep` archive plus per-kernel / per-memcpy / OpenACC CSVs
    and a `summary.json` with the top-N kernels and wallclock.
    Tolerates nsys rc=2 soft warnings (e.g. "CPU IP/backtrace
    sampling not supported") when the report archive is produced.
    Exit codes: `0` ok, `2` usage, `40` nsys missing, `41` binary
    missing, `42` nsys failed.
  - `docs/profiling/2026-04-22.md` — honest M11 profiling report.
    Headline finding: the 10× perf gate is **not met** at 50 drones ×
    1000 ticks on GB10. GPU wallclock is ~1000× the CPU wallclock
    because per-tick parallelism with n=50 is pinned at kernel-launch
    granularity; device-side work + memcpy account for only 42 ms of
    a 5 764 ms run (99.26 % overhead). Recommendation: defer the 10×
    gate to M12 where batched parallel-over-matches amortises launch
    overhead across N matches. This is recorded as an architectural
    mismatch, not a coding defect.
- Tournament & analysis (M12):
  - `scripts/tournament.py` — round-robin and Swiss tournament runner
    producing Elo-like ratings + a raw win matrix. Each pairing is
    evaluated both directions (AI as TeamA and TeamB) to average out
    the engine's spawn-layout asymmetry. Seed derivation is
    deterministic per ordered pair via SHA-256 of
    `"seed_base|team_a|team_b"` so two runs with the same config
    produce byte-identical `tournament.canonical.json`. Elo updates
    apply per-match (not per-pairing) so high-N pairings get more
    weight. Outputs: `tournament.json`, `tournament.canonical.json`,
    `ratings.csv`, `win_matrix.csv`, `events.jsonl`. Exit codes:
    `0` ok, `2` usage, `40` compile failure, `41` no compiler.
  - `scripts/analysis.py` — plots + derived stats from completed
    experiment directories. Tournament analysis produces
    `rating_trajectory.png`, `win_matrix_heatmap.png`,
    `clustering.png` (single-linkage dendrogram of behavioural
    distance, defined as symmetrised |wins_a(i,j) − wins_a(j,i)| /
    n_matches), and `top_matches.json` (the pairings with the largest
    Elo surprise). Evolution analysis produces a champion-trajectory
    `fitness.png`. matplotlib is optional; when missing, a
    `skipped.json` stub is written and exit is still 0.
  - `scripts/analysis.ipynb` — thin notebook that imports
    `scripts/analysis.py` and displays its PNGs inline (interactive
    use only; the real logic is unit-tested in the `.py` module).
  - `scripts/render_report.py` — stdlib-only Markdown report
    renderer. Given a completed experiment directory, emits
    `report.md` with sections for environment, tournament (ratings
    table, participants, plots, most-informative matches), and
    evolution (champion fitness, generations). Missing inputs
    degrade to a graceful "no results" section. M12 exit criterion
    met: zero manual edits between a completed experiment dir and
    a publishable Markdown report. `--run-analysis` flag
    auto-invokes `scripts.analysis` if plots are absent.
  - `tests/test_tournament.py` — 14 tests covering pure Elo update
    (monotone + zero-sum), pair-seed determinism, round-robin
    schedule coverage, PairingResult outcomes accounting, plus
    integration tests: 4-AI rankings stable across 3 reruns (the
    M12 exit-criterion test), win-matrix conservation, Swiss top-1
    matches round-robin top-1 for a dominant player, ExperimentLog
    event schema, canonical-JSON byte stability, and three CLI
    error-path tests.
  - `tests/test_analysis.py` — 7 tests covering in-memory synthetic
    tournament dicts: `load_tournament` dir+file, behaviour-distance
    symmetry and diagonal, `top_matches` sort invariant, full
    artefact production (gated on matplotlib), matplotlib-missing
    fallback, and CLI smoke.
  - `tests/test_render_report.py` — 8 tests: tournament sections,
    evolve sections, empty-dir graceful degradation, custom output
    path, CLI error path, analysis-plot references, most-informative
    matches table.
  - `tests/fixtures/drift_ai.cpp` — minimal fourth test AI (drifts
    in +x, never attacks) so the 4-AI tournament-stability test
    has a distinct fourth participant without promoting it to a
    frozen baseline.
  - Final test counts: **225 passed, 4 skipped** (3 GPU-only,
    1 matplotlib XOR).
