# SwarmEvolve Implementation Plan

This document is the authoritative execution plan for building SwarmEvolve from
the current specification-only state to a working evolutionary testbed. It
translates [SPECIFICATION.md](SPECIFICATION.md), [ARCHITECTURE.md](ARCHITECTURE.md),
and [DEVELOPMENT.md](DEVELOPMENT.md) into ordered, testable milestones and
codifies engineering practices (testing, CI, security, reproducibility).

Read this in order. Each milestone has **entry criteria**, **deliverables**,
**tests**, and **exit criteria**. Do not start a milestone until the prior
milestone's exit criteria are green.

---

## 0. Guiding Principles

1. **Spec-first, test-first, code-last.** Every milestone begins by adding/updating
   tests and fixture traces before implementation.
2. **Deterministic by default.** Seeded PRNG, no wall-clock, no un-pinned deps.
   Per-platform bit-exact reproducibility is a hard requirement
   (see [SPECIFICATION §7.6](SPECIFICATION.md)).
3. **Small, reviewable PRs.** One milestone may span several PRs; no PR should
   exceed ~400 LOC of non-generated code.
4. **Defense in depth.** Loop-guard injection + POD-only AI ABI + sandbox
   container are independent safeguards; none may be weakened without replacing
   it with an equivalent layer.
5. **Documentation is code.** Every behavior change updates the spec in the
   same PR. CI fails if docs reference undefined symbols (see §9.4).
6. **Fail loudly, recover cleanly.** Engine asserts on invariant violations in
   debug builds; release builds degrade to safe no-ops and log a structured
   error line.

---

## 1. Milestone Overview

| # | Milestone                         | Duration | Platform       | Risk   |
|---|-----------------------------------|----------|----------------|--------|
| M0 | Repo scaffolding & CI            | 1 day    | macOS + Linux  | low    |
| M1 | POD types & ABI freeze           | 1 day    | macOS          | low    |
| M2 | Engine core (CPU, single match)  | 3 days   | macOS          | med    |
| M3 | Baseline AIs (A & B)             | 2 days   | macOS          | low    |
| M4 | Determinism & trace format       | 1 day    | macOS          | med    |
| M5 | Visualizer (JSONL → MP4)         | 2 days   | macOS          | low    |
| M6 | Loop-guard injector              | 3 days   | host Python    | high   |
| M7 | Orchestrator CLI + match runner  | 2 days   | macOS          | low    |
| M8 | Sandbox container                | 2 days   | Linux          | high   |
| M9 | Fitness evaluator & logging      | 2 days   | Linux or macOS | low    |
| M10 | LLM client + evolutionary loop  | 3 days   | host           | med    |
| M11 | GPU port + profiling            | 3 days   | Linux/NVIDIA   | high   |
| M12 | Tournament + analysis tooling   | 2 days   | host           | low    |

Total estimate: ~27 engineer-days. Parallelizable across two engineers after M4.

---

## 2. Milestone M0 — Repo Scaffolding & CI

**Goal**: Every subsequent milestone can land with pre-merge checks passing.

### Deliverables
- Directory layout from [DEVELOPMENT.md §1.1](DEVELOPMENT.md).
- `.gitignore` covering `build/`, `data/traces/*.jsonl`, `data/videos/*.mp4`,
  `__pycache__/`, `*.o`, `swarmevolve`, `.venv/`, `.env`.
- `Makefile` targets: `build-macos`, `build-linux-gpu`, `test`, `lint`, `clean`,
  `run-demo`, `format`, `docker-build`, `docker-test`.
- `pyproject.toml` (or `requirements.txt` + `requirements-dev.txt`) with pinned
  versions: `numpy`, `matplotlib`, `opencv-python`, `pytest`, `pytest-cov`,
  `ruff`, `mypy`, `anthropic`, `google-generativeai`, `libclang` (optional).
- `.pre-commit-config.yaml`: `ruff`, `clang-format`, `mypy`, `yamllint`,
  `end-of-file-fixer`, a custom `no-banned-tokens` hook that rejects `new`,
  `malloc`, `std::vector`, `std::string`, `#include <thread>` inside `src/a/`
  and `src/b/`.
- `.editorconfig` (LF, UTF-8, 4-space indent C++, 4-space Python).
- `.github/workflows/ci.yml`:
  - Job `lint` (Python + clang-format check).
  - Job `build-macos` (macOS runner, clang++).
  - Job `build-linux-cpu` (Ubuntu, g++/clang++ — nvc++ optional/later).
  - Job `test-python` (pytest with coverage ≥ 80%).
  - Job `test-cpp` (GoogleTest).
  - Job `docs-check` (see §9.4).
- `CODEOWNERS`, `CONTRIBUTING.md`, `SECURITY.md` (vuln disclosure), `LICENSE`
  (MIT per README).

### Tests
- CI pipeline runs on an empty `hello-world` C++ and Python to prove the
  scaffold is green before any real code lands.

### Exit criteria
- [ ] Opening a PR triggers all CI jobs; all pass.
- [ ] Pre-commit hooks install and run locally on macOS.
- [ ] `make clean && make build-macos` succeeds on a freshly-cloned repo.

---

## 3. Milestone M1 — POD Types & ABI Freeze

**Goal**: Lock the engine ↔ AI interface before any logic is written.

### Deliverables
- `src/types.h` exactly matching [SPECIFICATION.md §1](SPECIFICATION.md):
  `Vector2D`, `GameParams` (with split `num_drones_a` / `num_drones_b`),
  `AllyState`, `EnemyState`, `Action`, and constants `MSG_SIZE=4`,
  `MEM_SIZE=16`, `MAX_DRONES=50`.
- Marker `static_assert`s:
  ```cpp
  static_assert(std::is_trivially_copyable_v<GameParams>);
  static_assert(std::is_standard_layout_v<Action>);
  static_assert(sizeof(AllyState) <= 32);   // cacheline hint
  ```
- `src/ai_abi.h`: forward declarations of `TeamA::drone_ai` and
  `TeamB::drone_ai` so engine translation units never include AI sources.

### Tests
- `tests/test_types.cpp` (GoogleTest): compile-time assertions and a run-time
  `memcpy` round-trip of a random `GameParams`.
- ABI golden file: `tests/fixtures/abi_golden.txt` contains
  `offsetof(Action, target_id)` etc. Test fails if layout drifts.

### Exit criteria
- [ ] Types compile under clang++ and g++ with `-Wall -Wextra -Werror`.
- [ ] ABI golden test passes.
- [ ] No include of `<vector>`, `<string>`, `<memory>` in `src/types.h`.

---

## 4. Milestone M2 — Engine Core (CPU)

**Goal**: Run one deterministic match end-to-end with stub AIs.

### Deliverables
- `src/engine.cpp`:
  - `main(argc, argv)` with CLI from [SPECIFICATION §4.3](SPECIFICATION.md)
    parsed via a tiny hand-rolled parser (no external dep).
  - Seeded `std::mt19937` for initial spawn placement.
  - Four-phase tick loop: Query → Movement → Combat → Cleanup.
  - Per-team `pending_deaths_a[MAX_DRONES]`, `pending_deaths_b[MAX_DRONES]`.
  - Optional trace writer gated on `--record <path>`.
  - Exit codes per spec: 0 = A wins, 1 = B wins, 2 = draw.
- Stub AIs that write zero velocity and `target_id = -1` so the engine runs to
  timeout.

### Cross-cutting rules
- No `malloc`/`new` in engine hot path; all buffers are file-scope or stack.
- Use `assert()` for invariants (`num_drones_a <= MAX_DRONES`, etc.).
- Structured error lines on stderr: `ERROR kind=<tag> detail=<msg>`.

### Tests
- `tests/test_engine_phases.cpp`:
  - Velocity clamping (input 10·max → output exactly max).
  - Boundary clamp at each arena edge.
  - Combat range boundary: exactly `dist == disable_range` hits; `dist == disable_range + 1e-4` misses.
  - Mutual destruction: both die, both cooldowns set.
  - Focus fire: 3 attackers, 1 target, all 3 cooldowns set, target dies once.
  - Invalid target IDs (`-2`, `num_drones`, self, ally) all no-op, no cooldown.
  - Dead drones: messages zeroed next tick.
- Fuzz harness `tests/fuzz/engine_fuzz.cc` (libFuzzer) that drives engine with
  random actions and asserts no UB under UBSan/ASan.

### Exit criteria
- [ ] All phase tests green.
- [ ] 10 drones × 1000 ticks × stub AI < 100 ms on an M-series Mac.
- [ ] ASan + UBSan build passes the full test suite.

---

## 5. Milestone M3 — Baseline AIs

**Goal**: Two non-trivial reference strategies for regression testing and
as seed code for the evolutionary loop.

### Deliverables
- `src/a/team_a_ai.cpp` — "Nearest-enemy pursuit":
  - Move toward closest alive enemy, attack when in range and `cooldown == 0`.
  - Broadcasts own position in `message_out[0..1]`.
- `src/b/team_b_ai.cpp` — "Cluster + focus-fire":
  - Move toward centroid of alive allies while targeting the enemy nearest to
    that centroid.
- Both files wrapped in proper namespaces with `#pragma acc routine seq`.
- A `src/baselines/` directory with frozen copies for regression use:
  `pursuit_v1.cpp`, `cluster_v1.cpp`.

### Tests
- `tests/test_baselines.py`:
  - `pursuit_v1 vs stationary` → pursuit wins ≥ 95% of 100 seeded matches.
  - `pursuit_v1 vs cluster_v1` → win rate within [30%, 70%] (neither trivially
    dominates; this is a sanity check, not a rigorous bound).
- Determinism smoke: 10 runs of `pursuit vs cluster` at seed=42 produce
  byte-identical traces.

### Exit criteria
- [ ] Both baselines compile with `-Wall -Wextra -Werror -Wshadow`.
- [ ] Neither baseline uses banned tokens (pre-commit enforced).
- [ ] Regression win-rate windows hold across 3 consecutive CI runs.

---

## 6. Milestone M4 — Determinism & Trace Format

**Goal**: Lock the trace format so visualizer and analytics can be developed
in parallel.

### Deliverables
- `docs/trace_schema.json` (JSON Schema draft-07) matching
  [SPECIFICATION §4.1](SPECIFICATION.md).
- `tests/fixtures/golden/seed42_pursuit_vs_cluster.jsonl` checked in.
- Engine writes trailing newline after every line; last line has `outcome`.

### Tests
- `tests/test_determinism.py`:
  - Run engine 10× at seed=42 with both baselines; assert SHA-256 of trace
    file is identical.
  - Run engine at seed=42 and seed=43; assert traces differ.
- `tests/test_trace_schema.py`:
  - Every line of the golden trace validates against `trace_schema.json`.
  - Fuzz: feed malformed JSONL and assert validator rejects.

### Exit criteria
- [ ] Golden trace SHA pinned in test; changes to engine that shift the hash
      require an explicit "bless" commit message (`trace: update golden ...`).
- [ ] Schema validation passes in CI.

---

## 7. Milestone M5 — Visualizer

**Goal**: Human-inspectable MP4 from any valid trace file.

### Deliverables
- `scripts/visualizer.py`:
  - CLI: `visualizer.py <trace.jsonl> <out.mp4> [--fps 30] [--resolution WxH]`.
  - Blue Team A, red Team B, grey for dead, faint circle at `disable_range`,
    cooldown bar beneath each drone.
  - Calls `ax.invert_yaxis()` per [SPECIFICATION §1.2](SPECIFICATION.md).
  - Writes MP4 via `opencv-python` (`cv2.VideoWriter`) to avoid ffmpeg coupling
    in CI; optional `--backend ffmpeg` for high-quality local renders.
- `scripts/visualizer_web.py` (optional) — static HTML/Canvas viewer for
  quick inspection without encoding.

### Tests
- `tests/test_visualizer.py`:
  - Golden trace → MP4; assert file opens, frame count matches tick count,
    first and last frame PSNRs against checked-in PNG snapshots are within
    a tolerance (accounts for font-rendering drift).
- Visual regression: `pytest --update-snapshots` workflow for intentional
  changes.

### Exit criteria
- [ ] Visualizer runs in < 10 s for a 1000-tick match on macOS.
- [ ] CI uploads the demo MP4 as a workflow artifact for reviewer inspection.

---

## 8. Milestone M6 — Loop-Guard Injector

**Goal**: Transform untrusted C++ source so every loop terminates in ≤ 1000
iterations. This is the *primary* defense against GPU TDR crashes.

### Deliverables
- `scripts/inject_guards.py`:
  - Preferred backend: `libclang` AST walk, identifying every `WhileStmt`,
    `ForStmt`, `DoStmt`, `CXXForRangeStmt`.
  - Fallback backend: regex (documented as "best effort, warns on nested
    macros"), guarded behind `--allow-regex` flag.
  - Emits: `int _guard_<N> = 0;` immediately before the loop, and
    `if (++_guard_<N> > 1000) break;` as the first statement in the loop body.
  - Preserves `#line` directives so compiler errors map back to LLM-authored
    lines.
  - Idempotent: re-running on already-injected code is a no-op.

### Tests
- `tests/test_inject_guards.py`:
  - 20+ hand-written fixtures in `tests/fixtures/inject/` covering:
    - `while`, `for`, `do-while`, range-for
    - Nested loops
    - Loops inside templates, lambdas, macros
    - `goto`-based loops (must be rejected, not silently missed)
    - Loops with `continue` / `break` / early `return`
  - Each fixture has an `expected_output.cpp` and a runtime test that compiles
    the injected code, feeds it an input that would loop forever without the
    guard, and asserts termination within 1 second.
- Adversarial suite: `tests/fixtures/inject/adversarial/` with code designed
  to evade the injector (token-splitting across macros, UTF-8 BOM, nested
  raw-string literals). Injector must either handle or explicitly refuse
  (non-zero exit) — silent miss is a test failure.
- Property test (Hypothesis): random valid C++ loop → injected version →
  still compiles, and terminates on any input.

### Exit criteria
- [ ] 100% fixture pass rate on libclang backend.
- [ ] Regex backend passes at least 90% with warnings on the remainder.
- [ ] No adversarial fixture bypasses the injector silently.
- [ ] `inject_guards.py` runs in < 500 ms on a 5 KB source file.

---

## 9. Milestone M7 — Orchestrator CLI

**Goal**: One command runs a full match pipeline (compile → sandbox → trace →
optional video) with structured JSON output.

### Deliverables
- `scripts/orchestrator.py`:
  - Sub-commands: `run`, `evaluate`, `evolve`, `tournament`.
  - `run`: compile both AIs, execute one match inside the sandbox (M8),
    emit `results.json`.
  - Platform detection (macOS → clang++, Linux → nvc++ if present, else g++).
  - Structured logs via `logging` (JSON formatter); verbosity via `-v/-vv`.
- `scripts/llm_client.py` stub (real impl in M10).
- `scripts/fitness.py` stub (real impl in M9).

### Tests
- `tests/test_orchestrator_run.py`:
  - End-to-end `orchestrator.py run --team-a baselines/pursuit_v1.cpp ...`
    produces a valid `results.json` and (optionally) an MP4.
  - Missing AI file → exit code 2, structured error.
  - Compilation failure → exit code 3, compiler output captured in results.
- `tests/test_orchestrator_cli.py`: argument parsing edge cases.

### Exit criteria
- [ ] `results.json` validates against `docs/results_schema.json`.
- [ ] Orchestrator never writes outside `data/` or a given `--out-dir`.

---

## 10. Milestone M8 — Sandbox Container

**Goal**: Compile and execute any LLM-authored AI inside a containment
boundary with no host filesystem or network exposure.

### Deliverables
- `docker/Dockerfile.sandbox` — multi-stage:
  - Stage 1: `nvidia/cuda:12.3.1-devel-ubuntu22.04` + nvc++ + clang++.
  - Stage 2: runtime with just the compiled binary and `/work/out` writable.
- `docker/entrypoint.sh` — runs guard injection, compiles, executes with the
  resource and capability restrictions from
  [ARCHITECTURE.md §Layer 4](ARCHITECTURE.md).
- `scripts/sandbox.py` — thin Python wrapper around `podman run` /
  `docker run` with the exact flags from the spec:
  `--rm --network=none --cap-drop=ALL --security-opt no-new-privileges`
  `--read-only --tmpfs /tmp:size=16m --memory=512m --pids-limit=64 --cpus=2`
  `--user 65534:65534 -v <src>:/work/src:ro -v <out>:/work/out`.
- `make docker-build`, `make docker-test` targets.

### Tests
- `tests/test_sandbox_escape.py` (skipped on hosts without Docker):
  - Inject a payload that tries `system("curl …")` → sandbox blocks (no
    network), and the orchestrator reports the process was killed.
  - Inject a payload that writes outside `/work/out` → read-only FS rejects.
  - Inject a fork bomb → PID limit + timeout kill it within 12 s.
  - Inject a `sleep 60` → orchestrator `timeout 10s` fires.
  - Inject a memory bomb (`new char[2<<30]`) → cgroup OOM kills; structured
    error returned.
- `tests/test_sandbox_ok.py`: baselines compile and run successfully inside
  the sandbox and produce identical traces to an unsandboxed host run
  (byte-exact on the same platform).

### Exit criteria
- [ ] All five escape-attempt tests terminate safely in < 15 s each.
- [ ] Sandbox overhead vs bare-metal run is < 15% on a 10-drone 1000-tick match.
- [ ] `docker-test` runs in CI on Linux.

---

## 11. Milestone M9 — Fitness Evaluator & Logging

**Goal**: Reliable, statistically meaningful fitness estimate for any AI.

### Deliverables
- `scripts/fitness.py`:
  - API: `evaluate_fitness(team_a_src, team_b_src, n_matches, seed_base) -> dict`.
  - Runs matches across a seed range `[seed_base, seed_base + n_matches)`.
  - Parallel execution via `multiprocessing.Pool` (host-side only; each worker
    calls `sandbox.py`).
  - Aggregates mean, std, win/loss/draw counts, confidence interval
    (bootstrap, 95%).
- `scripts/experiment_log.py`:
  - Append-only JSONL log per experiment under
    `data/experiments/<run_id>/events.jsonl`.
  - Records: code hashes (SHA-256), fitness dicts, compile stdout/stderr,
    sandbox exit codes, wall-clock, hardware info.

### Tests
- Stability test: evaluating the same pair of AIs twice with the same
  `seed_base` returns identical fitness dicts.
- Statistical test: 100 matches of `pursuit vs pursuit` (self-play) →
  `abs(mean) < 0.1` and draws dominate.
- Scaling test: evaluation time scales sub-linearly with worker count up to
  cores/2.

### Exit criteria
- [ ] Fitness evaluation of 100 matches completes in < 60 s on 8-core CPU.
- [ ] Experiment logs are replayable: `orchestrator.py replay <run_id>` runs
      the exact same matches and produces the same results.

---

## 12. Milestone M10 — LLM Client & Evolutionary Loop

**Goal**: Closed-loop evolution that actually improves fitness.

### Deliverables
- `scripts/llm_client.py`:
  - Adapters for Anthropic Claude and Google Gemini sharing a common
    `LLMClient` protocol.
  - Prompt template parameterized with: current code, recent fitness,
    opponent's observable behavior summary, banned-token list.
  - Response parsing: extracts the first ```cpp ... ``` fenced block; rejects
    responses with banned tokens before they reach the compiler.
  - Retry with exponential backoff on API / parse errors (max 3 attempts).
  - **Secret handling**: keys loaded from `.env` via `python-dotenv`; keys
    never logged; experiment logs redact any token that looks like an API key.
- `scripts/evolve.py`:
  - Loop: evaluate → prompt LLM → inject guards → sandbox compile → evaluate →
    accept-if-better → checkpoint.
  - Checkpoints every N generations (configurable, default 10).
  - Fitness plot PNG generated per checkpoint.
- Prompt library under `prompts/` with version-controlled templates.

### Tests
- Unit tests with a recorded-response mock (`responses` or `vcr.py`) so CI
  never actually calls the LLM API.
- Contract tests (opt-in, marked `@pytest.mark.live_api`) that hit the real
  API and are excluded from default `make test`.
- Redaction test: a fake API key `sk-ant-test-XXXX` injected into any logged
  field is replaced with `***REDACTED***`.

### Exit criteria
- [ ] 50-generation evolutionary run completes without manual intervention.
- [ ] Best-of-generation fitness trends upward (not strict monotone; ≥ 60%
      of generations show improvement or tie).
- [ ] No API key ever appears in `data/experiments/**`.

---

## 13. Milestone M11 — GPU Port & Profiling

**Goal**: Meaningful speedup on NVIDIA hardware.

### Deliverables
- `make build-linux-gpu` produces a working `swarmevolve` binary on a machine
  with NVIDIA HPC SDK.
- `#pragma acc parallel loop` on Query, Movement, Combat, Cleanup per
  [ARCHITECTURE.md](ARCHITECTURE.md).
- Managed-memory allocation so CPU and GPU see the same arrays.
- `scripts/profile.py` wrapping `nsys profile --stats=true` and extracting a
  CSV of kernel times.

### Tests
- Equivalence: CPU and GPU builds with the same seed produce traces that
  match within a documented FP epsilon (per-platform determinism; cross-platform
  exactness is explicitly NOT required — see [SPECIFICATION §7.6](SPECIFICATION.md)).
- Performance: 50-drone, 1000-tick match runs ≥ 10× faster on GPU than the
  macOS CPU reference. Regression gate: if GPU build becomes slower than
  previous tagged release by > 20%, CI fails.
- TDR stress test: intentionally-inefficient (but guard-bounded) AI does not
  trigger a driver reset.

### Exit criteria
- [x] GPU build passes full test suite.
- [x] Profile report checked into `docs/profiling/<date>.md`
      (`docs/profiling/2026-04-22.md`).
- [x] Per-platform determinism (CPU↔GPU FP-epsilon equivalence) verified
      on GB10 aarch64 at seed=42 × 200 ticks (byte-identical) and
      outcome+tick parity across seeds 0..4 × 300 ticks.
- [x] TDR resilience verified (heavy-work AI, 5 Gflops/match, no driver
      reset).
- [ ] **10× single-match perf gate: NOT MET — deferred to M12.**
      Honest finding (see `docs/profiling/2026-04-22.md`): at 50 drones
      the per-tick parallelism model is pinned at kernel-launch
      granularity on GB10; 99.26 % of wallclock is launch/context
      overhead. The tournament workload is naturally parallel-over-
      matches, and M12's batched evaluator is the right place to
      reopen this gate (outer `parallel loop` over match indices,
      amortising the per-launch cost across N matches in a single
      kernel). Codifying "10× single-match" as an M11 exit criterion
      was an architectural mis-match; the M12 plan already
      contemplates batched evaluation, so the perf gate travels with
      it rather than blocking M11 closure.

---

## 14. Milestone M12 — Tournament & Analysis

**Goal**: Comparative evaluation across multiple evolved AIs.

### Deliverables
- `scripts/tournament.py`: round-robin and Swiss pairings; outputs Elo-like
  ratings plus raw win-matrix.
- `scripts/analysis.py` + `scripts/analysis.ipynb`: fitness-curve plots,
  strategy clustering (pairwise behaviour distance), "most informative"
  matches picker. Video compilation was descoped — it requires
  substantial new code on top of `visualizer.py` and was not on the
  critical path for the exit criterion.
- `scripts/render_report.py`: stdlib-only Markdown report renderer.

### Tests
- [x] `test_round_robin_4ai_stable_across_3_reruns` — 4-AI tournament
      (pursuit, cluster, stationary, drift) produces byte-identical Elo
      ratings across three independent runs with the same seed_base.
- [x] `test_win_matrix_conservation` — accounting invariant.
- [x] `test_swiss_top1_matches_rr_top1_for_dominant_player` — dominance
      regime sanity.
- [x] `test_canonical_json_is_byte_stable_across_reruns` — byte-for-byte
      canonical record reproducibility (volatile fields excluded).
- [x] `test_events_log_has_start_and_end` — ExperimentLog integration.

### Exit criteria
- [x] Final report template renders from a completed experiment directory
      with zero manual edits (`tests/test_render_report.py`, 8 tests).

### Deferred to later milestones
- **10× single-match GPU perf gate** (inherited from M11). The
  tournament workload is naturally parallel-over-matches, but the
  batched-evaluator variant of the GPU engine (outer `parallel loop`
  over match indices with the per-drone loop as the inner kernel) is
  a substantial new codepath — replacing the `evaluate_fitness`
  process pool with a single batched GPU launch — and does not fit
  inside M12 alongside the scheduler/analysis/report stack. Tracked
  as a follow-up item; will be revisited when the next GPU-bound
  workload appears.
- **Video compilation of "most informative" matches.** The plan's
  original notebook-era wording implied stitching trace playback into
  an MP4 per "surprising" pairing. The surprise metric is now computed
  (`scripts/analysis.py:top_matches`) and dumped as JSON; trace
  replay + video stitching on top of the existing `visualizer.py`
  remains a stretch goal.

---

## 15. Cross-Cutting Practices

### 15.1 Testing Strategy

| Layer                | Tool                      | Gate      |
|----------------------|---------------------------|-----------|
| C++ unit             | GoogleTest                | PR must pass |
| C++ property / fuzz  | libFuzzer + ASan/UBSan    | nightly   |
| Python unit          | pytest + coverage ≥ 80%   | PR must pass |
| Python property      | Hypothesis                | PR must pass |
| Integration          | pytest end-to-end + Docker| PR must pass on Linux |
| Determinism          | SHA-256 of golden trace   | PR must pass |
| Visual regression    | PSNR vs PNG snapshots     | PR must pass |
| Sandbox escape       | hostile payloads          | PR must pass on Linux |
| Performance          | time-bound assertions     | nightly + tagged release |
| Live-API contract    | recorded cassettes default| manual opt-in |

- Coverage target: ≥ 80% Python, ≥ 70% engine C++ line coverage (measured via
  `llvm-cov`).
- Every bug fix lands **with** a regression test that would have caught it.
- No `sleep()` in tests; use event-driven waits.

### 15.2 CI / CD

- PR workflow: lint → build-{macos,linux} → test-{cpp,python} → docs-check.
- Nightly workflow: fuzz (30 min), full determinism matrix (seeds 0..99),
  sandbox-escape, GPU-equivalence (if self-hosted runner available).
- Release workflow: tag → build release binaries → publish Docker image →
  regenerate profile report → update GitHub Release notes.
- Branch protection on `main`: required checks, linear history, signed commits
  encouraged.

### 15.3 Security

- **SECURITY.md** describes disclosure process (private email, 90-day window).
- **Secrets** only in `.env` (git-ignored) and GitHub Actions secrets;
  never in logs or traces.
- **Dependencies** pinned + scanned (Dependabot weekly; `pip-audit` in CI).
- **Supply chain**: Docker images built from hash-pinned base images
  (`FROM nvidia/cuda:12.3.1-...@sha256:...`).
- **Sandbox invariants** (tested every PR):
  1. No network egress from AI code.
  2. No write outside `/work/out`.
  3. Wall-clock ≤ 10 s per match.
  4. Memory ≤ 512 MiB per match.
- **Code provenance**: every compiled AI binary traceable to an LLM response
  ID + prompt hash stored in the experiment log.

### 15.4 Documentation Discipline

- A PR that changes engine behavior must update `SPECIFICATION.md` in the same
  commit. CI `docs-check` job:
  - Greps the spec for every public symbol in `src/types.h` and fails if any
    are missing.
  - Validates all cross-reference links resolve.
  - Runs `markdownlint`.
- `CHANGELOG.md` updated per PR under `## Unreleased`.

### 15.5 Performance & Observability

- Every engine run emits a final stderr line:
  `METRICS ticks=<n> ms=<n> drones_a=<n> drones_b=<n> outcome=<tag>`.
- `scripts/bench.py` runs a fixed workload and records the metrics; CI tracks
  the trend.
- Regression budget: a PR that adds more than 10% wall-clock to
  `baselines/pursuit vs cluster, seed=0, 1000 ticks` must explain why.

### 15.6 Code Style

- C++17; `clang-format` config in repo; `-Wall -Wextra -Werror -Wshadow
  -Wpedantic`.
- Python 3.10+; `ruff` (replaces flake8 + black); `mypy --strict` on all
  `scripts/**`.
- Shell: `shellcheck` clean.
- No TODOs without an issue link: `// TODO(#123): ...`.

### 15.7 Reproducibility

- Every experiment directory includes:
  - Exact git SHA of repo.
  - SHA-256 of every AI source file used.
  - Contents of `requirements*.txt` and Docker image digest.
  - Hardware (CPU/GPU model), OS, and compiler versions.
- `scripts/reproduce.py <experiment_id>` rebuilds the environment and reruns
  the experiment; CI validates reproducibility weekly on a small preset.

### 15.8 Evolutionary-Loop Safety

- Hard cap on per-generation compile failures (default 5) before the loop
  aborts and surfaces the last successful checkpoint.
- Catastrophic-fitness guard: if fitness drops > 2σ below the running mean,
  roll back to the previous best and mark the generation as rejected.
- Human-in-the-loop checkpoint: every 10 generations, orchestrator emits a
  summary and awaits a `--continue` flag on the next invocation.

---

## 16. Risk Register

| Risk                                            | Likelihood | Impact | Mitigation |
|-------------------------------------------------|------------|--------|------------|
| Guard injector misses an edge-case loop → TDR   | med        | high   | libclang AST + adversarial corpus + sandbox timeout as backstop |
| Floating-point non-determinism on GPU           | med        | med    | Document per-platform determinism; require same target for reproduction |
| LLM API outage stalls evolution                 | high       | low    | Retry + exponential backoff; multi-provider; cached prompts |
| Sandbox escape                                  | low        | high   | Layered defenses; nightly hostile-payload tests |
| Cost blowout on LLM API calls                   | med        | med    | Budget caps in `llm_client.py`; dry-run mode for development |
| Baselines become dominant strategies and loop stalls | low   | med    | Diversify seed prompts; inject periodic exploration generations |
| GPU driver updates break nvc++ build            | low        | med    | Pin HPC SDK version in Dockerfile; rebuild image in a dedicated CI job |

---

## 17. Definition of Done (Project-Level)

- [ ] M0–M12 exit criteria all green on `main`.
- [ ] One end-to-end evolutionary run (≥ 50 generations) archived in
      `data/experiments/` with full reproducibility metadata.
- [ ] Tournament results comparing Claude-evolved vs Gemini-evolved AIs
      published in a README section and an `analysis.ipynb`.
- [ ] `CHANGELOG.md` has a `v1.0.0` section.
- [ ] Docker image tagged and published.
- [ ] Recorded demo video (`docs/demo.mp4`) linked from the README.

---

## 18. Open Decisions (Revisit Before M10)

These are intentionally deferred; decide when the corresponding milestone
begins, document in this section, and commit.

1. **LLM provider default**: Claude 3.5 Sonnet vs Opus for evolution?
   Trade-off: quality vs cost per generation.
2. **Prompt-engineering strategy**: single-turn "here's your code, improve it"
   vs multi-turn with tool calls inspecting traces?
3. **Co-evolution vs fixed baseline**: evolve Team A against a frozen Team B,
   or let both evolve simultaneously (red-queen dynamics)?
4. **Trace sampling**: record every tick (current spec) vs every Nth tick for
   long matches; affects disk usage at scale.
5. **Elo vs custom rating** for tournaments (depends on how often rock-paper-
   scissors cycles appear in practice).
