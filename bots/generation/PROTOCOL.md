# Bot generation protocol

Fixed on 2026-10-23, before any bot was generated. Its SHA-256 and that of
`design_prompt.md` are recorded in `docs/paper/RESEARCH_BRIEF.md`; any later
change is a deviation and goes to `prereg/DEVIATIONS.md`. Prompt 8a runs it,
once the study's hardened LLM client (prompt 8) exists.

## Purpose

The study's reference opponents replace the lost M22 and Track A champions.
They are written by AI models from families other than Claude's. No person,
no Claude model and not the study's evolution loop writes any of them, and
this fixed procedure picks them before any arm runs, so none is tied to an
arm being compared. It produces three things:

- **R1**, the fixed opponent of E1 and Team A's start in C3;
- the **panel bots** of the held-out panel;
- a **same-author set** for an exploratory near-transfer check only.

## 1. Authors

| Order | Family | Model | Reasoning effort |
| --- | --- | --- | --- |
| 1 | Google | `gemini-4-argon` | high |
| 2 | OpenAI | `gpt-6.1-sol` | high |

Never a Claude model. Reasoning effort is set to "high" through each API's
own reasoning-effort setting; the exact request parameters are stored with
every request. Keys come from `SWARMEVOLVE_GEMINI_API_KEY` and
`SWARMEVOLVE_OPENAI_API_KEY` and are never stored or printed.

## 2. Design prompt

`design_prompt.md` is sent as the single user message of every sample, byte
for byte, with no system prompt, to every author. It holds the game rules
(SPECIFICATION.md §1-3 and §7 as the engine implements them, without the
specification's design commentary, plus the study's scoring), `bots/RULES.md`,
`src/types.h`, `src/ai_abi.h`, `bots/template.cpp`, and `pursuit_v1` and
`cluster_v1` as example programs. It asks for the strongest bot the author
can write, as one C++ file, to face a range of unknown opponents.

It contains no tactic hints. The example programs are shown with their
comments removed by `scripts/strip_comments.py`, because their comments name
and explain their strategies; their code, including its identifier names,
is unchanged. `scripts/build_design_prompt.py --check` shows the file was
assembled from these sources.

## 3. Sampling and repairs

- **Samples.** 12 independent samples per author. Each sample is a new
  conversation that starts with the design prompt; samples share nothing.
- **Sampling settings.** Each API's defaults: no temperature, top-p, top-k
  or seed parameter is sent. Maximum output is set to 64,000 tokens (or the
  API's maximum if lower, recorded).
- **Order.** Requests go out in sample order: sample 1 of each author in the
  order of §1, then sample 2, and so on. A later batch follows the earlier
  ones. Sample order is also the last tie-breaker in §4.
- **Extraction.** The program is the first ```` ```cpp ```` block of the reply
  (or, if there is none, the first fenced block). A reply without one fails
  with the linter error `no C++ code block found`.
- **Build check.** `scripts/validate_bot.py --build-only`: lint, guard
  injection and compilation in the canonical container.
- **Repairs.** Up to 2 per sample. A repair adds one user message to the
  sample's conversation, exactly:

  ```
  Your bot did not pass the build check. Fix it and reply with the complete corrected file in a single ```cpp code block. The error output was:

  <error text>
  ```

  where `<error text>` is the card's `error_text`: the last 4,000 characters
  of the linter, guard-injector or compiler output. Nothing else is sent.
- **Valid candidate.** A sample whose program passes the build check, after
  at most 2 repairs. Others are invalid and are kept in `raw/` only.
- **Failed requests.** Transport errors and rate limits are retried by the
  client and are not samples. A reply that arrives is the sample, whatever
  it holds (a refusal or a truncated reply fails extraction and may be
  repaired).
- **No feedback.** No match result, score, rank or other author's code is
  ever sent to an author.

## 4. Validation and ranking

All matches run in the canonical container on the canonical host
(`prereg/PLATFORM.json`) with 10 drones a side, the default arena, a
1,000-tick cap and a 10 s limit per match.

- **Report cards.** Every valid candidate gets a full card from
  `scripts/validate_bot.py`: 1,000 seeds with both side assignments against
  each of `pursuit_v1` and `cluster_v1` (2,000 matches per baseline).
- **Round robin.** Every valid candidate plays every other valid candidate
  and both baselines, 400 matches per pairing: 200 seeds, each with both side
  assignments. A candidate's score in a match is +1 win, 0 draw, -1 loss. An
  invalid match (crash or timeout) is a loss for the candidate; when both
  programs are candidates, it is a loss for both.
- **Ranking keys,** in order:
  1. mean score over all of the candidate's pairings, each pairing weighted
     equally (other valid candidates and both baselines);
  2. mean survivor differential (own drones alive minus the opponent's at
     the end; an invalid match counts as minus the team size), weighted the
     same way;
  3. fewer repairs;
  4. earlier sample order (§3).

## 5. Selection

- **R1** is the top-ranked candidate whose report card has a mean score of at
  least 0.8 against each of `pursuit_v1` and `cluster_v1`.
- **Panel bots** are the 3 top-ranked valid candidates from each family other
  than R1's (all of them if a family has fewer than 3). They need not reach
  0.8.
- **Same-author set**: the 3 valid candidates from R1's family ranked next
  below R1. Used only for an exploratory near-transfer check; never in the
  panel and never a training opponent.
- Other candidates are not used.

## 6. Fallbacks

- **No candidate qualifies as R1:** run one more batch of 12 samples per
  author with the same prompt and settings, then validate, re-rank all
  candidates of all batches and re-apply §5. If still none qualifies, stop
  and report; nothing more is generated without a new decision.
- **R1 too strong** (by the pilot's criterion, prompt 10): the next
  qualifying candidate from R1's family, in rank order, replaces it. The
  same-author set becomes the 3 valid candidates from that family ranked
  next below the new R1, leaving out the replaced bot. The panel does not
  change.
- **R1 too weak** (by the pilot's criterion): run one more batch of 12
  samples per author at the highest reasoning effort each API offers, then
  validate, re-rank all candidates of all batches and re-apply §5.
- Every fallback is logged in `raw/log.jsonl`; after the freeze it is also a
  deviation.

## 7. Freezing

For R1, each panel bot and each same-author bot:

1. Strip comments with `scripts/strip_comments.py` (the SPDX line stays).
2. Check that the stripped bot plays exactly like its original:
   `scripts/validate_bot.py --seeds 200 --seed-base 7300000` on both, and the
   two `matches.jsonl` files must be byte-identical. If they are not, stop:
   the stripper is at fault.
3. Save the stripped bot as `bots/frozen/<family>-b<batch>-s<NN>.cpp`
   (`google-b1-s07.cpp`), with its report card.
4. Record it in `bots/frozen/MANIFEST.json` (file, SHA-256, role, author
   model, family, batch, sample, repairs, rank, ranking scores, card) and
   `bots/frozen/SHA256SUMS`.

R1's hash goes into the brief before any pilot run. After freezing, nothing
in `bots/frozen/` or `bots/generation/raw/` is edited.

## 8. Records

Everything stays in `bots/generation/raw/`, append-only:

- `raw/<model>/b<batch>-s<NN>/`: for each request `request_<k>.json` (the exact
  request body, without the key) and `response_<k>.json` (the exact response
  body), each extracted `candidate_<k>.cpp` with its build card, and the full
  report card of a valid candidate;
- `raw/ranking/`: every round-robin match result;
- `raw/log.jsonl`: one line per request, build check, repair, fallback and
  selection, with time and cost.

## 9. Seeds

Seeds 7,000,000 to 7,999,999 are reserved for bot generation:

| Use | Seeds |
| --- | --- |
| Report cards | 7,100,000 - 7,100,999 |
| Round robin | 7,200,000 - 7,200,199 |
| Strip check | 7,300,000 - 7,300,199 |

The study's training and held-out seeds lie outside this block.

## 10. Budget

$25 for all generation, repairs and fallbacks, enforced as a hard cap by
the budget governor. Before each request the governor checks that its
largest possible cost (the prompt plus 64,000 output tokens at list price)
fits in what is left; if it does not, generation stops and is reported, and
no selection is made from a partial batch without a new decision. Spend is
recorded per request. As for every paid run, the plan and projected cost are
put to the user first.
