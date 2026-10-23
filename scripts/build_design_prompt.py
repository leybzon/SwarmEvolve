#!/usr/bin/env python3
"""Build bots/generation/design_prompt.md, the prompt every bot author gets.

The prompt is the fixed text below plus, verbatim, ``bots/RULES.md``,
``src/types.h``, ``src/ai_abi.h``, ``bots/template.cpp`` and the two
baselines with their comments removed by ``scripts/strip_comments.py`` (the
baselines' comments describe their strategies; the authors get the code
only). The rules section restates SPECIFICATION.md §1-3 and §7 as the
engine implements them, without the specification's design commentary,
and gives the scoring the study uses (SPECIFICATION.md §6 is not used).

The file is frozen with its SHA-256 in docs/paper/RESEARCH_BRIEF.md; this
script exists so a test can show it was assembled from those sources.

    python scripts/build_design_prompt.py          # write the prompt
    python scripts/build_design_prompt.py --check  # fail if it differs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from strip_comments import strip_comments  # noqa: E402

OUT = REPO_ROOT / "bots" / "generation" / "design_prompt.md"

# (heading, path, fence language, strip comments)
EMBEDDED = (
    ("Bot rules (bots/RULES.md)", "bots/RULES.md", "markdown", False),
    ("src/types.h", "src/types.h", "cpp", False),
    ("src/ai_abi.h", "src/ai_abi.h", "cpp", False),
    ("Template (bots/template.cpp)", "bots/template.cpp", "cpp", False),
    ("Example program: pursuit_v1", "src/baselines/pursuit_v1.cpp", "cpp", True),
    ("Example program: cluster_v1", "src/baselines/cluster_v1.cpp", "cpp", True),
)

INTRO = """\
# Write a SwarmEvolve bot

SwarmEvolve is a simulation in which two teams of drones fight in a square
arena. A bot is a C++ program that controls every drone of one team. Your
task is to write the strongest bot you can.

Your bot will play many matches against a range of opponents that you will
not see, sometimes as Team A and sometimes as Team B. It is judged only by
the results of those matches. Everything you need is below: the game rules,
the rules every bot must follow, the two headers the bot is compiled
against, a template, and two example programs.

Reply with the complete source of your bot as one C++ file in a single
```cpp code block. Anything outside the code block is ignored.
"""

GAME_RULES = """\
## Game rules

**Teams and arena.** Two teams with the same number of drones, N per team
(`params->num_drones_a`; usually 10, at most `MAX_DRONES`). The arena is
`[0, params->arena_width] x [0, params->arena_height]`, usually 1000 x 1000.
The origin is the top-left corner, x grows to the right and y grows
downwards.

**Start.** Every drone of both teams starts at an independent, uniformly
random position in the arena, drawn from the match's seed. Cooldowns,
memories and messages start at zero.

**Ticks.** A match runs in ticks. Each tick has four phases.

1. *Query.* `drone_ai` is called once for every alive drone of both teams.
   All calls in a tick see the same snapshot of the game state; nothing
   changes until every drone has answered. Each call fills in an `Action`.
2. *Movement.* Each alive drone's `velocity` is scaled down to length
   `params->max_velocity` (usually 5) if it is longer, added to its
   position, and the position is clamped to the arena. Velocity does not
   carry over between ticks. Dead drones do not move.
3. *Combat.* An alive drone attacks successfully when its `cooldown` is 0,
   its `target_id` is the index of an alive enemy, and that enemy is within
   `params->disable_range` (usually 50) of it after movement. A successful
   attack marks the target to die and sets the attacker's cooldown to
   `params->max_cooldown` (usually 10). All attacks of both teams are
   resolved before anyone dies, so two drones can destroy each other, and
   every drone that successfully attacks the same target pays the cooldown
   although the target dies only once. An attack with `target_id == -1`, an
   invalid index, a dead target, a target out of range or a nonzero
   cooldown does nothing and costs nothing.
4. *Cleanup.* Marked drones die. Every alive drone's cooldown above 0 drops
   by 1. Each alive drone's `message_out` is delivered to all its teammates
   on the next tick as `incoming_messages[sender_id]`; a dead drone's slot
   is all zeros, and so is every slot on the first tick.
   `params->current_tick` increases by 1.

**What a drone sees.** `allies` holds its own team, including itself at
`allies[my_id]`: id, position, cooldown and alive flag. `enemies` holds the
other team's id, position and alive flag; enemy cooldowns are not visible.
Both arrays have `MAX_DRONES` entries, of which the first N are used.
`my_memory` is `MEM_SIZE` floats belonging to this drone alone: zero at the
start, kept from tick to tick, and frozen when the drone dies.

**End of a match.** A match ends when a team has no drones left, or when
`params->max_ticks` ticks have been played. A team with drones left wins
against a team with none; if both teams lose their last drones in the same
tick, it is a draw. At the tick limit, the team with more drones alive wins,
and equal numbers are a draw.

**Scoring.** A win scores +1, a draw 0 and a loss -1. A bot that breaks the
bot rules, fails to compile, crashes, or makes a match run longer than 10
seconds loses that match.

**Determinism.** The engine is deterministic: the same seed and the same
two programs always give the same match.
"""


def _source(path: str, strip: bool) -> str:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    return strip_comments(text) if strip else text


def build() -> str:
    parts = [INTRO, GAME_RULES]
    for heading, path, lang, strip in EMBEDDED:
        note = "with its comments removed" if strip else "verbatim"
        parts.append(f"## {heading}\n\n`{path}`, {note}.\n\n```{lang}\n{_source(path, strip)}```\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the prompt differs")
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(REPO_ROOT)} differs from its sources", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
