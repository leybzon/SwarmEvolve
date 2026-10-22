# legacy/m25: every change to the 7e539a4 copies

The commit that created `legacy/m25/` holds the files exactly as they were
at 7e539a4 (see `PROVENANCE.md`). The changes below come in the two
following commits (changes 1-3, then change 4). Nothing else differs.

Decision logic, scoring (a crash or timeout scores 0), 10 matches per
evaluation, the seed schedule (`seed + round * 1000`), journal lessons
(last 3 entries of the evolving team), prompt rendering and the acceptance
rule (`candidate > champion - 0.05`, champion fitness never re-measured)
are unchanged. `tests/test_legacy_m25.py` pins them against golden files.

## Imports resolve to the snapshot

- `legacy/` and `legacy/m25/` are packages; run the loop with
  `python -m legacy.m25.evolve_coevolve`.
- `evolve_coevolve.py`, `dual_llm.py`: the `sys.path` insertions are gone and
  sibling imports are relative (`from . import dual_llm`), so they load the
  snapshot's copies and never `scripts/`.
- `dual_llm.py`: prompts and the two headers pasted into the coder prompt
  come from `legacy/m25/prompts/` and `legacy/m25/src/`.
- `journal.py`: the schema comes from `legacy/m25/docs/journal_schema.json`.
- `client.py` (new) holds the three pieces of `scripts/llm_client.py` the
  loop used (`LLMResponse`, `LLMClient`, `MockClient`, copied unchanged)
  plus `RecordingClient`. `dual_llm.py` and `evolve_coevolve.py` import it
  as `llm_client`.

## Execution change 1: fixed-opponent mode

`evolve_coevolve(..., evolve_team="A" | "B")`, CLI `--evolve-team`. Only that
team evolves; the other keeps its init champion. The alternating schedule
is kept: round numbers, seeds and the evolving team's rounds are those M25
used, and the frozen team's rounds are skipped (no LLM call, no evaluation,
no journal entry). With `evolve_team="B"` this is M25 Team B's process,
because M25's Team A never changed after round 0. `evolve_team=None` is
M25's alternating co-evolution.

## Execution change 2: matches in the canonical eval container

`fitness.py`: the host compile-and-run path (`_find_compiler`, `_render`,
`_compile`, `_run_one_match`, `_worker_batch`) is replaced by
`_play_in_container`, which hands the seeds to `scripts/fitness.py` with
`backend="sandbox"` and `under_test=None` and takes back each match's
outcome. The snapshot still scores the outcomes itself with M25's table
(A_WIN +1, B_WIN -1, DRAW 0, CRASH 0, TIMEOUT 0) and computes the mean,
stdev and bootstrap CI as before. Tick cap 1000 and the 10 s per-match
limit are M25's.

Consequences of running in the container, not of the snapshot's logic:

- The container injects loop guards into both programs before compiling
  (the candidate already carries M25's guards; re-injection is a no-op).
  A guard or compile failure raises `CompileError`, which the loop turns
  into a stalled round, as M25 did with its host compile failures. The
  error text is the container's compile log, not the host compiler's.
- Compiler and flags are the eval image's (`compile_flags.eval_flags`):
  g++ 11.4 and `-ffp-contract=off` in addition to M25's flags.
- `FitnessResult` gains `backend` and `platform` (the fingerprint and its
  canonical/dev label); the loop does not read them.
- A container that yields no usable result raises `InfraError`.
  `evolve_coevolve.py` lets it end the run instead of journaling a stall,
  since it says nothing about the candidate. M25 had no such failure
  class (its host path had none); every other exception still stalls the
  round as before.

## Execution change 3: injected model clients

`evolve_coevolve()` takes `planner_client` and `coder_client` instead of
model names and no longer builds `AnthropicClient`s. The journal's `model`
field is `f"{planner_client.model}+{coder_client.model}"`, as before with
model names. The CLI loads a factory with `--client-factory module:attr`,
called as `factory(role, model)`. `--planner-model` and `--coder-model`
have no defaults (M25's planner, claude-sonnet-4-20250514, was retired on
2026-06-15). `dual_llm.py` still passes `max_tokens=4096`; see `client.py`
for what M25 sent per call and how an injected client treats `max_tokens`.

## Execution change 4: resume from the last completed round

Added at the user's request on 2026-10-22, before the snapshot froze.
`evolve_coevolve(..., resume=True)`, CLI `--resume`.

- At the start of every round the loop appends a checkpoint to
  `out_dir/state.jsonl`: the next round, both champions' code and fitness,
  and the run settings. The file is append-only; a torn last line is
  ignored on resume.
- `resume=True` restores the last checkpoint and refuses if the run
  settings differ (init programs, models, rounds, matches, seed, modes).
- A round whose journal entry was written after the last checkpoint is
  taken as finished: its verdict, fitness and `candidate.cpp` set the
  champion, as the loop itself would have.
- An interrupted round's directory is first copied to
  `out_dir/interrupted/round_NNNN_k/`, then the round re-runs. If its plan and
  code were already written (`candidate.cpp` exists), they are reused and
  only evaluated again; no model call is repeated, and the container's
  evaluation is deterministic, so the decision is the one the round would
  have reached. If the round stopped earlier, its model calls are made
  again; a plan whose coder call failed was never saved, so the planner is
  asked again.
- Each resume appends a `resume` event to `state.jsonl` naming the round,
  any round taken from the journal, and the copy.
- Without `resume`, an `out_dir` that already holds `state.jsonl` or a
  journal is refused instead of appended to.

The golden prompts and decisions are unchanged; `tests/test_legacy_m25.py`
stops the scenario at four points and checks that each resumed run ends
with the same prompts, journal and champion.
