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
