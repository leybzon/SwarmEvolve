# Contributing to SwarmEvolve

Thank you for considering a contribution. Please read this file and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) before opening a PR.

## Ground rules

1. **Spec first.** Behavior changes update `SPECIFICATION.md` in the same PR.
2. **Test first.** A PR that fixes a bug must add a regression test that
   would have caught it.
3. **Small PRs.** Aim for < 400 LOC of non-generated code per PR.
4. **Deterministic tests only.** Never use wall-clock sleeps or unseeded RNGs.
5. **Never weaken safety layers** (loop guards, POD ABI, sandbox) without
   replacing with an equivalent layer; see `IMPLEMENTATION_PLAN.md §0.4`.

## Dev setup

```bash
git clone https://github.com/leybzon/SwarmEvolve.git
cd SwarmEvolve
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
make build-macos   # or build-linux-cpu
make test
```

## Branches and commits

- Work on a feature branch: `feature/<short-name>` or `fix/<short-name>`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, `test:`, `build:`, `ci:`.
- Rebase on `main` before opening a PR; keep history linear.
- Signed commits (`git commit -S`) are encouraged.

## CI gates

Every PR must pass:

- `ruff`, `ruff format --check`, `mypy --strict`, `clang-format` (lint job)
- macOS clang++ and Linux g++ builds
- Full C++ and Python test suites
- `docs-check` (no broken internal doc links, required files present)

Nightly / on-demand jobs (see `.github/workflows/`):

- Fuzz (libFuzzer + ASan)
- Determinism matrix (seeds 0..99)
- Sandbox escape suite (requires Linux + Docker)
- GPU equivalence (requires self-hosted NVIDIA runner)

## Safety-critical review

PRs that touch any of the following require review from a CODEOWNER:

- `src/` (engine and AI ABI)
- `scripts/inject_guards.py` (loop-guard injection)
- `scripts/sandbox.py` or `docker/` (container sandbox)
- `.github/workflows/` (CI gating)

## Reporting security issues

See [SECURITY.md](SECURITY.md).

## Code of conduct

Be respectful. Technical disagreements are welcome; personal attacks are not.
