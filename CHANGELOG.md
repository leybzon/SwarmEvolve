# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
