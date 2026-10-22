"""Characterisation scenario for the frozen M25 loop (legacy/m25).

Runs ``legacy.m25.evolve_coevolve`` in fixed-opponent mode as arm A1 will
(Team B evolves, Team A frozen) for 6 LLM rounds, with recording mock
clients and scripted match outcomes in place of the container. Everything
the loop sends to a model and everything it decides is written out as
golden files under ``tests/fixtures/legacy_m25/golden/``;
``tests/test_legacy_m25.py`` re-runs the scenario and compares byte for
byte.

The rounds are chosen to touch every branch that shapes a later prompt or
a decision:

====== ======================================================== ===========
round  what happens                                             decision
====== ======================================================== ===========
1      init champion evaluated, no LLM call                     accept
3      valid plan, clean code                                   accept
5      plan fails validation once, retry, then code             reject
7      code uses std::vector                                    lint stall
9      2 crashes and 2 timeouts among the 10 matches            accept
11     container compile failure                                stall
13     candidate ties the champion                              accept
====== ======================================================== ===========

Regenerate the golden files (only if the scenario itself changes; the
snapshot never does) with::

    python -m tests.legacy_m25_scenario --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from legacy.m25 import client as legacy_client
from legacy.m25 import evolve_coevolve
from legacy.m25 import fitness as legacy_fitness
from legacy.m25 import journal as legacy_journal

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "legacy_m25"
INPUTS = FIXTURES / "inputs"
GOLDEN = FIXTURES / "golden"

# Stand-ins for A1's real inputs. M22 gen 33 (A1's frozen opponent) is not
# on this host; cluster_v1 takes its place. pursuit_v1 is A1's real start.
OPPONENT_A = INPUTS / "opponent_a.cpp"
START_B = INPUTS / "start_b.cpp"

SEED = 42
N_MATCHES = 10
ROUNDS = 14  # Team B's rounds 1, 3, ..., 13: one init evaluation + 6 LLM rounds
PLANNER_MODEL = "mock-planner"
CODER_MODEL = "mock-coder"

# ---------------------------------------------------------------------------
# Scripted model responses
# ---------------------------------------------------------------------------

_KEY_METRICS = [
    "Outcome: LOSS (1 win, 7 losses, 2 draws / 10 matches)",
    "Cooldown utilization: ours=0.40, theirs=0.70",
    "Focus-fire redundancy: 0.50",
    "Mean pairwise distance: ours=30.0, theirs=80.0",
    "Message bus entropy: 0.0",
]


def _plan(name: str, mechanism: str, why: str, metrics: tuple[str, str]) -> str:
    spec = {
        "observe": {"key_metrics": _KEY_METRICS},
        "orient": {
            "why_we_failed": "Our drones chase the nearest enemy one at a time and arrive "
            "piecemeal, so the opponent trades evenly and then wins on numbers.",
            "what_enemy_exploited": "The opponent groups before engaging, so our first "
            "arrivals meet several shooters at once.",
            "constraints_violated": "We ignore message_out and my_memory entirely.",
        },
        "decide": {
            "tactic_name": name,
            "mechanism": mechanism,
            "why_this_counters_failure": why,
        },
        "act": {
            "expected_changes": [
                {
                    "metric": metrics[0],
                    "old_value": 0.4,
                    "target_value": 0.6,
                    "reason": "fewer wasted engagements",
                },
                {
                    "metric": metrics[1],
                    "old_value": 0.5,
                    "target_value": 0.3,
                    "reason": "less duplicated targeting",
                },
            ]
        },
        "implementation_guidance": {
            "message_protocol": "message_out[0] = claimed enemy id, others 0",
            "memory_layout": "my_memory[0] = last target id",
            "special_cases": "no alive enemy: hold position",
        },
    }
    return "```json\n" + json.dumps(spec, indent=2) + "\n```\n"


_MECH = (
    "Each drone picks the nearest alive enemy, closes at full speed, fires when the "
    "target is inside disable_range and its cooldown is zero, and backs away while "
    "cooling down if the target is closer than {k} times disable_range."
)

PLANNER_TEXTS = [
    # round 3
    _plan(
        "New approach: Cooldown Standoff",
        _MECH.format(k="1.5"),
        "Cooldown utilization 0.40 shows drones idling in range; backing off while "
        "cooling down avoids free kills.",
        ("cooldown_utilization_us", "focus_fire_redundancy"),
    ),
    # round 5, attempt 1: invalid metric name -> validation retry
    _plan(
        "Refined: Cooldown Standoff - wider standoff",
        _MECH.format(k="2.0"),
        "Focus-fire redundancy 0.50 stays high; a wider standoff spreads engagements.",
        ("kill_ratio", "focus_fire_redundancy"),
    ),
    # round 5, attempt 2
    _plan(
        "Refined: Cooldown Standoff - wider standoff",
        _MECH.format(k="2.0"),
        "Focus-fire redundancy 0.50 stays high; a wider standoff spreads engagements.",
        ("cooldown_utilization_us", "focus_fire_redundancy"),
    ),
    # round 7
    _plan(
        "New approach: Claim List",
        _MECH.format(k="1.5") + " Claims are kept in a list of enemy ids.",
        "Focus-fire redundancy 0.50: claims stop two drones chasing one enemy.",
        ("focus_fire_redundancy", "cooldown_utilization_us"),
    ),
    # round 9
    _plan(
        "Refined: Cooldown Standoff - shorter standoff",
        _MECH.format(k="1.2"),
        "Cooldown utilization 0.40: a shorter standoff re-engages sooner.",
        ("cooldown_utilization_us", "focus_fire_redundancy"),
    ),
    # round 11
    _plan(
        "Refined: Cooldown Standoff - memory of last target",
        _MECH.format(k="1.2") + " Drones keep their last target while it lives.",
        "Focus-fire redundancy 0.50: sticky targets cut switching.",
        ("focus_fire_redundancy", "cooldown_utilization_us"),
    ),
    # round 13
    _plan(
        "Refined: Cooldown Standoff - tuned standoff",
        _MECH.format(k="1.3"),
        "Cooldown utilization 0.40: tuning the standoff distance.",
        ("cooldown_utilization_us", "focus_fire_redundancy"),
    ),
]

_PROGRAM = """#include "../ai_abi.h"
#include "../types.h"
#include <cmath>

namespace TeamB {

namespace {
inline float dist(float x1, float y1, float x2, float y2) {
    const float dx = x2 - x1;
    const float dy = y2 - y1;
    return sqrtf(dx * dx + dy * dy);
}
}  // namespace

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
@@EXTRA@@    const AllyState& me = allies[my_id];
    int best = -1;
    float best_d = 1e30f;
    for (int i = 0; i < params->num_drones_a; ++i) {
        if (!enemies[i].alive) {
            continue;
        }
        const float d = dist(me.pos.x, me.pos.y, enemies[i].pos.x, enemies[i].pos.y);
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    out_action->velocity = {0.0f, 0.0f};
    out_action->target_id = -1;
    for (int k = 0; k < MSG_SIZE; ++k) {
        out_action->message_out[k] = 0.0f;
    }
    if (best < 0) {
        return;
    }
    float vx = enemies[best].pos.x - me.pos.x;
    float vy = enemies[best].pos.y - me.pos.y;
    if (me.cooldown > 0 && best_d < @@K@@f * params->disable_range) {
        vx = -vx;
        vy = -vy;
    }
    const float mag = sqrtf(vx * vx + vy * vy);
    if (mag > 1e-6f) {
        vx *= params->max_velocity / mag;
        vy *= params->max_velocity / mag;
    }
    out_action->velocity = {vx, vy};
    if (best_d <= params->disable_range && me.cooldown == 0) {
        out_action->target_id = best;
    }
    out_action->message_out[0] = static_cast<float>(best);
    my_memory[0] = static_cast<float>(best);
}

}  // namespace TeamB
"""


def program(k: str, extra: str = "") -> str:
    """The scripted coder's program: pursuit with a cooldown standoff of ``k``."""
    return _PROGRAM.replace("@@K@@", k).replace("@@EXTRA@@", extra)


def _code(k: str, extra: str = "") -> str:
    return "```cpp\n" + program(k, extra) + "```\n"


CODER_TEXTS = [
    _code("1.5"),  # round 3
    _code("2.0"),  # round 5
    _code("1.5", "    std::vector<int> claims;\n"),  # round 7: lint violation
    _code("1.2"),  # round 9
    _code("1.2"),  # round 11
    _code("1.3"),  # round 13
]

# ---------------------------------------------------------------------------
# Scripted match outcomes (what the container would report), per evaluation
# ---------------------------------------------------------------------------

A, B, D, CRASH, TIMEOUT = "A_WIN", "B_WIN", "DRAW", "CRASH", "TIMEOUT"
COMPILE_FAIL = "compile"

OUTCOMES: dict[int, list[str] | str] = {
    1: [A, A, A, A, B, A, D, A, A, A],  # B fitness -0.7
    3: [A, B, A, D, B, A, A, B, D, A],  # -0.2
    5: [A, A, B, A, D, A, B, A, B, A],  # -0.3 < -0.2 - 0.05
    9: [A, B, CRASH, A, TIMEOUT, B, A, CRASH, B, TIMEOUT],  # 0.0
    11: COMPILE_FAIL,
    13: [A, B, D, B, A, B, A, D, A, B],  # 0.0, ties the champion
}

COMPILE_LOG = (
    "compile fail: side=B rc=1\n--- compile.log\n== B rc=1\n"
    "/work/src/b/ai.cpp:30:5: error: 'target_memory' was not declared in this scope\n"
)

# ---------------------------------------------------------------------------
# Running the scenario
# ---------------------------------------------------------------------------


@dataclass
class Recorded:
    """Everything the loop sent or decided in one scenario run."""

    prompts: list[dict[str, Any]] = field(default_factory=list)  # role, round, attempt, ...
    lost_prompts: list[dict[str, Any]] = field(default_factory=list)  # calls of a failed round
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    exit_code: int | None = None


class _RoundAwareClient(legacy_client.RecordingClient):
    """RecordingClient that notes which round each call belongs to."""

    def __init__(self, texts: list[str], model: str, role: str, out_dir: Path, rec: Recorded):
        super().__init__(texts, model)
        self._role = role
        self._out_dir = out_dir
        self._rec = rec
        self.fail_in_round: int | None = None  # raise Interrupted once, in this round

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> legacy_client.LLMResponse:
        rounds = sorted(p.name for p in self._out_dir.glob("round_*"))
        round_num = int(rounds[-1].split("_")[1])
        if round_num == self.fail_in_round:
            self.fail_in_round = None
            raise Interrupted(f"{self._role} call failed in round {round_num}")
        attempt = 1 + sum(
            1 for p in self._rec.prompts if p["round"] == round_num and p["role"] == self._role
        )
        self._rec.prompts.append(
            {
                "role": self._role,
                "round": round_num,
                "attempt": attempt,
                "max_tokens": max_tokens,
                "model": self.model,
                "text": prompt,
            }
        )
        response = super().generate(prompt, max_tokens=max_tokens)
        self.last_text = response.text
        return response


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Interrupted(Exception):
    """A simulated failure that stops the run (for the resume tests)."""


class _ScriptedContainer:
    """Stands in for ``legacy_fitness._play_in_container``."""

    def __init__(self, rec: Recorded, fail_in_round: int | None = None):
        self._rec = rec
        self._fail_in_round = fail_in_round

    def __call__(
        self,
        team_a: Path,
        team_b: Path,
        *,
        n_matches: int,
        seed_base: int,
        workers: int | None,
        max_ticks: int,
        timeout: float,
        scratch_root: Path | str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        round_num = int(team_a.parent.name.split("_")[1])
        if round_num == self._fail_in_round:
            self._fail_in_round = None
            raise legacy_fitness.InfraError(f"scripted container failure in round {round_num}")
        script = OUTCOMES[round_num]
        self._rec.evaluations.append(
            {
                "round": round_num,
                "team_a_file": team_a.name,
                "team_b_file": team_b.name,
                "team_a_sha256": _sha(team_a.read_text()),
                "team_b_sha256": _sha(team_b.read_text()),
                "n_matches": n_matches,
                "seed_base": seed_base,
                "max_ticks": max_ticks,
                "timeout": timeout,
                "workers": workers,
                "outcomes": script,
            }
        )
        if script == COMPILE_FAIL:
            raise legacy_fitness.CompileError(COMPILE_LOG)
        assert isinstance(script, list) and len(script) == n_matches
        rows = [
            {
                "seed": seed_base + i,
                "outcome": outcome,
                "score": 99.0,  # ignored: the snapshot scores outcomes itself
                "ticks": 500,
                "wall_ms": 7,
                "a_alive": 3,
                "b_alive": 2,
                "return_code": {A: 0, B: 1, D: 2}.get(outcome, -1),
            }
            for i, outcome in enumerate(script)
        ]
        return rows, {"label": "scripted"}, 1, "scripted"


class _ListHandler(logging.Handler):
    def __init__(self, sink: list[str], work: Path):
        super().__init__(level=logging.INFO)
        self._sink = sink
        self._work = str(work)

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage().replace(self._work, "<WORK>")
        self._sink.append(f"{record.levelname} {record.name}: {msg}")


class _Run:
    """One scenario run directory, its recorder and its clients."""

    def __init__(self, work: Path):
        self.work = work
        self.rec = Recorded()
        self.out_dir = work / "run"
        self.out_dir.mkdir(parents=True)
        self.inputs = work / "inputs"
        shutil.copytree(INPUTS, self.inputs)
        self.planner = _RoundAwareClient(
            PLANNER_TEXTS, PLANNER_MODEL, "planner", self.out_dir, self.rec
        )
        self.coder = _RoundAwareClient(CODER_TEXTS, CODER_MODEL, "coder", self.out_dir, self.rec)

    def invoke(self, container: _ScriptedContainer, *, resume: bool = False) -> int:
        logger = logging.getLogger("swarmevolve")
        handler = _ListHandler(self.rec.log, self.work)
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        original = legacy_fitness._play_in_container
        legacy_fitness._play_in_container = container  # type: ignore[assignment]
        try:
            return evolve_coevolve.evolve_coevolve(
                init_champion_a=self.inputs / OPPONENT_A.name,
                init_champion_b=self.inputs / START_B.name,
                planner_client=self.planner,
                coder_client=self.coder,
                rounds=ROUNDS,
                n_matches=N_MATCHES,
                seed=SEED,
                out_dir=self.out_dir,
                strict_reflection=True,  # as M25 ran (--strict-reflection)
                acceptance_mode="relative",
                evolve_team="B",
                resume=resume,
            )
        finally:
            legacy_fitness._play_in_container = original  # type: ignore[assignment]
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def check_all_responses_used(self) -> None:
        assert not self.planner._queue and not self.coder._queue, "responses left unused"


def run_scenario(work: Path) -> tuple[Recorded, Path]:
    """Run the scenario in ``work``; return the record and the run's out_dir."""
    run = _Run(work)
    run.rec.exit_code = run.invoke(_ScriptedContainer(run.rec))
    run.check_all_responses_used()
    return run.rec, run.out_dir


# Where run_interrupted_scenario stops the first run.
INTERRUPTIONS = (
    "evaluation:9",  # container failure after round 9's plan and code exist
    "planner:11",  # model call fails at the start of round 11
    "coder:3",  # model call fails after round 3's plan, before its code
    "checkpoint:10",  # stop after round 9's journal entry, before the next checkpoint
)


def run_interrupted_scenario(work: Path, interruption: str) -> tuple[Recorded, Path]:
    """Run the scenario, stop it at ``interruption``, then resume it to the end."""
    where, _, num = interruption.partition(":")
    at = int(num)
    run = _Run(work)
    fail_eval = at if where == "evaluation" else None
    if where == "planner":
        run.planner.fail_in_round = at
    if where == "coder":
        run.coder.fail_in_round = at

    write_line = legacy_journal._write_line

    def stop_at_checkpoint(path: Path, entry: dict[str, Any]) -> None:
        if path.name == evolve_coevolve.STATE_FILE and entry.get("next_round") == at:
            raise Interrupted(f"stopped before checkpoint {at}")
        write_line(path, entry)

    if where == "checkpoint":
        legacy_journal._write_line = stop_at_checkpoint
    try:
        run.invoke(_ScriptedContainer(run.rec, fail_in_round=fail_eval))
        raise AssertionError(f"interruption {interruption} never happened")
    except (Interrupted, legacy_fitness.InfraError):
        if where == "coder":
            # The plan of a round whose coder call failed is never saved, so
            # resume asks the planner again. The mock answers as before; the
            # first attempt's calls are kept apart from the golden comparison.
            run.rec.lost_prompts = [p for p in run.rec.prompts if p["round"] == at]
            run.rec.prompts = [p for p in run.rec.prompts if p["round"] != at]
            run.planner._queue.insert(0, run.planner.last_text)
    finally:
        legacy_journal._write_line = write_line
    run.rec.exit_code = run.invoke(_ScriptedContainer(run.rec), resume=True)
    run.check_all_responses_used()
    return run.rec, run.out_dir


# ---------------------------------------------------------------------------
# Golden files
# ---------------------------------------------------------------------------


def prompt_filename(p: dict[str, Any]) -> str:
    return f"r{p['round']:04d}_{p['role']}_{p['attempt']}.md"


def _journal(out_dir: Path) -> str:
    lines = []
    for line in (out_dir / "journal.jsonl").read_text().splitlines():
        entry = json.loads(line)
        entry.pop("timestamp_utc")
        lines.append(json.dumps(entry, sort_keys=True))
    return "\n".join(lines) + "\n"


def render_golden(rec: Recorded, out_dir: Path) -> dict[str, str]:
    """Golden files as {relative path: text}."""
    files: dict[str, str] = {}
    calls = []
    for p in rec.prompts:
        name = prompt_filename(p)
        files[f"prompts/{name}"] = p["text"]
        calls.append(
            {
                "file": f"prompts/{name}",
                "role": p["role"],
                "round": p["round"],
                "attempt": p["attempt"],
                "model": p["model"],
                "max_tokens": p["max_tokens"],
                "sha256": _sha(p["text"]),
            }
        )
    files["calls.json"] = json.dumps(calls, indent=2) + "\n"
    decisions = {
        "exit_code": rec.exit_code,
        "evaluations": rec.evaluations,
        "log": rec.log,
        "round_dirs": {
            d.name: sorted(f.name for f in d.iterdir()) for d in sorted(out_dir.glob("round_*"))
        },
        "champion_files": sorted(p.name for p in out_dir.glob("champion_*.cpp")),
    }
    files["decisions.json"] = json.dumps(decisions, indent=2) + "\n"
    files["journal.jsonl"] = _journal(out_dir)
    files["champion_b.cpp"] = (out_dir / "champion_b.cpp").read_text()
    return files


def scenario_files() -> dict[str, str]:
    """Run the scenario in a scratch directory and return its golden files."""
    with tempfile.TemporaryDirectory(prefix="legacy_m25_") as tmp:
        rec, out_dir = run_scenario(Path(tmp))
        return render_golden(rec, out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="(re)write the golden files")
    args = parser.parse_args(argv)
    files = scenario_files()
    if not args.write:
        for name in sorted(files):
            print(name)
        return 0
    if GOLDEN.exists():
        shutil.rmtree(GOLDEN)
    for name, text in files.items():
        dest = GOLDEN / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    print(f"wrote {len(files)} files to {GOLDEN.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
