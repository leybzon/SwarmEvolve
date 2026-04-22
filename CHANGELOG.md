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
