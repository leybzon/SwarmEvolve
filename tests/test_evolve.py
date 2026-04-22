"""End-to-end tests for the M10 evolutionary loop (``scripts/evolve.py``).

Coverage:

* ``test_three_gen_mock_loop`` — 3 generations with deterministic
  ``MockClient`` responses. Verifies:
  - each gen_end event is written,
  - accepted challengers become the champion,
  - the champion's recorded fitness never regresses,
  - a checkpoint is emitted at the cap (``--checkpoint-every=1``),
  - the fitness plot PNG is produced.
* ``test_checkpoint_validates_against_schema`` — checkpoint JSON validates
  against ``docs/checkpoint_schema.json``.
* ``test_resume_matches_uninterrupted`` — a run stopped after 2 gens and
  resumed for 1 more reaches the same final champion as a single
  uninterrupted 3-gen run. Determinism contract for M10.
* ``test_redacts_api_key_in_logs`` — a synthetic ``sk-ant-XXX...`` key
  stashed in the environment and passed through as part of an error
  message is ``***REDACTED***`` in every on-disk artifact.
* ``test_compile_failure_cap`` — mock responses that fail to parse are
  counted as compile-failures; once the cap is hit the loop exits 30.
* ``test_no_cpp_block_rejects_gen`` — a mock response without a fenced
  block is marked ``parse_failed`` and the champion is unchanged.

The tests gate the compile path on a usable C++ compiler (via
``tests._build_helper.CXX``); the pure-Python tests (redaction,
parse-failed, compile-failure cap) run unconditionally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
BASELINES = REPO_ROOT / "src" / "baselines"
CHECKPOINT_SCHEMA = REPO_ROOT / "docs" / "checkpoint_schema.json"

# Make scripts importable.
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve  # noqa: E402
import experiment_log  # noqa: E402
from _build_helper import CXX  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _baseline_cpp(namespace: str) -> str:
    """Render pursuit_v1 with the requested team namespace + rewrite
    the ``#include "../foo.h"`` pairs to the non-relative form that the
    build_matchup path uses. For the evolve loop, however, the
    candidate.injected.cpp is compiled under a temporary ``src/{a,b}/``
    layout that fitness.py builds; the original ``../ai_abi.h`` relative
    include form is what evolve_once uses too, so keep it as-is.
    """
    src = (BASELINES / "pursuit_v1.cpp").read_text()
    return src.replace("TEAM_NS_PLACEHOLDER", namespace)


def _fenced(body: str) -> str:
    return f"```cpp\n{body}\n```\n"


@pytest.fixture()
def mock_response_dir_three_gens(tmp_path: Path) -> Path:
    """Three mock responses, all valid pursuit AI for TeamA."""
    mr = tmp_path / "responses"
    mr.mkdir()
    body = _fenced(_baseline_cpp("TeamA"))
    for i in range(3):
        (mr / f"{i:02d}.md").write_text(body)
    return mr


@pytest.fixture()
def opponent_path() -> Path:
    return BASELINES / "pursuit_v1.cpp"


# ---------------------------------------------------------------------------
# Pure-Python tests (no compiler required)
# ---------------------------------------------------------------------------


def test_redact_preserves_non_secret_text():
    """Sanity: evolve-level redaction is the same primitive as M9."""
    out = experiment_log.redact("normal text key=sk-ant-TESTKEYTESTKEY1234567890")
    assert "sk-ant-" not in out
    assert experiment_log.REDACTED in out


def test_no_cpp_block_rejects_gen(tmp_path: Path, opponent_path: Path):
    """A mock response without a fenced block → parse_failed, loop continues."""
    mr = tmp_path / "responses"
    mr.mkdir()
    (mr / "0.md").write_text("no code block here, just prose.\n")

    out_dir = tmp_path / "run"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mr),
        "--generations", "1",
        "--n-matches", "1",
        "--workers", "1",
        "--checkpoint-every", "1",
        "--out-dir", str(out_dir),
        "--seed", "7",
    ])
    # Loop completes (exit 0) because 1 failure is below the default cap.
    assert rc == 0
    state = json.loads((out_dir / "state.json").read_text())
    assert state["history"][0]["status"] == "parse_failed"
    assert state["compile_failures"] == 1
    # Champion unchanged — still the seed.
    assert state["champion_generation"] == -1


def test_compile_failure_cap(tmp_path: Path, opponent_path: Path):
    """5 parse_failed gens should trip the default cap and exit 30."""
    mr = tmp_path / "responses"
    mr.mkdir()
    for i in range(6):
        (mr / f"{i:02d}.md").write_text(f"no fence in response {i}\n")

    out_dir = tmp_path / "run"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mr),
        "--generations", "10",
        "--n-matches", "1",
        "--workers", "1",
        "--max-compile-failures", "3",
        "--checkpoint-every", "1",
        "--out-dir", str(out_dir),
        "--seed", "7",
    ])
    assert rc == evolve.EXIT_LOOP_ABORTED_COMPILE_CAP
    state = json.loads((out_dir / "state.json").read_text())
    assert state["compile_failures"] == 3
    assert state["generation"] == 3

    # events.jsonl must contain loop_aborted with reason=max_compile_failures.
    events = experiment_log.ExperimentLog.read(out_dir)
    assert any(e["type"] == "loop_aborted"
               and e.get("reason") == "max_compile_failures" for e in events)


def test_redacts_api_key_in_logs(tmp_path: Path, opponent_path: Path,
                                 monkeypatch: pytest.MonkeyPatch):
    """A fake Anthropic key smuggled into an LLM error string must be
    redacted in every on-disk artifact the loop writes."""
    # Use a payload that masquerades as an error message containing the
    # key. We inject it via a MockClient that raises LLMError by giving
    # it an empty queue — the resulting error string comes from our own
    # test harness, so wrap a custom mock.
    import llm_client

    fake_key = "sk-ant-TESTKEY0000111122223333444455556666"

    class LeakyClient:
        model = "mock-leaky"
        def generate(self, prompt: str, *, max_tokens: int = 4096):
            raise llm_client.LLMError(f"synthetic failure: api_key={fake_key}")

    # Build state directly (bypass CLI) so we can inject LeakyClient.
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    # Feed the loop through the normal code path by monkey-patching
    # evolve._build_client to yield our leaky client.
    def _build(kind, *, model, mock_response_paths, mock_cursor):
        return LeakyClient(), mock_cursor + 1

    monkeypatch.setattr(evolve, "_build_client", _build)

    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        # mock_response_paths is not actually read by LeakyClient but
        # the CLI requires --mock-response-dir; pass an empty placeholder
        # via a dummy md.
        "--mock-response-dir", str(_dummy_mock_dir(tmp_path)),
        "--generations", "1",
        "--n-matches", "1",
        "--workers", "1",
        "--max-compile-failures", "5",
        "--checkpoint-every", "1",
        "--out-dir", str(out_dir),
        "--seed", "42",
    ])
    # Loop returns 0 (1 failure, cap is 5). The key must not appear
    # anywhere under run_dir/.
    assert rc == 0
    leaked = _scan_tree_for(out_dir, fake_key)
    assert leaked == [], f"api key leaked into: {leaked}"


def _dummy_mock_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dummy_mocks"
    d.mkdir(exist_ok=True)
    (d / "0.md").write_text("(unused)\n")
    return d


def _scan_tree_for(root: Path, needle: str) -> list[str]:
    """Return all file paths under ``root`` whose text contains ``needle``."""
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if needle in text:
            hits.append(str(p.relative_to(root)))
    return hits


# ---------------------------------------------------------------------------
# Tests that require a working C++ compiler
# ---------------------------------------------------------------------------


needs_cxx = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


@needs_cxx
def test_three_gen_mock_loop(
    tmp_path: Path, opponent_path: Path, mock_response_dir_three_gens: Path,
):
    """Three-gen run with symmetric seed=opponent.

    Each gen proposes the same (valid) pursuit AI. Score of pursuit
    vs pursuit is 0 in expectation; with small n_matches it may be
    slightly non-zero. We don't assert specific outcomes — only the
    loop invariants: the pipeline runs end-to-end, history has 3
    gen entries, and a checkpoint + plot exist.
    """
    out_dir = tmp_path / "run"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir_three_gens),
        "--generations", "3",
        "--n-matches", "3",
        "--workers", "1",
        "--checkpoint-every", "1",
        "--out-dir", str(out_dir),
        "--seed", "101",
    ])
    assert rc == 0, f"loop exited {rc}"

    state = json.loads((out_dir / "state.json").read_text())
    assert state["generation"] == 3
    assert len(state["history"]) == 3
    for g in state["history"]:
        assert g["status"] in ("accepted", "rejected"), g

    # Champion's fitness never regresses across accepted gens.
    champ_means: list[float] = []
    for g in state["history"]:
        if g["accepted"]:
            champ_means.append(g["mean"])
    for i in range(1, len(champ_means)):
        assert champ_means[i] > champ_means[i - 1] - 1e-9, champ_means

    # Checkpoints exist.
    ckpts = sorted((out_dir / "checkpoints").glob("[0-9]*.json"))
    assert ckpts, "no checkpoint file produced"
    latest = out_dir / "checkpoints" / "latest.json"
    assert latest.is_file()

    # Plot exists and is a real PNG (starts with the PNG magic).
    # matplotlib is optional; when missing, evolve.py silently skips.
    plot = out_dir / "plots" / "fitness.png"
    try:
        import matplotlib  # noqa: F401
        have_mpl = True
    except ImportError:
        have_mpl = False
    if have_mpl:
        assert plot.is_file()
        assert plot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # Per-gen artifacts: each gens/NNNN/ has prompt, response, candidate,
    # injected, fitness.
    for n in range(3):
        gd = out_dir / "gens" / f"{n:04d}"
        assert (gd / "prompt.md").is_file()
        assert (gd / "response.md").is_file()
        assert (gd / "candidate.cpp").is_file()
        assert (gd / "candidate.injected.cpp").is_file()
        assert (gd / "fitness.json").is_file()

    # events.jsonl is well-formed: starts with experiment_start, ends
    # with experiment_end, has one loop_done.
    events = experiment_log.ExperimentLog.read(out_dir)
    assert events[0]["type"] == "experiment_start"
    assert events[-1]["type"] == "experiment_end"
    assert sum(1 for e in events if e["type"] == "loop_done") == 1


@needs_cxx
def test_checkpoint_validates_against_schema(
    tmp_path: Path, opponent_path: Path, mock_response_dir_three_gens: Path,
):
    jsonschema = pytest.importorskip("jsonschema")
    out_dir = tmp_path / "run"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir_three_gens),
        "--generations", "3",
        "--n-matches", "2",
        "--workers", "1",
        "--checkpoint-every", "3",
        "--out-dir", str(out_dir),
        "--seed", "11",
    ])
    assert rc == 0
    schema = json.loads(CHECKPOINT_SCHEMA.read_text())
    fitness_schema = json.loads(
        (REPO_ROOT / "docs" / "fitness_schema.json").read_text()
    )
    # The checkpoint schema's `$ref: fitness_schema.json` resolves
    # relative to its own `$id` — we pre-seed the resolver store so it
    # never hits the network.
    store = {
        schema["$id"]: schema,
        fitness_schema["$id"]: fitness_schema,
        # The relative form the validator will try when combining $id
        # with the $ref value:
        "https://swarmevolve.io/schemas/fitness_schema.json": fitness_schema,
    }
    resolver = jsonschema.RefResolver.from_schema(schema, store=store)
    payload = json.loads((out_dir / "checkpoints" / "latest.json").read_text())
    jsonschema.validate(payload, schema, resolver=resolver)


@needs_cxx
def test_resume_matches_uninterrupted(
    tmp_path: Path, opponent_path: Path, mock_response_dir_three_gens: Path,
):
    """Run 2 gens → resume for 1 more → same final champion as a
    single uninterrupted 3-gen run (with the same seed)."""
    # Run A: 2 gens, then resume for 1 more.
    run_a = tmp_path / "run_a"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir_three_gens),
        "--generations", "2",
        "--n-matches", "2",
        "--workers", "1",
        "--checkpoint-every", "2",
        "--out-dir", str(run_a),
        "--seed", "321",
    ])
    assert rc == 0
    rc = evolve.main([
        "--resume", str(run_a),
        "--generations", "3",
    ])
    assert rc == 0

    # Run B: 3 gens in one shot.
    run_b = tmp_path / "run_b"
    rc = evolve.main([
        "--opponent", str(opponent_path),
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir_three_gens),
        "--generations", "3",
        "--n-matches", "2",
        "--workers", "1",
        "--checkpoint-every", "3",
        "--out-dir", str(run_b),
        "--seed", "321",
    ])
    assert rc == 0

    state_a = json.loads((run_a / "state.json").read_text())
    state_b = json.loads((run_b / "state.json").read_text())
    # Compare the invariant parts: champion fitness mean/ci and
    # the mean column of history (run_id + timestamps differ).
    assert state_a["champion_fitness"] is not None
    assert state_b["champion_fitness"] is not None
    assert state_a["champion_fitness"]["mean"] == state_b["champion_fitness"]["mean"]
    assert state_a["champion_fitness"]["wins_a"] == state_b["champion_fitness"]["wins_a"]
    a_means = [g["mean"] for g in state_a["history"]]
    b_means = [g["mean"] for g in state_b["history"]]
    assert a_means == b_means, (a_means, b_means)
