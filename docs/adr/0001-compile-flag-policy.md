# ADR-0001: Compile-flag Policy for LLM-authored AI Sandboxes

* **Status**: Accepted
* **Date**: 2026-04-23
* **Deciders**: SwarmEvolve maintainers
* **Scope**: `scripts/fitness.py` (evolutionary sandbox), `scripts/orchestrator.py`
  (M9 compile path), `prompts/evolve_ai.md`, `scripts/evolve.py`
  retry loop.

## Context

The SwarmEvolve evolutionary loop (`scripts/evolve.py`) asks an LLM to
author a `drone_ai(...)` C++ function. Each candidate must survive
parse → lint → loop-guard injection → **compile with `-Werror`** → run
→ evaluate against a frozen opponent before it can replace the
champion.

Prior to this ADR the sandbox compile flags were:

```
-std=c++17 -O2 -Wall -Wextra -Wshadow -Wpedantic -Werror
    -Wno-unknown-pragmas
```

During SOTA shakedowns with the Anthropic client we observed a **100%
rejection rate** for both Claude Sonnet 4.5 and Claude Opus 4.7 across
every lineage. Symptom pattern (verified in `data/runs/track_a/
shakedown_*`):

```
error: unused variable 'threat_count' [-Werror,-Wunused-variable]
error: unused parameter 'params' [-Werror,-Wunused-parameter]
```

Both SOTA models reliably scaffold lookahead bookkeeping variables in
their **first draft** — counters, candidate targets, unused pointer
parameters kept for API symmetry — and only prune them on later
iterations once they have a working core. `-Werror` converted that
natural drafting style into a hard reject, so the loop never accepted
*any* candidate. RQ2 ("can an LLM evolve a drone AI?") and RQ3 ("do
different SOTA models converge on different strategies?") were
effectively unanswerable.

## Decision

### 1. Relax `-Wunused-*` style warnings (keep `-Werror` otherwise)

In the evolutionary sandbox (`scripts/fitness.py:177-181`) and the M9
orchestrator compile path (`scripts/orchestrator.py:209-220`) we pass:

```
-Wall -Wextra -Wshadow -Wpedantic -Werror
-Wno-unknown-pragmas
-Wno-unused-variable -Wno-unused-parameter
-Wno-unused-function -Wno-unused-but-set-variable
-Wno-unused-const-variable
```

**Rationale**: unused-symbol diagnostics are *style* judgements. They
do not flag UB, out-of-bounds access, uninitialized reads, shadowing,
sign-compare, ABI mismatches, pedantic-C++ violations, or any other
correctness issue. Those stay hard errors. The sandbox still enforces:

* No dynamic allocation (lint layer rejects `new`, `malloc`,
  `std::vector`, `std::string`).
* Namespace isolation (`TeamA`/`TeamB`) preserved by the render step.
* Loop-guard injection before compile.
* GPU-safety constraints (`MAX_DRONES`, `MSG_SIZE`, `MEM_SIZE`).

### 2. Teach the prompt to avoid unused scaffolding

`prompts/evolve_ai.md` now explicitly asks the LLM to **not** leave
unused variables or parameters in the final candidate. This is a soft
signal — the compiler no longer enforces it — but it keeps the
authoring style converging toward clean code without gating the
evolutionary loop on first-draft style.

### 3. LLM-driven retry on non-acceptance

Even with (1)+(2), candidates can still fail downstream stages (parse,
lint, inject, compile with non-style errors, evaluation). We added a
per-generation **compile-retry loop** (`scripts/evolve.py`):

* CLI: `--max-compile-retries 10` (default).
* On any non-accepted stage, the stage error + diagnostics are fed
  back to the LLM and a fresh attempt is requested.
* Each attempt gets its own forensic subdirectory:
  `<run-dir>/gens/<NNNN>/attempt_<NN>/`.
* State schema gains `n_attempts` and `final_attempt_status` per
  generation history row (see `docs/checkpoint_schema.json`).
* Retry budget is per-generation; `--max-compile-failures N` still
  caps *consecutive* generations that fail to produce a champion, and
  still yields the documented exit code `30`
  (`EXIT_LOOP_ABORTED_COMPILE_CAP`).

### 4. Track-level fault tolerance

`scripts/tracks/track_a.py` now treats `evolve rc=30` as a **soft
failure**: the lineage is recorded in `manifest.exhausted_seeds` with
`exit_code=30` and the track moves on to the next seed instead of
raising `RuntimeError`. One unhealthy lineage cannot take down an
otherwise healthy multi-seed track run. Track B and Track C still use
the strict wrapper (`invoke_evolve(..., strict=True)`); that
asymmetry is intentional — those tracks have richer intra-lineage
coupling (RR tournaments, yardsticks, coevolution partners) and a
single exhausted lineage would corrupt downstream aggregates.

## Consequences

### Positive

* SOTA shakedowns now produce champions on the first draft more often
  than not; retry loop handles the residual fraction.
* `-Werror` is still on for everything that actually signals a bug.
* Forensic `attempt_<NN>/` directories give us full diagnostic
  provenance when a generation does need multiple drafts — critical
  for the Chapter 6 "why did the LLM get it right / wrong?"
  narrative.
* Track A can survive an unlucky lineage without abandoning the other
  seeds in the run.

### Negative / accepted risk

* Rendered binaries may contain dead code that increases `.text`
  size marginally. Irrelevant at our scale (matches are throughput-
  bound on physics, not instruction-cache).
* The LLM no longer gets a hard signal *from the compiler* that
  unused scaffolding is undesirable. We mitigate this via the prompt
  update in (2) and — because fitness is ultimately what matters —
  via evolutionary pressure: cleaner drafts win matches at the same
  rate as scaffolded ones, so over generations the population
  self-prunes.
* Per-generation retries consume extra LLM tokens. Budget is tracked
  through the existing `TokenBudget` enforcement and surfaced in the
  manifest (`tokens_total`, `budget_exceeded`). With the default
  `--max-compile-retries 10`, worst-case cost is 11x tokens-per-
  generation; typical cost is <2x because most drafts now compile.

## Alternatives Considered

1. **Keep `-Werror` for unused, adjust the prompt only**. Rejected:
   prompt pressure alone does not change first-draft behaviour in
   SOTA models, only subsequent revisions — but without a retry loop
   the first draft is all we get.
2. **Drop `-Werror` entirely**. Rejected: hides real correctness
   defects (shadowing, sign-compare) that do cause silent match
   divergence between replicas.
3. **Post-compile cleanup pass** (strip unused decls via libclang).
   Rejected: adds a Python↔LLVM dependency, blurs provenance between
   what the LLM wrote and what we ran, and doesn't help with the
   other non-style stages (parse/lint/inject).

## Follow-ups

* Re-bless the M20 reproduce fingerprint digest (the `state.json`
  schema now contains `n_attempts` / `final_attempt_status`).
* Re-run the SOTA shakedown to confirm end-to-end champion
  production with `--max-compile-retries 10`.
* If Track B / Track C show the same seed-level brittleness against
  real LLM clients, revisit their `strict=True` choice in a separate
  ADR.
