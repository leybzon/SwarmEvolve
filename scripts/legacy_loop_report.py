#!/usr/bin/env python3
"""Render reports/legacy/LEGACY_LOOP.md from the frozen M25 loop's golden files.

Every table, excerpt and number in the report is read from
``tests/fixtures/legacy_m25/golden/`` (what ``legacy/m25`` sent and decided
in the characterisation scenario) or from the snapshot's source; the prose
around them is fixed text in this script. ``--check`` exits 1 if the
committed report differs from a fresh render.

    python scripts/legacy_loop_report.py          # write the report
    python scripts/legacy_loop_report.py --check  # verify it is current
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "legacy_m25" / "golden"
INPUTS = REPO_ROOT / "tests" / "fixtures" / "legacy_m25" / "inputs"
SNAPSHOT = REPO_ROOT / "legacy" / "m25"
OUT = REPO_ROOT / "reports" / "legacy" / "LEGACY_LOOP.md"

AAR_HEAD = "# After-Action Report (Last Generation)\n\n"
LESSONS_HEAD = "# Lessons from Prior Generations\n\n"
# Tactics the brief (D4) says the prompt seeds, as they are worded in it.
TACTIC_TERMS = ("flanking", "bait", "striker", "formation", "kite", "kiting", "spread")
# A line only the evolving team's programs contain: pursuit_v1's header and
# the scripted coder's standoff test. Neither may appear in a prompt.
OWN_CODE_MARKERS = (
    '"Nearest-Enemy Pursuit" baseline',
    "if (me.cooldown > 0 && best_d <",
)


# ---------------------------------------------------------------------------
# Reading the golden files
# ---------------------------------------------------------------------------


def _between(text: str, head: str, end: str) -> str | None:
    if head not in text:
        return None
    rest = text.split(head, 1)[1]
    return rest.split(end, 1)[0]


def _block(text: str, first: int, last: int) -> str:
    """Lines first..last (1-based, inclusive) as a quoted block."""
    body = text.splitlines()[first - 1 : last]
    return "\n".join(f"> {line}".rstrip() for line in body)


def _line_of(path: Path, needle: str) -> tuple[int, str]:
    hits = [(i, ln) for i, ln in enumerate(path.read_text().splitlines(), 1) if needle in ln]
    if len(hits) != 1:
        raise SystemExit(f"expected one line containing {needle!r} in {path}, got {len(hits)}")
    return hits[0]


def _cite(path: Path, needle: str) -> str:
    """`path:line` plus the stripped source line, for quoting code."""
    lineno, line = _line_of(path, needle)
    return f"`{path.relative_to(REPO_ROOT)}:{lineno}`: `{line.strip()}`"


def _load() -> dict[str, Any]:
    calls = json.loads((GOLDEN / "calls.json").read_text())
    for c in calls:
        c["text"] = (GOLDEN / c["file"]).read_text()
    return {
        "calls": calls,
        "decisions": json.loads((GOLDEN / "decisions.json").read_text()),
        "journal": [json.loads(x) for x in (GOLDEN / "journal.jsonl").read_text().splitlines()],
        "opponent": (INPUTS / "opponent_a.cpp").read_text(),
    }


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _context_table(calls: list[dict[str, Any]], opponent: str) -> str:
    rows = [
        "| Prompt | AAR section | Own code | Opponent source | Journal lessons | "
        "Tactic terms present |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in calls:
        text = c["text"]
        aar = _between(text, AAR_HEAD, "\n\n# ")
        aar_cell = "no section" if aar is None else f"`{aar.strip()}`"
        own = "yes" if any(m in text for m in OWN_CODE_MARKERS) else "no"
        opp = "yes, full file" if opponent in text else "no"
        lessons = _between(text, LESSONS_HEAD, "\n\n---")
        if lessons is None:
            lessons_cell = "no section"
        elif lessons.strip() == "(none)":
            lessons_cell = "`(none)`"
        else:
            n = len(lessons.strip().splitlines())
            lessons_cell = f"{n} line{'s' if n != 1 else ''}"
        terms = [t for t in TACTIC_TERMS if re.search(rf"\b{t}", text, re.IGNORECASE)]
        rows.append(
            f"| `{c['file'].split('/')[-1]}` | {aar_cell} | {own} | {opp} | {lessons_cell} | "
            f"{', '.join(terms) or 'none'} |"
        )
    return "\n".join(rows)


def _champion_block_table(calls: list[dict[str, Any]]) -> str:
    rows = [
        "| Prompt | Champion Fitness | Last Generation | Last Fitness | Last Hypothesis (start) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in calls:
        if c["role"] != "planner":
            continue
        fields = dict(re.findall(r"^\*\*(.+?):\*\* (.*)$", c["text"], re.MULTILINE))
        hyp = fields["Last Hypothesis"]
        rows.append(
            f"| `{c['file'].split('/')[-1]}` | {fields['Champion Fitness']} | "
            f"{fields['Last Generation']} | {fields['Last Fitness']} | "
            f"{hyp[:48]}{'…' if len(hyp) > 48 else ''} |"
        )
    return "\n".join(rows)


def _decision_table(decisions: dict[str, Any], journal: list[dict[str, Any]]) -> str:
    evals = {e["round"]: e for e in decisions["evaluations"]}
    entries = {e["generation"]: e for e in journal}
    stalls: dict[int, str] = {}
    for line in decisions["log"]:
        m = re.search(r"(lint-failed|fitness-failed|dual-llm-failed) round=(\d+)", line)
        if m:
            stalls[int(m.group(2))] = m.group(1)
    rows = [
        "| Round | Seeds | Scripted outcomes (A_WIN/B_WIN/DRAW/CRASH/TIMEOUT) | "
        "Team B fitness | Journal verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rnd in sorted(decisions["round_dirs"]):
        r = int(rnd.split("_")[1])
        ev = evals.get(r)
        if ev is None:
            seeds = "not evaluated"
            counts = "n/a"
        else:
            seeds = f"{ev['seed_base']}–{ev['seed_base'] + ev['n_matches'] - 1}"
            if isinstance(ev["outcomes"], list):
                counts = "/".join(
                    str(ev["outcomes"].count(o))
                    for o in ("A_WIN", "B_WIN", "DRAW", "CRASH", "TIMEOUT")
                )
            else:
                counts = "compile failure"
        entry = entries.get(r)
        fitness = "n/a" if entry is None else f"{entry['fitness']:.1f}"
        verdict = entry["verdict"] if entry else f"none ({stalls.get(r, 'stalled')}, not journaled)"
        rows.append(f"| {r} | {seeds} | {counts} | {fitness} | {verdict} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render() -> str:
    d = _load()
    calls, decisions, journal, opponent = d["calls"], d["decisions"], d["journal"], d["opponent"]
    by_file = {c["file"].split("/")[-1]: c["text"] for c in calls}
    p3, p13 = by_file["r0003_planner_1.md"], by_file["r0013_planner_1.md"]
    p5b, c3 = by_file["r0005_planner_2.md"], by_file["r0003_coder_1.md"]
    n_planner = sum(1 for c in calls if c["role"] == "planner")
    n_coder = len(calls) - n_planner
    max_tokens = sorted({c["max_tokens"] for c in calls})
    rounds = sorted({c["round"] for c in calls})
    evals = decisions["evaluations"]
    loop = SNAPSHOT / "evolve_coevolve.py"
    fit = SNAPSHOT / "fitness.py"
    dual = SNAPSHOT / "dual_llm.py"

    def at(text: str, needle: str) -> int:
        return _line_of_text(text, needle)

    aar_l = at(p13, "# After-Action Report (Last Generation)")
    champ_l = at(p13, "## Current Champion")
    explore_l = at(p3, "### Option 2: EXPLORE")
    next_l = at(p3, "# What Happens Next")
    opp_l = at(p3, "## Opponent")
    retry_l = at(p5b, "Your previous response failed parsing")
    coder_fixed_l = at(c3, "The opponent is **fixed**")
    coder_noaar_l = at(c3, "You will **NOT** see the After-Action Report")
    coder_kite_l = at(c3, "kite at range")
    first_eval = evals[0]

    return f"""# The M25 loop as it ran: what it sent and how it decided

Generated by `scripts/legacy_loop_report.py` from the golden files in
`tests/fixtures/legacy_m25/golden/` and the frozen source in `legacy/m25/`.
Do not edit by hand.

## Scope and method

`legacy/m25/` is the M25 co-evolution loop copied from commit 7e539a4, with
three execution changes only (fixed-opponent mode, matches in the eval
container, injected model clients; `legacy/m25/CHANGES.md`). It is arm A1.

`tests/legacy_m25_scenario.py` ran it as A1 will run: Team B evolves, Team A
is frozen, seed 42, 10 matches per evaluation, relative acceptance, strict
reflection on (as M25 ran). Recording mock clients answered the model calls
and scripted outcomes stood in for the container. Over rounds
{", ".join(map(str, rounds))} the loop made {n_planner} planner calls and
{n_coder} coder calls; every prompt is stored verbatim under
`golden/prompts/`, and `tests/test_legacy_m25.py` fails if any of them, or any
decision, changes.

This report describes the loop's code and prompts. It replaces an audit of
old M25 run data; it says nothing about what M25's models answered.

Two inputs are stand-ins. M22 gen 33, A1's frozen opponent, is not on this
host (`data/runs/m22_rq1_100gen/` is absent), so `cluster_v1` plays Team A in
the scenario. Team B starts from `pursuit_v1`, as in M25 and A1. The prompts'
structure does not depend on which programs these are.

## What each prompt contained

Every prompt the loop sent, checked mechanically. "Own code" means any
version of Team B's program: the start program or a later champion.
"Tactic terms" are the brief's D4 terms found anywhere in the prompt; a
coder prompt embeds the planner's plan, so some of its terms (round 5's
"spread") come from the scenario's scripted plan rather than the template.

{_context_table(calls, opponent)}

Every call asked for `max_tokens` = {", ".join(map(str, max_tokens))}. Per
`legacy/m25/client.py`, M25's client sent each prompt as one user message with
no system prompt and no temperature (API default).

### Planner prompt

The planner prompt (`legacy/m25/prompts/planner_analyze_aar_v2.md`) carries,
in order: the team letter, the opponent's full source, a fixed ABI and
combat summary, the AAR slot, the journal lessons, a "Current Champion" block
of numbers, a REFINE/EXPLORE guide with worked tactic examples, the OODA
protocol, the JSON schema and a scoring rubric. It never carries Team B's own
program.

The opponent section opens like this and continues with the whole file
(`r0003_planner_1.md`, lines {opp_l}–{opp_l + 4}):

{_block(p3, opp_l, opp_l + 4)}

The AAR and lessons sections in the last round (`r0013_planner_1.md`, lines
{aar_l}–{aar_l + 9}):

{_block(p13, aar_l, aar_l + 9)}

The champion block in the same prompt (lines {champ_l}–{champ_l + 5}):

{_block(p13, champ_l, champ_l + 5)}

The EXPLORE option seeds specific tactics (`r0003_planner_1.md`, lines
{explore_l}–{explore_l + 15}):

{_block(p3, explore_l, explore_l + 15)}

What the prompt says happens next (lines {next_l + 2}–{next_l + 8}):

{_block(p3, next_l + 2, next_l + 8)}

A rejected plan is retried with the whole prompt plus a note; at most
three attempts in all (`dual_llm.call_planner`, `max_retries=2`). The note
always says "failed parsing", even for a schema violation, because the parse
handler catches `ValueError` first and `TacticSpecValidationError` is a
`ValueError` (`r0005_planner_2.md`, lines {retry_l}–{retry_l + 1}):

{_block(p5b, retry_l, retry_l + 1)}

### Coder prompt

The coder prompt (`legacy/m25/prompts/coder_implement_tactic.md`) carries the
planner's TacticSpec JSON, the opponent's full source, `src/types.h`,
`src/ai_abi.h`, the planner's message/memory guidance and fixed style rules.
It has no AAR, no journal and no copy of Team B's program, so every round's
candidate is written from scratch (`r0003_coder_1.md`, lines
{coder_noaar_l}, {coder_fixed_l} and {coder_kite_l}):

{_block(c3, coder_noaar_l, coder_noaar_l)}
>
{_block(c3, coder_fixed_l, coder_fixed_l)}
>
{_block(c3, coder_kite_l, coder_kite_l)}

## How the loop decides

### Rounds and seeds

- Rounds alternate by number: even rounds are Team A's, odd rounds Team B's.
  In fixed-opponent mode the frozen team's rounds are skipped, so with
  `--evolve-team B` and `--rounds N` Team B plays rounds 1, 3, 5, … < N. Its
  round 1 evaluates the start program without a model call; every later
  round makes one planner call (up to three on retries) and one coder call.
  M25's `--rounds 100` gave Team B 50 rounds: the round-1 evaluation and 49
  LLM rounds.
- Seeds: {_cite(loop, "seed_base=seed + round_num * 1000")}. Each evaluation
  plays seeds `seed_base … seed_base + 9`, in the scenario
  {first_eval["seed_base"]}–{first_eval["seed_base"] + first_eval["n_matches"] - 1}
  for round {first_eval["round"]}. New seeds every round; the champion and the
  candidate never play the same seeds.
- Sides are fixed: the evolving team always plays its own side (Team B as B).
  There is no side swap.
- The opponent is named `champion_a_r<round>` in the prompts, a new name every
  round although its code never changes.

### Scoring

- Per match, from Team A's side: {_cite(fit, "_SCORE_BY_OUTCOME = ")}.
  CRASH and TIMEOUT score 0, the same as a draw. Team B's fitness is minus
  the mean ({_cite(loop, "team_fitness = -fitness_result.mean")}).
- A program that fails guard injection or compilation is not scored: the
  round stalls. A lint violation stalls the round before any match.
- Stalled rounds leave no journal entry. The stall entry the loop writes has
  `status: "stalled"`, which `legacy/m25/docs/journal_schema.json` does not
  allow, so validation fails and the entry is dropped.
- Per-match results are not saved; the round directory keeps only the plan,
  the code and the opponent's copy (`decisions.json`, `round_dirs`).

### Acceptance

- {_cite(loop, "accepted = team_fitness > (current_champion_fitness - 0.05)")}
- The first evaluation of a team is always accepted
  ({_cite(loop, "if current_champion_fitness is None:")}).
- On acceptance the champion's fitness becomes the candidate's own 10-match
  score ({_cite(loop, "champion_b_fitness = team_fitness")}). It is never
  measured again.
- With 10 matches every score is a multiple of 0.1, so the 0.05 margin makes
  the rule "candidate ≥ champion": a tie is accepted.

### Journal lessons

- {_cite(loop, "recent = team_entries[-3:]")}: the evolving team's last three
  journal entries, accepted or rejected, one line each
  ({_cite(loop, 'lines.append(f"Gen {gen}: {verdict} (fitness={fitness:.3f}) - {hypothesis}")')}).
- "Last Generation" is the round number two back; "Last Hypothesis" and "Last
  Fitness" come from the last journal entry. After a stalled round they
  disagree (table below).

### The scenario's decisions

{_decision_table(decisions, journal)}

The planner's "Current Champion" block in each round:

{_champion_block_table(calls)}

## Defects D1–D5

**D1 No inheritance: confirmed.** No planner or coder prompt contains any
version of Team B's program (table above, "Own code"). The planner sees the
opponent's source and numbers about its own champion; the coder sees a plan,
the opponent's source and the headers. Each candidate is a fresh rewrite.

**D2 AAR never delivered: confirmed.** Every planner prompt's AAR section is
`(none - first generation)`. The loop looks for the previous round's
`trace_sample.jsonl` ({_cite(loop, 'prev_trace = prev_round_dir / "trace_sample.jsonl"')}),
and nothing in `legacy/m25/` writes that file (no round directory in
`decisions.json` has one). The prompt still asks the planner to "Analyze the
After-Action Report", requires 5–8 "metrics from the AAR" in its answer, and
promises that "the next AAR" will compare its predictions with results.

**D3 Stale acceptance bar: confirmed, in two forms.** The bar is the
champion's single 10-match score on its own round's seeds, set when it was
accepted and never re-measured. In alternating co-evolution it goes stale
when the opponent changes. In fixed-opponent mode, as for M25's Team B whose
opponent never changed, the opponent cannot change, but the bar is still one
noisy measurement on different seeds from the candidate's, and since it is
only ever set by a candidate that passed, it is biased upward.

**D4 Prompt-seeded tactics: confirmed.** The planner prompt names flanking,
bait and striker roles, formation control, kiting and spreading as options
and worked examples (EXPLORE excerpt above; "Tactic terms" column). The coder
prompt adds "kite at range" and "maintain formation" as examples. A tactic
named in these prompts cannot count as emergent in A1 under the brief's rule.

**D5 Scoring loophole: confirmed.** Crashes and timeouts score 0, a draw: in
round 9 the scripted candidate had 2 crashes and 2 timeouts and was accepted.
Build failures stall the round instead of counting as losses, and the stall
is dropped from the journal. Per-match results are not saved.

## Other behaviour A1 inherits

- Stalled rounds vanish from the lessons, and "Last Generation" then names
  the stalled round while "Last Hypothesis" describes an earlier one.
- The planner's retry note says "failed parsing" for every rejection; the
  validation-specific branch in {_cite(dual, "except ts_mod.TacticSpecValidationError as e:")}
  is unreachable.
- The round-1 journal entry fails strict-reflection validation and is written
  anyway with its validation flags set to false (`decisions.json`, `log`).
- The prompt's examples were written for M22, where the opponent was
  `pursuit_v1`; in A1 `pursuit_v1` is Team B's own start program.
- Container infrastructure failures end the run (execution change 2). The
  snapshot has no resume: a run that stops must restart from round 0.
"""


def _line_of_text(text: str, needle: str) -> int:
    hits = [i for i, ln in enumerate(text.splitlines(), 1) if needle in ln]
    if len(hits) != 1:
        raise SystemExit(f"expected one line containing {needle!r}, got {len(hits)}")
    return hits[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the report is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        if not OUT.is_file() or OUT.read_text() != text:
            print(f"{OUT.relative_to(REPO_ROOT)} is stale; re-run without --check", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
