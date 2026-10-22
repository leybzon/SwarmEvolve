# legacy/m25: provenance

Frozen copy of the loop that produced M25 (`scripts/evolve_coevolve.py`),
taken from commit `7e539a4` (the last commit before the v2 study began).
It is the program run by arm A1 in E1. Nothing in this directory changes
after the session that created it (2026-10-22).

## Files copied byte-for-byte from 7e539a4

| Snapshot path | Source at 7e539a4 |
| --- | --- |
| `evolve_coevolve.py` | `scripts/evolve_coevolve.py` |
| `dual_llm.py` | `scripts/dual_llm.py` |
| `tactic_spec.py` | `scripts/tactic_spec.py` |
| `journal.py` | `scripts/journal.py` |
| `telemetry_aar.py` | `scripts/telemetry_aar.py` |
| `lint_ai_tokens.py` | `scripts/lint_ai_tokens.py` |
| `inject_guards.py` | `scripts/inject_guards.py` |
| `fitness.py` | `scripts/fitness.py` |
| `prompts/planner_analyze_aar_v2.md` | `prompts/planner_analyze_aar_v2.md` |
| `prompts/coder_implement_tactic.md` | `prompts/coder_implement_tactic.md` |
| `src/types.h` | `src/types.h` (pasted into the coder prompt) |
| `src/ai_abi.h` | `src/ai_abi.h` (pasted into the coder prompt) |
| `docs/journal_schema.json` | `docs/journal_schema.json` (read by `journal.py`) |

`scripts/llm_client.py` is deliberately not copied: model calls go through
an injected client (see `client.py`).

The commit that added these files contains them exactly as they were at
7e539a4; every later edit is in the following commits and listed in
`CHANGES.md`. To see every change:

    git diff 7e539a4:scripts/evolve_coevolve.py legacy/m25/evolve_coevolve.py
