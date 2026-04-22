# Frozen Baselines

This directory holds **frozen reference copies** of AI implementations for
regression testing. They are *not* part of the normal build (the Makefile's
`SRC_AI` wildcard only matches `src/a/*.cpp` and `src/b/*.cpp`).

## Files

| File              | Origin                  | Strategy                          |
|-------------------|-------------------------|-----------------------------------|
| `pursuit_v1.cpp`  | `src/a/team_a_ai.cpp` @ M3 | Nearest-enemy pursuit          |
| `cluster_v1.cpp`  | `src/b/team_b_ai.cpp` @ M3 | Weighted cluster + focus-fire  |

## Usage

The M3 test `tests/test_baselines.py` copies these files into `src/a/` /
`src/b/` (or a temp dir + custom CXX invocation) before running matches,
so that later changes to the live AI source never silently invalidate
regression numbers.

## Freezing a new baseline

1. Copy the live file into this directory with a version suffix
   (e.g. `pursuit_v2.cpp`).
2. Update `tests/test_baselines.py` to reference the new file and record
   expected win/loss/draw windows.
3. Mention the version bump in `CHANGELOG.md`.

Changes to any existing `*_v<N>.cpp` require a "baseline-break" commit
message and must come with an explanation of why the frozen version is
being revised (typically a bug fix that the regression windows can
tolerate without reopening M3).
