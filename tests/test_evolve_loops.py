"""Tests for the dual-LLM evolution loops ``scripts/evolve_coevolve.py``
and ``scripts/evolve_dual.py``: the D1, D2, D3 and D5 regressions, and the
inherit and feedback switches with their per-round records.

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
import round_records
import telemetry_aar
from llm_client import LLMResponse, MockClient

from tests._build_helper import CXX
from tests.test_dual_llm import PLANNER_JSON

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler found on PATH")

BASELINES = REPO_ROOT / "src" / "baselines"
_TAG = re.compile(r"// TAG:(\w+)")


def _tag(source: str) -> str:
    m = _TAG.search(source)
    assert m is not None, source
    return m.group(1)


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
        tags = (_tag(a_text), _tag(b_text))
        mean = self.table[tags]
        self.calls.append(
            dict(
                tags=tags,
                a_text=a_text,
                b_text=b_text,
                seed_base=seed_base,
                n_matches=n_matches,
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
    """D2: the AAR is rendered from a match the loop itself recorded: one
    traced match of the champion on the round's fixed AAR seed."""
    out, fake, planner, _ = coevolve_run
    trace = out / "round_0002" / "aar_trace.jsonl"
    assert trace.is_file()
    expected = telemetry_aar.render_aar(trace, perspective="A", fmt="both").markdown
    assert "(none - first generation)" not in planner.calls[0]
    assert expected in planner.calls[0]
    aar_calls = [c for c in fake.calls if c["traced"]]
    assert [c["seed_base"] for c in aar_calls] == [round_records.aar_seed(42, r) for r in range(5)]
    assert all(c["n_matches"] == 1 for c in aar_calls)
    assert [c["tags"] for c in aar_calls][2] == ("a0", "b0")  # the champion, not the candidate
    round2 = [c for c in fake.calls if c["seed_base"] == 42 + 2 * 1000]
    assert [c["traced"] for c in round2] == [False, False]  # evaluations are not traced


def test_coevolve_remeasures_champion_against_current_opponent(coevolve_run):
    """D3: round 4 re-scores champion a0 against the new champion b1 and
    accepts against that fresh value, not the stale round-0 score."""
    out, fake, planner, _ = coevolve_run
    round4 = [c for c in fake.calls if c["seed_base"] == 42 + 4 * 1000]
    assert [c["tags"] for c in round4] == [("a0", "b1"), ("a2", "b1")]
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
        offset = c["seed_base"] - 42
        round_num = offset - round_records.AAR_SEED_OFFSET if c["traced"] else offset // 1000
        assert c["under_test"] == ("A" if round_num % 2 == 0 else "B")
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
    trace = out / "gen_0001" / "aar_trace.jsonl"
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


# ---------------------------------------------------------------------------
# Switches (inherit, feedback) and per-round records: a 6-round co-evolution
# ---------------------------------------------------------------------------

# Team-A-perspective means. Round 2 accepts a1, round 3 accepts b1, round 4
# rejects a2, round 5 accepts b2, so the champion changes between rounds.
SIX_ROUND_TABLE = {
    ("a0", "b0"): 0.5,
    ("a1", "b0"): 0.7,
    ("a1", "b1"): 0.2,
    ("a2", "b1"): 0.1,
    ("a1", "b2"): -0.5,
}
# Champion each LLM round starts from: (round, team, tag)
SIX_ROUND_PARENTS = [(2, "A", "a0"), (3, "B", "b0"), (4, "A", "a1"), (5, "B", "b1")]

ROUND_FILES = {
    "summary.json", "aar.md", "aar.json", "aar_trace.jsonl", "fitness_champion.json",
    "candidate.cpp", "tactic_spec.json", "planner_response.md", "coder_response.md",
}  # fmt: skip
LLM_ROUND_FILES = ROUND_FILES | {
    "planner_prompt.md", "coder_prompt.md", "candidate.injected.cpp", "fitness_candidate.json",
}  # fmt: skip


def _run_six_rounds(tmp_path, monkeypatch, trace, *, inherit, feedback):
    init_a = tmp_path / "init_a.cpp"
    init_b = tmp_path / "init_b.cpp"
    init_a.write_text(_program("a0", "TeamA"))
    init_b.write_text(_program("b0", "TeamB"))
    fake = FakeFitness(SIX_ROUND_TABLE, trace)
    planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")] * 4)
    coder = MockClient(
        [
            _coder_reply("a1", "TeamA"),
            _coder_reply("b1", "TeamB"),
            _coder_reply("a2", "TeamA"),
            _coder_reply("b2", "TeamB"),
        ]
    )
    _install_mocks(monkeypatch, fake, planner, coder)
    out = tmp_path / "run"
    rc = evolve_coevolve.evolve_coevolve(
        init_champion_a=init_a,
        init_champion_b=init_b,
        planner_model="mock-planner",
        coder_model="mock-coder",
        rounds=6,
        n_matches=3,
        seed=42,
        out_dir=out,
        inherit=inherit,
        feedback=feedback,
    )
    assert rc == 0
    return out


def _source_of(out: Path, tag: str) -> str:
    """The exact source the loop holds for program ``tag``: an init file,
    or the code extracted from the round that produced it."""
    for path in [*out.glob("round_*/candidate.cpp"), *out.parent.glob("init_*.cpp")]:
        text = path.read_text()
        if f"TAG:{tag}\n" in text:
            return text
    raise AssertionError(tag)


@pytest.fixture
def six_rounds_full(tmp_path, monkeypatch, real_trace):
    return _run_six_rounds(tmp_path, monkeypatch, real_trace, inherit="parent", feedback="aar")


@pytest.fixture
def six_rounds_off(tmp_path, monkeypatch, real_trace):
    return _run_six_rounds(tmp_path, monkeypatch, real_trace, inherit="none", feedback="none")


def test_inherit_parent_puts_the_champion_in_both_prompts_byte_for_byte(six_rounds_full):
    out = six_rounds_full
    for round_num, _team, tag in SIX_ROUND_PARENTS:
        rd = out / f"round_{round_num:04d}"
        parent = _source_of(out, tag)
        for name in ("planner_prompt.md", "coder_prompt.md"):
            assert f"```cpp\n{parent}\n```" in (rd / name).read_text(), (round_num, name)


def test_feedback_aar_gives_every_planner_a_nonempty_aar_matching_aar_json(six_rounds_full):
    out = six_rounds_full
    for round_num, team, _tag in SIX_ROUND_PARENTS:
        rd = out / f"round_{round_num:04d}"
        prompt = (rd / "planner_prompt.md").read_text()
        aar = prompt.split("# After-Action Report (Current Champion)\n\n", 1)[1]
        aar = aar.split("\n\n# Lessons from Prior Generations", 1)[0]
        metrics = json.loads((rd / "aar.json").read_text())
        assert aar.strip() and metrics["perspective"] == team
        assert aar == telemetry_aar.render_markdown(metrics) == (rd / "aar.md").read_text()
        for key in ("ticks", "shots_fired_us", "shots_fired_them"):
            assert str(metrics[key]) in aar
        assert "# Lessons from Prior Generations\n\nGen " in prompt


def test_both_switches_off_send_neither_parent_nor_aar(six_rounds_off):
    out = six_rounds_off
    for round_num, _team, tag in SIX_ROUND_PARENTS:
        rd = out / f"round_{round_num:04d}"
        parent = _source_of(out, tag)
        aar = (rd / "aar.md").read_text()  # recorded anyway, for grounding
        for name in ("planner_prompt.md", "coder_prompt.md"):
            text = (rd / name).read_text()
            assert f"TAG:{tag}\n" not in text and parent not in text
            assert aar not in text and "After-Action Report (Current Champion)" not in text
            assert "Lessons from Prior Generations" not in text and "Parent Program" not in text


def test_six_round_coevolution_writes_every_per_round_file(six_rounds_full):
    out = six_rounds_full
    rounds = sorted(p.name for p in out.glob("round_*"))
    assert rounds == [f"round_{r:04d}" for r in range(6)]
    for r in range(6):
        rd = out / f"round_{r:04d}"
        llm = r >= 2
        expected = LLM_ROUND_FILES if llm else ROUND_FILES
        files = {p.name for p in rd.iterdir()}
        assert expected <= files, (r, expected - files)
        summary = json.loads((rd / "summary.json").read_text())
        assert summary["status"] == "ok" and summary["llm_round"] is llm
        assert summary["aar_seed"] == round_records.aar_seed(42, r)
        assert set(summary["files"]) == files - {"summary.json"}
        if llm:
            sim = summary["similarity"]
            assert 0.0 <= sim["line_ratio"] <= 1.0 and 0.0 <= sim["token_ratio"] <= 1.0
            g = summary["grounding"]
            assert g["cited"] == g["matched"] + g["unmatched"] > 0
        else:
            assert summary["similarity"] is None and summary["grounding"] is None
    verdicts = [json.loads((out / f"round_{r:04d}" / "summary.json").read_text())["accepted"]
                for r in range(2, 6)]  # fmt: skip
    assert verdicts == [True, True, False, True]


def test_similarity_compares_candidate_with_its_parent(six_rounds_full):
    out = six_rounds_full
    summary = json.loads((out / "round_0004" / "summary.json").read_text())
    parent, child = _source_of(out, "a1"), _source_of(out, "a2")
    assert summary["similarity"] == round_records.similarity(parent, child)


def test_dual_loop_switches(tmp_path, monkeypatch, real_trace):
    """The fixed-opponent loop honours the same switches."""
    opponent = tmp_path / "opp.cpp"
    init = tmp_path / "init.cpp"
    opponent.write_text(_program("opp", "TeamA"))
    init.write_text(_program("c0", "TeamB"))
    for inherit, feedback in [("parent", "aar"), ("none", "none")]:
        planner = MockClient([LLMResponse(text=PLANNER_JSON, model="mock-planner")])
        coder = MockClient([_coder_reply("c1", "TeamB")])
        _install_mocks(monkeypatch, FakeFitness(DUAL_TABLE, real_trace), planner, coder)
        out = tmp_path / f"run_{inherit}_{feedback}"
        assert evolve_dual.evolve_dual_llm(
            opponent_path=opponent, as_team="B", planner_model="mock-planner",
            coder_model="mock-coder", generations=2, n_matches=3, seed=7, out_dir=out,
            init_champion=init, inherit=inherit, feedback=feedback,
        ) == 0  # fmt: skip
        gd = out / "gen_0001"
        assert (gd / "planner_prompt.md").read_text() == planner.calls[0]
        assert (gd / "coder_prompt.md").read_text() == coder.calls[0]
        aar = (gd / "aar.md").read_text()
        on = inherit == "parent"
        assert (f"```cpp\n{init.read_text()}\n```" in planner.calls[0]) is on
        assert (f"```cpp\n{init.read_text()}\n```" in coder.calls[0]) is on
        assert (aar in planner.calls[0]) is (feedback == "aar")
        summary = json.loads((gd / "summary.json").read_text())
        assert summary["inherit"] == inherit and summary["feedback"] == feedback
        assert summary["grounding"]["cited"] > 0 and summary["similarity"] is not None


def test_aar_failure_stalls_an_aar_round_and_is_logged(tmp_path, monkeypatch, real_trace):
    """With feedback=aar a round never runs without its AAR."""
    out = tmp_path / "run"

    def broken(*_a, **_k):
        raise RuntimeError("container down")

    monkeypatch.setattr(round_records, "record_aar", broken)
    _install_mocks(
        monkeypatch, FakeFitness(SIX_ROUND_TABLE, real_trace), MockClient(), MockClient()
    )
    init_a, init_b = tmp_path / "a.cpp", tmp_path / "b.cpp"
    init_a.write_text(_program("a0", "TeamA"))
    init_b.write_text(_program("b0", "TeamB"))
    evolve_coevolve.evolve_coevolve(
        init_champion_a=init_a, init_champion_b=init_b, planner_model="mock-planner",
        coder_model="mock-coder", rounds=3, n_matches=3, seed=42, out_dir=out,
    )  # fmt: skip
    for r in range(3):
        summary = json.loads((out / f"round_{r:04d}" / "summary.json").read_text())
        assert summary["status"] == "stalled" and "aar failed" in summary["stall_reason"]
        assert not (out / f"round_{r:04d}" / "planner_prompt.md").exists()
