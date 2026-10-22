"""Tests that pin the frozen M25 loop (legacy/m25) for arm A1.

* Snapshot integrity: the files copied unchanged still match their 7e539a4
  blobs, and every file under legacy/ matches legacy/m25/SHA256SUMS.
* Golden characterisation: ``tests/legacy_m25_scenario.py`` runs 6 LLM
  rounds with recording clients and scripted match outcomes; every prompt
  sent and every decision taken must equal the golden files byte for byte.
* The four execution changes: fixed-opponent mode, container execution
  with M25's scoring, injected clients, resume from the last completed round
  (an interrupted and resumed scenario must end exactly as the golden one).
* ``docker`` tests (skip without a runtime or the eval image): one
  evaluation and two loop rounds really run in the eval container.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from legacy.m25 import client as legacy_client
from legacy.m25 import dual_llm, evolve_coevolve, journal
from legacy.m25 import fitness as legacy_fitness
from tests import legacy_m25_scenario as scenario

LEGACY = REPO_ROOT / "legacy"
SNAPSHOT = LEGACY / "m25"
MANIFEST = SNAPSHOT / "SHA256SUMS"

# git blob ids at 7e539a4 of the files the snapshot keeps unchanged.
UNCHANGED_SINCE_7E539A4 = {
    "tactic_spec.py": "434abe59f72212ba906d01b3ee60a8afac29afa1",
    "telemetry_aar.py": "326b8ef25b1d33d4e8af4438e7b7d311307a6532",
    "lint_ai_tokens.py": "c4683a8db20e4362fcc22b6241987d4913c70dcf",
    "inject_guards.py": "d4d21e1616504a63d94500bc0949f36ade26cddd",
    "prompts/planner_analyze_aar_v2.md": "85a8a70d6a16ad22bdf5ba313baf02eacfeec480",
    "prompts/coder_implement_tactic.md": "62baae0764fac37d25c1100fdd062243e8881daa",
    "src/types.h": "c4a4c9f3a8548c50451cb51ef5c2e249cd14a49b",
    "src/ai_abi.h": "479844771e91afbdc62ec6fbf3bdc0022364a1a7",
    "docs/journal_schema.json": "0035c4b846b8af7c0622d17827ba69a3b3fd6c13",
}


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _snapshot_files() -> list[Path]:
    return sorted(
        p
        for p in LEGACY.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p != MANIFEST
    )


# ---------------------------------------------------------------------------
# Snapshot integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", sorted(UNCHANGED_SINCE_7E539A4))
def test_unedited_files_match_7e539a4(rel: str) -> None:
    assert _git_blob_id((SNAPSHOT / rel).read_bytes()) == UNCHANGED_SINCE_7E539A4[rel]


def test_every_snapshot_file_matches_manifest() -> None:
    listed = {}
    for line in MANIFEST.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    actual = {
        str(p.relative_to(LEGACY)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in _snapshot_files()
    }
    assert sorted(actual) == sorted(listed), "files added to or removed from legacy/"
    changed = [rel for rel in actual if actual[rel] != listed[rel]]
    assert not changed, f"legacy/ is frozen; these files changed: {changed}"


def test_imports_resolve_to_the_snapshot() -> None:
    for mod in (dual_llm, evolve_coevolve, journal, legacy_fitness, legacy_client):
        assert Path(str(mod.__file__)).parent == SNAPSHOT, mod.__name__
    loop_globals = vars(evolve_coevolve)
    assert loop_globals["tspec"].__name__ == "legacy.m25.tactic_spec"
    assert loop_globals["inject_guards"].__name__ == "legacy.m25.inject_guards"
    assert dual_llm.PROMPTS == SNAPSHOT / "prompts"
    assert dual_llm.TYPES_HEADER == SNAPSHOT / "src" / "types.h"
    assert dual_llm.ABI_HEADER == SNAPSHOT / "src" / "ai_abi.h"
    assert journal.SCHEMA_PATH == SNAPSHOT / "docs" / "journal_schema.json"
    assert str(SNAPSHOT) not in sys.path


def test_prompt_headers_still_match_the_engine() -> None:
    """The coder prompt pastes the snapshot's headers; the container compiles
    against src/. If the engine's ABI ever changes, A1's prompt would describe
    the wrong one, so this fails first."""
    for name in ("types.h", "ai_abi.h"):
        assert (SNAPSHOT / "src" / name).read_bytes() == (REPO_ROOT / "src" / name).read_bytes()


# ---------------------------------------------------------------------------
# Golden characterisation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scenario_files() -> dict[str, str]:
    return scenario.scenario_files()


def test_golden_files_cover_the_scenario(scenario_files: dict[str, str]) -> None:
    golden = sorted(str(p.relative_to(scenario.GOLDEN)) for p in scenario.GOLDEN.rglob("*.*"))
    assert sorted(scenario_files) == golden


@pytest.mark.parametrize(
    "rel",
    sorted(str(p.relative_to(scenario.GOLDEN)) for p in scenario.GOLDEN.rglob("*.*")),
)
def test_matches_golden(scenario_files: dict[str, str], rel: str) -> None:
    assert scenario_files.get(rel) == (scenario.GOLDEN / rel).read_text(), (
        f"legacy/m25 now sends or decides something different: {rel} "
        "differs from tests/fixtures/legacy_m25/golden"
    )


def _golden_json(name: str) -> Any:
    return json.loads((scenario.GOLDEN / name).read_text())


def test_golden_run_is_fixed_opponent_with_m25_schedule() -> None:
    decisions = _golden_json("decisions.json")
    assert decisions["exit_code"] == 0
    evals = decisions["evaluations"]
    assert [e["round"] for e in evals] == [1, 3, 5, 9, 11, 13]
    opponent = (scenario.INPUTS / "opponent_a.cpp").read_text()
    for e in evals:
        assert e["seed_base"] == scenario.SEED + e["round"] * 1000
        assert (e["n_matches"], e["max_ticks"], e["timeout"]) == (10, 1000, 10.0)
        assert (e["team_a_file"], e["team_b_file"]) == ("opponent_A.cpp", "candidate.injected.cpp")
        assert e["team_a_sha256"] == hashlib.sha256(opponent.encode()).hexdigest()
    assert sorted(decisions["round_dirs"]) == [f"round_{r:04d}" for r in (1, 3, 5, 7, 9, 11, 13)]
    assert decisions["champion_files"] == ["champion_b.cpp"]


def test_golden_decisions() -> None:
    entries = [
        json.loads(line) for line in (scenario.GOLDEN / "journal.jsonl").read_text().splitlines()
    ]
    # Stalled rounds (7: lint, 11: compile) never reach the journal: their
    # entries fail the schema (status "stalled") and are dropped.
    assert [(e["generation"], e["verdict"], e["fitness"]) for e in entries] == [
        (1, "confirmed", -0.7),
        (3, "confirmed", -0.2),
        (5, "rejected", -0.3),
        (9, "confirmed", 0.0),  # 2 crashes + 2 timeouts scored as draws
        (13, "confirmed", 0.0),  # a tie passes "candidate > champion - 0.05"
    ]
    assert all(e["track"] == "B" for e in entries)
    final = (scenario.GOLDEN / "champion_b.cpp").read_text()
    assert final == scenario.program("1.3").rstrip("\n")  # the fence regex drops it


def test_golden_prompts_carry_no_aar_and_no_own_code() -> None:
    calls = _golden_json("calls.json")
    assert [(c["round"], c["role"], c["attempt"]) for c in calls] == [
        (3, "planner", 1),
        (3, "coder", 1),
        (5, "planner", 1),
        (5, "planner", 2),
        (5, "coder", 1),
        (7, "planner", 1),
        (7, "coder", 1),
        (9, "planner", 1),
        (9, "coder", 1),
        (11, "planner", 1),
        (11, "coder", 1),
        (13, "planner", 1),
        (13, "coder", 1),
    ]
    assert {c["max_tokens"] for c in calls} == {4096}
    start = (scenario.INPUTS / "start_b.cpp").read_text()
    opponent = (scenario.INPUTS / "opponent_a.cpp").read_text()
    for c in calls:
        text = (scenario.GOLDEN / c["file"]).read_text()
        assert opponent in text
        assert start not in text
        for k in ("1.5f", "2.0f", "1.2f", "1.3f"):
            assert scenario.program(k) not in text
        if c["role"] == "planner":
            assert "# After-Action Report (Last Generation)\n\n(none - first generation)\n" in text


# ---------------------------------------------------------------------------
# Execution change 2: container execution, M25 scoring
# ---------------------------------------------------------------------------


def _rows(outcomes: list[str], seed_base: int = 0) -> list[dict[str, Any]]:
    return [
        {"seed": seed_base + i, "outcome": o, "score": -1.0, "ticks": 1, "return_code": 0}
        for i, o in enumerate(outcomes)
    ]


def _write(dir_: Path, name: str) -> Path:
    p = dir_ / name
    p.write_text("// program\n")
    return p


def test_crash_and_timeout_score_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = ["A_WIN", "B_WIN", "DRAW", "CRASH", "TIMEOUT", "A_WIN"]

    def fake(*_args: Any, **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        return _rows(outcomes, kw["seed_base"]), {"label": "dev"}, 2, "g++"

    monkeypatch.setattr(legacy_fitness, "_play_in_container", fake)
    res = legacy_fitness.evaluate_fitness(
        _write(tmp_path, "a.cpp"), _write(tmp_path, "b.cpp"), n_matches=6, seed_base=7
    )
    assert [r["score"] for r in res.per_match] == [1.0, -1.0, 0.0, 0.0, 0.0, 1.0]
    assert (res.wins_a, res.wins_b, res.draws, res.invalid) == (2, 1, 1, 2)
    assert res.mean == pytest.approx(1 / 6)
    assert res.backend == "sandbox" and res.platform == {"label": "dev"}


def test_unknown_outcome_is_an_infrastructure_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(*_args: Any, **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        return _rows(["A_WIN", "GUARD_FAIL"]), {}, 1, "g++"

    monkeypatch.setattr(legacy_fitness, "_play_in_container", fake)
    with pytest.raises(legacy_fitness.InfraError):
        legacy_fitness.evaluate_fitness(
            _write(tmp_path, "a.cpp"), _write(tmp_path, "b.cpp"), n_matches=2
        )


class _FakeCanonical:
    class CompileError(RuntimeError):
        pass

    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.kwargs: dict[str, Any] = {}

    def evaluate_fitness(self, a: Path, b: Path, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return type(
            "R",
            (),
            {"per_match": _rows(["DRAW"] * 10), "platform": {}, "workers": 3, "compiler": "g++"},
        )()


def test_container_call_uses_m25_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCanonical()
    monkeypatch.setattr(legacy_fitness, "_canonical_fitness", lambda: fake)
    legacy_fitness.evaluate_fitness(
        _write(tmp_path, "a.cpp"), _write(tmp_path, "b.cpp"), n_matches=10, seed_base=3042
    )
    assert fake.kwargs["backend"] == "sandbox"
    assert fake.kwargs["under_test"] is None
    assert (fake.kwargs["n_matches"], fake.kwargs["seed_base"]) == (10, 3042)
    assert (fake.kwargs["max_ticks"], fake.kwargs["timeout"]) == (1000, 10.0)


def test_build_failure_is_a_compile_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCanonical()
    fake.exc = fake.CompileError("compile fail: side=B rc=1")
    monkeypatch.setattr(legacy_fitness, "_canonical_fitness", lambda: fake)
    with pytest.raises(legacy_fitness.CompileError, match="side=B"):
        legacy_fitness.evaluate_fitness(
            _write(tmp_path, "a.cpp"), _write(tmp_path, "b.cpp"), n_matches=10
        )


def test_container_failure_is_an_infrastructure_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCanonical(RuntimeError("batch container exited rc=125"))
    monkeypatch.setattr(legacy_fitness, "_canonical_fitness", lambda: fake)
    with pytest.raises(legacy_fitness.InfraError, match="rc=125"):
        legacy_fitness.evaluate_fitness(
            _write(tmp_path, "a.cpp"), _write(tmp_path, "b.cpp"), n_matches=10
        )


def test_infrastructure_error_ends_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kw: Any) -> Any:
        raise legacy_fitness.InfraError("container produced no result")

    monkeypatch.setattr(legacy_fitness, "_play_in_container", broken)
    out = tmp_path / "run"
    with pytest.raises(legacy_fitness.InfraError):
        evolve_coevolve.evolve_coevolve(
            init_champion_a=scenario.OPPONENT_A,
            init_champion_b=scenario.START_B,
            planner_client=legacy_client.RecordingClient([], "p"),
            coder_client=legacy_client.RecordingClient([], "c"),
            rounds=2,
            n_matches=10,
            seed=42,
            out_dir=out,
            evolve_team="B",
        )
    assert not (out / "journal.jsonl").exists()


# ---------------------------------------------------------------------------
# Execution changes 1 and 3: fixed-opponent mode, injected clients
# ---------------------------------------------------------------------------


def test_alternating_mode_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, int]] = []

    def fake(a: Path, b: Path, **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        seen.append((a.name, b.name, kw["seed_base"]))
        return _rows(["DRAW"] * kw["n_matches"], kw["seed_base"]), {}, 1, "g++"

    monkeypatch.setattr(legacy_fitness, "_play_in_container", fake)
    planner = legacy_client.RecordingClient([scenario.PLANNER_TEXTS[0]], "p")
    coder = legacy_client.RecordingClient([scenario.CODER_TEXTS[0]], "c")
    rc = evolve_coevolve.evolve_coevolve(
        init_champion_a=scenario.OPPONENT_A,
        init_champion_b=scenario.START_B,
        planner_client=planner,
        coder_client=coder,
        rounds=3,
        n_matches=10,
        seed=42,
        out_dir=tmp_path / "run",
    )
    assert rc == 0
    assert seen == [
        ("candidate.injected.cpp", "opponent_B.cpp", 42),
        ("opponent_A.cpp", "candidate.injected.cpp", 1042),
        ("candidate.injected.cpp", "opponent_B.cpp", 2042),
    ]
    assert "You are Team **A**." in planner.calls[0].prompt
    tracks = [
        json.loads(line)["track"]
        for line in (tmp_path / "run" / "journal.jsonl").read_text().splitlines()
    ]
    assert tracks == ["A", "B", "A"]


def test_fixed_opponent_mode_rejects_unknown_team(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="evolve_team"):
        evolve_coevolve.evolve_coevolve(
            init_champion_a=scenario.OPPONENT_A,
            init_champion_b=scenario.START_B,
            planner_client=legacy_client.RecordingClient([], "p"),
            coder_client=legacy_client.RecordingClient([], "c"),
            rounds=2,
            n_matches=10,
            seed=42,
            out_dir=tmp_path / "run",
            evolve_team="C",
        )


def test_cli_needs_models_and_a_client_factory(tmp_path: Path) -> None:
    base = [
        "--init-champion-a",
        str(scenario.OPPONENT_A),
        "--init-champion-b",
        str(scenario.START_B),
        "--out-dir",
        str(tmp_path / "run"),
    ]
    with pytest.raises(SystemExit) as exc:
        evolve_coevolve.main([*base, "--planner-model", "p", "--coder-model", "c"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        evolve_coevolve.main([*base, "--client-factory", "x:y"])
    assert exc.value.code == 2


_FACTORY_CALLS: list[tuple[str, str]] = []


def recording_factory(role: str, model: str) -> legacy_client.RecordingClient:
    """Client factory used by the CLI test below."""
    _FACTORY_CALLS.append((role, model))
    return legacy_client.RecordingClient([], model)


def test_cli_builds_clients_through_the_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(*_args: Any, **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        return _rows(["DRAW"] * kw["n_matches"], kw["seed_base"]), {}, 1, "g++"

    monkeypatch.setattr(legacy_fitness, "_play_in_container", fake)
    _FACTORY_CALLS.clear()
    rc = evolve_coevolve.main(
        [
            "--init-champion-a",
            str(scenario.OPPONENT_A),
            "--init-champion-b",
            str(scenario.START_B),
            "--out-dir",
            str(tmp_path / "run"),
            "--planner-model",
            "planner-x",
            "--coder-model",
            "coder-y",
            "--client-factory",
            "tests.test_legacy_m25:recording_factory",
            "--evolve-team",
            "B",
            "--rounds",
            "2",
        ]
    )
    assert rc == 0
    assert _FACTORY_CALLS == [("planner", "planner-x"), ("coder", "coder-y")]
    entry = json.loads((tmp_path / "run" / "journal.jsonl").read_text())
    assert entry["model"] == "planner-x+coder-y"


# ---------------------------------------------------------------------------
# docker: the container really runs the matches
# ---------------------------------------------------------------------------


def _sandbox_or_skip() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import sandbox

    try:
        rt = sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")
    if not sandbox.image_present(sandbox.DEFAULT_IMAGE, runtime=rt):
        pytest.skip(f"eval image '{sandbox.DEFAULT_IMAGE}' not found; run `make docker-build`")


@pytest.fixture()
def home_tmp() -> Iterator[Path]:
    """Scratch under $HOME so the container runtime can bind-mount it."""
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="legacy_m25_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.docker
def test_container_evaluation_scores_like_m25(home_tmp: Path) -> None:
    _sandbox_or_skip()
    res = legacy_fitness.evaluate_fitness(
        scenario.START_B, scenario.OPPONENT_A, n_matches=6, seed_base=1042, scratch_root=home_tmp
    )
    canonical = legacy_fitness._canonical_fitness().evaluate_fitness(
        scenario.START_B,
        scenario.OPPONENT_A,
        n_matches=6,
        seed_base=1042,
        backend="sandbox",
        scratch_root=home_tmp,
        max_ticks=1000,
    )
    assert [r["outcome"] for r in res.per_match] == [r["outcome"] for r in canonical.per_match]
    table = {"A_WIN": 1.0, "B_WIN": -1.0, "DRAW": 0.0, "CRASH": 0.0, "TIMEOUT": 0.0}
    assert [r["score"] for r in res.per_match] == [table[r["outcome"]] for r in res.per_match]
    assert res.backend == "sandbox"
    assert res.platform["image_id"].startswith("sha256:") and res.platform["label"]


@pytest.mark.docker
@pytest.mark.slow
def test_loop_plays_its_rounds_in_the_container(home_tmp: Path) -> None:
    """Round 1 (init) and round 3 (scripted plan and code) in the container."""
    _sandbox_or_skip()
    out = home_tmp / "run"
    rc = evolve_coevolve.evolve_coevolve(
        init_champion_a=scenario.OPPONENT_A,
        init_champion_b=scenario.START_B,
        planner_client=legacy_client.RecordingClient([scenario.PLANNER_TEXTS[0]], "p"),
        coder_client=legacy_client.RecordingClient([scenario.CODER_TEXTS[0]], "c"),
        rounds=4,
        n_matches=10,
        seed=42,
        out_dir=out,
        evolve_team="B",
    )
    assert rc == 0
    entries = [json.loads(line) for line in (out / "journal.jsonl").read_text().splitlines()]
    assert [e["generation"] for e in entries] == [1, 3]  # round 3 compiled and played
    assert all(isinstance(e["fitness"], float) for e in entries)


# ---------------------------------------------------------------------------
# Execution change 4: resume from the last completed round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interruption", scenario.INTERRUPTIONS)
def test_interrupted_run_resumes_to_the_golden_result(interruption: str, tmp_path: Path) -> None:
    rec, out_dir = scenario.run_interrupted_scenario(tmp_path, interruption)
    files = scenario.render_golden(rec, out_dir)
    for rel, text in files.items():
        golden = (scenario.GOLDEN / rel).read_text()
        if rel == "decisions.json":
            # Same decisions; the log gains the resume lines.
            got, want = json.loads(text), json.loads(golden)
            got.pop("log")
            want.pop("log")
            assert got == want
        else:
            assert text == golden, rel
    events = [json.loads(x) for x in (out_dir / "state.jsonl").read_text().splitlines()]
    resumes = [e for e in events if e["event"] == "resume"]
    assert len(resumes) == 1
    at = int(interruption.split(":")[1])
    if interruption.startswith("checkpoint"):
        # Round 9 finished its journal entry; it is taken from the journal.
        assert resumes[0]["reconciled_round"] == 9 and resumes[0]["interrupted_copy"] is None
    else:
        assert resumes[0]["interrupted_copy"] == f"interrupted/round_{at:04d}_1"
        assert (out_dir / resumes[0]["interrupted_copy"]).is_dir()
    if interruption == "evaluation:9":
        # The plan and code survived, so no model call is repeated.
        assert any("resume-reuse round=9" in line for line in rec.log)
        assert (out_dir / "interrupted" / "round_0009_1" / "candidate.cpp").is_file()
    if interruption.startswith("coder"):
        # The unsaved plan is asked for again.
        assert [p["role"] for p in rec.lost_prompts] == ["planner"]


def _fresh_run(out_dir: Path, *, rounds: int = 2, resume: bool = False, n_matches: int = 10) -> int:
    return evolve_coevolve.evolve_coevolve(
        init_champion_a=scenario.OPPONENT_A,
        init_champion_b=scenario.START_B,
        planner_client=legacy_client.RecordingClient([], "p"),
        coder_client=legacy_client.RecordingClient([], "c"),
        rounds=rounds,
        n_matches=n_matches,
        seed=42,
        out_dir=out_dir,
        evolve_team="B",
        resume=resume,
    )


@pytest.fixture()
def draws(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []

    def fake(*_args: Any, **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], int, str]:
        seen.append(kw["seed_base"])
        return _rows(["DRAW"] * kw["n_matches"], kw["seed_base"]), {}, 1, "g++"

    monkeypatch.setattr(legacy_fitness, "_play_in_container", fake)
    return seen


def test_existing_run_is_not_restarted_without_resume(tmp_path: Path, draws: list[int]) -> None:
    assert _fresh_run(tmp_path / "run") == 0
    with pytest.raises(FileExistsError):
        _fresh_run(tmp_path / "run")


def test_finished_run_resumes_to_nothing(tmp_path: Path, draws: list[int]) -> None:
    assert _fresh_run(tmp_path / "run") == 0
    assert _fresh_run(tmp_path / "run", resume=True) == 0
    assert draws == [1042]


def test_resume_refuses_changed_settings(tmp_path: Path, draws: list[int]) -> None:
    assert _fresh_run(tmp_path / "run") == 0
    with pytest.raises(ValueError, match="n_matches"):
        _fresh_run(tmp_path / "run", resume=True, n_matches=20)


def test_resume_needs_a_checkpoint(tmp_path: Path, draws: list[int]) -> None:
    with pytest.raises(FileNotFoundError):
        _fresh_run(tmp_path / "run", resume=True)


def test_resume_ignores_a_torn_last_line(tmp_path: Path, draws: list[int]) -> None:
    assert _fresh_run(tmp_path / "run") == 0
    with (tmp_path / "run" / "state.jsonl").open("a") as fh:
        fh.write('{"event": "checkp')
    assert _fresh_run(tmp_path / "run", resume=True) == 0
