"""Regression tests (D1, D2, D3, D5) for the dual-LLM evolution loops
``scripts/evolve_coevolve.py`` and ``scripts/evolve_dual.py``.

Offline and LLM-code-free: LLM clients are ``MockClient`` doubles and
``fitness.evaluate_fitness`` is replaced by a scripted fake that scores
each pairing from a table keyed on a ``// TAG:<name>`` comment in each
program. The only real match is a pursuit-vs-cluster baseline run that
produces a schema-v2 trace for the AAR.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_coevolve
import evolve_dual
import fitness
import inject_guards
import llm_client
import telemetry_aar
from _build_helper import CXX
from llm_client import LLMResponse, MockClient
from test_dual_llm import PLANNER_JSON

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler found on PATH")

BASELINES = REPO_ROOT / "src" / "baselines"
_TAG = re.compile(r"// TAG:(\w+)")


def _program(tag: str, ns: str) -> str:
    return f"// TAG:{tag}\nnamespace {ns} {{}}\n"


def _coder_reply(tag: str, ns: str) -> LLMResponse:
    return LLMResponse(text=f"```cpp\n{_program(tag, ns)}```", model="mock-coder")


@pytest.fixture(scope="module")
def real_trace(tmp_path_factory) -> Path:
    """A genuine schema-v2 trace recorded through fitness.evaluate_fitness."""
    tmp = tmp_path_factory.mktemp("real_trace")
    trace = tmp / "trace.jsonl"
    fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=1,
        workers=1,
        scratch_root=tmp,
        record_trace_for_match_idx=0,
        trace_output_path=trace,
    )
    return trace


class FakeFitness:
    """Scripted stand-in for fitness.evaluate_fitness."""

    def __init__(self, table: dict[tuple[str, str], float], trace: Path):
        self.table = table
        self.trace = trace
        self.calls: list[dict] = []

    def __call__(
        self,
        team_a_src,
        team_b_src,
        *,
        n_matches,
        seed_base,
        under_test=None,
        record_trace_for_match_idx=None,
        trace_output_path=None,
    ):
        a_text = Path(team_a_src).read_text()
        b_text = Path(team_b_src).read_text()
        tags = (_TAG.search(a_text).group(1), _TAG.search(b_text).group(1))
        mean = self.table[tags]
        self.calls.append(
            dict(
                tags=tags,
                a_text=a_text,
                b_text=b_text,
                seed_base=seed_base,
                under_test=under_test,
                traced=trace_output_path is not None,
            )
        )
        if trace_output_path is not None:
            shutil.copy(self.trace, trace_output_path)
        per_match = [
            dict(
                seed=seed_base + i,
                outcome="DRAW",
                score=mean,
                ticks=1,
                wall_ms=0,
                a_alive=0,
                b_alive=0,
                return_code=2,
            )
            for i in range(n_matches)
        ]
        return fitness.FitnessResult(
            team_a_path=str(team_a_src),
            team_b_path=str(team_b_src),
            n_matches=n_matches,
            seed_base=seed_base,
            mean=mean,
            per_match=per_match,
            under_test=under_test,
        )


def _install_mocks(monkeypatch, fake, planner, coder):
    def client_factory(model):
        return planner if model == "mock-planner" else coder

    monkeypatch.setattr(llm_client, "AnthropicClient", client_factory)
    monkeypatch.setattr(fitness, "evaluate_fitness", fake)


# ---------------------------------------------------------------------------
# Co-evolution
# ---------------------------------------------------------------------------

# Team-A-perspective means. Round 2's candidate a1 loses to the champion;
# round 3's b1 is accepted, so in round 4 champion a0 faces a stronger B
# (-0.6 instead of the stale +0.5). a2 beats a0 against b1 and must be
# accepted; against the stale bar (+0.5 - 0.05) it would be rejected.
COEVOLVE_TABLE = {
    ("a0", "b0"): 0.5,
    ("a1", "b0"): 0.0,
    ("a0", "b1"): -0.6,
    ("a2", "b1"): -0.2,
}


@pytest.fixture
def coevolve_run(tmp_path, monkeypatch, real_trace):
    init_a = tmp_path / "init_a.cpp"
    init_b = tmp_path / "init_b.cpp"
    init_a.write_text(_program("a0", "TeamA"))
    init_b.write_text(_program("b0", "TeamB"))
    fake = FakeFitness(COEVOLVE_TABLE, real_trace)
    planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")] * 3)
    coder = MockClient(
        [_coder_reply("a1", "TeamA"), _coder_reply("b1", "TeamB"), _coder_reply("a2", "TeamA")]
    )
    _install_mocks(monkeypatch, fake, planner, coder)
    out = tmp_path / "run"
    rc = evolve_coevolve.evolve_coevolve(
        init_champion_a=init_a,
        init_champion_b=init_b,
        planner_model="mock-planner",
        coder_model="mock-coder",
        rounds=5,
        n_matches=3,
        seed=42,
        out_dir=out,
    )
    assert rc == 0
    return out, fake, planner, coder


def test_coevolve_prompts_carry_parent_code(coevolve_run):
    """D1: planner and coder see the evolving team's own champion."""
    _, _, planner, coder = coevolve_run
    # Rounds 2 (A), 3 (B), 4 (A; a1 was rejected so the parent is still a0).
    for call_idx, parent_tag in [(0, "a0"), (1, "b0"), (2, "a0")]:
        assert f"TAG:{parent_tag}" in planner.calls[call_idx]
        assert f"TAG:{parent_tag}" in coder.calls[call_idx]


def test_coevolve_planner_receives_aar(coevolve_run):
    """D2: the AAR is rendered from a trace the loop itself recorded."""
    out, fake, planner, _ = coevolve_run
    trace = out / "round_0002" / "trace_sample.jsonl"
    assert trace.is_file()
    expected = telemetry_aar.render_aar(trace, perspective="A", fmt="both").markdown
    assert "(none - first generation)" not in planner.calls[0]
    assert expected in planner.calls[0]
    round2 = [c for c in fake.calls if c["seed_base"] == 42 + 2 * 1000]
    assert [c["traced"] for c in round2] == [True, False]  # champion run is traced


def test_coevolve_remeasures_champion_against_current_opponent(coevolve_run):
    """D3: round 4 re-scores champion a0 against the new champion b1 and
    accepts against that fresh value, not the stale round-0 score."""
    out, fake, planner, _ = coevolve_run
    round4 = [c for c in fake.calls if c["seed_base"] == 42 + 4 * 1000]
    assert [c["tags"] for c in round4] == [("a0", "b1"), ("a2", "b1")]
    assert round4[0]["traced"] and not round4[1]["traced"]
    assert "**Champion Fitness:** -0.600" in planner.calls[2]
    entries = [json.loads(line) for line in (out / "journal.jsonl").read_text().splitlines()]
    assert {e["generation"]: e["verdict"] for e in entries} == {
        0: "confirmed",
        1: "confirmed",
        2: "rejected",
        3: "confirmed",
        4: "confirmed",
    }
    assert "TAG:a2" in (out / "champion_a.cpp").read_text()
    assert "TAG:b1" in (out / "champion_b.cpp").read_text()


def test_coevolve_scores_program_under_test_and_saves_per_match(coevolve_run):
    """D5: every evaluation names the evolving side as the program under
    test, and the full result with per-match rows is written to disk."""
    out, fake, _, _ = coevolve_run
    for c in fake.calls:
        evolving = "A" if (c["seed_base"] - 42) // 1000 % 2 == 0 else "B"
        assert c["under_test"] == evolving
    for name in ("fitness_champion.json", "fitness_candidate.json"):
        saved = json.loads((out / "round_0002" / name).read_text())
        assert saved["under_test"] == "A"
        assert len(saved["per_match"]) == 3
        assert saved["seed_base"] == 42 + 2 * 1000


def test_coevolve_runs_opponent_and_champion_with_guards(coevolve_run):
    out, fake, _, _ = coevolve_run
    for c in fake.calls:
        assert inject_guards.MARKER_COMMENT in c["a_text"]
        assert inject_guards.MARKER_COMMENT in c["b_text"]


# ---------------------------------------------------------------------------
# Fixed opponent (evolve_dual)
# ---------------------------------------------------------------------------

DUAL_TABLE = {
    ("opp", "c0"): 0.5,  # init champion loses as Team B
    ("opp", "c1"): -0.4,  # candidate wins as Team B
}


@pytest.fixture
def dual_run(tmp_path, monkeypatch, real_trace):
    opponent = tmp_path / "opp.cpp"
    init = tmp_path / "init.cpp"
    opponent.write_text(_program("opp", "TeamA"))
    init.write_text(_program("c0", "TeamB"))
    fake = FakeFitness(DUAL_TABLE, real_trace)
    planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")])
    coder = MockClient([_coder_reply("c1", "TeamB")])
    _install_mocks(monkeypatch, fake, planner, coder)
    out = tmp_path / "run"
    rc = evolve_dual.evolve_dual_llm(
        opponent_path=opponent,
        as_team="B",
        planner_model="mock-planner",
        coder_model="mock-coder",
        generations=2,
        n_matches=3,
        seed=7,
        out_dir=out,
        init_champion=init,
    )
    assert rc == 0
    return out, fake, planner, coder


def test_dual_prompts_carry_parent_and_aar(dual_run):
    """D1 + D2 for the fixed-opponent loop."""
    out, _, planner, coder = dual_run
    assert "TAG:c0" in planner.calls[0]
    assert "TAG:c0" in coder.calls[0]
    trace = out / "gen_0001" / "trace_sample.jsonl"
    expected = telemetry_aar.render_aar(trace, perspective="B", fmt="both").markdown
    assert expected in planner.calls[0]


def test_dual_pairs_seeds_and_scores_from_as_team_perspective(dual_run):
    """Champion and candidate share seeds; as_team=B fitness is flipped
    to B's perspective, so a B win (mean_a < 0) is accepted."""
    out, fake, planner, _ = dual_run
    gen1 = [c for c in fake.calls if c["seed_base"] == 7 + 1000]
    assert [c["tags"] for c in gen1] == [("opp", "c0"), ("opp", "c1")]
    assert all(c["under_test"] == "B" for c in fake.calls)
    assert "**Champion Fitness:** -0.500" in planner.calls[0]
    entries = [json.loads(line) for line in (out / "journal.jsonl").read_text().splitlines()]
    assert [(e["generation"], e["verdict"], e["fitness"]) for e in entries] == [
        (0, "rejected", -0.5),
        (1, "confirmed", 0.4),
    ]
    assert "TAG:c1" in (out / "champion.cpp").read_text()
    saved = json.loads((out / "gen_0001" / "fitness_candidate.json").read_text())
    assert len(saved["per_match"]) == 3
