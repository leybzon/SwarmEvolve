"""End-to-end tests for ``scripts/orchestrator.py evaluate`` + ``replay`` (M9).

Coverage:

* **evaluate happy path** — produces ``fitness.json`` (validates against
  ``docs/fitness_schema.json``) and ``events.jsonl`` (experiment_start
  first, experiment_end last, one match_result per match,
  fitness_summary recorded).
* **evaluate invalid input** — missing team source → exit 2, no files
  written outside out_dir.
* **evaluate compile failure** — broken AI source → exit 3, compile_failed
  event in log, no fitness.json.
* **replay parity** — re-running ``replay <out_dir>`` on a completed run
  exits 0 (byte-identical summary).
* **replay on missing dir** — exits 2.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._build_helper import CXX

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = REPO_ROOT / "scripts" / "orchestrator.py"
BASELINES = REPO_ROOT / "src" / "baselines"
FITNESS_SCHEMA = REPO_ROOT / "docs" / "fitness_schema.json"

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")

jsonschema = pytest.importorskip("jsonschema")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    import os as _os
    env = dict(_os.environ)
    env["CXX"] = CXX
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR), *args],
        capture_output=True, text=True, check=False, env=env,
    )


def _read_events(run_dir: Path) -> list[dict]:
    lines = (run_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_fitness_schema_is_well_formed():
    schema = json.loads(FITNESS_SCHEMA.read_text())
    jsonschema.Draft7Validator.check_schema(schema)


# ---------------------------------------------------------------------------
# evaluate — happy path
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evaluate_run(tmp_path_factory):
    """One real evaluate invocation shared across tests.

    Module-scoped to avoid paying the compile+run tax more than once.
    """
    out = tmp_path_factory.mktemp("evaluate_ok")
    result = _run([
        "evaluate",
        "--team-a", str(BASELINES / "pursuit_v1.cpp"),
        "--team-b", str(BASELINES / "cluster_v1.cpp"),
        "--n-matches", "3",
        "--seed-base", "0",
        "--workers", "1",
        "--out-dir", str(out),
    ])
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return out


def test_evaluate_writes_fitness_json(evaluate_run):
    fj = evaluate_run / "fitness.json"
    assert fj.is_file(), "fitness.json not written"
    payload = json.loads(fj.read_text())
    jsonschema.validate(payload, json.loads(FITNESS_SCHEMA.read_text()))
    assert payload["n_matches"] == 3
    assert payload["wins_a"] + payload["wins_b"] + payload["draws"] + payload["invalid"] == 3
    assert len(payload["per_match"]) == 3
    # Seeds must cover [seed_base, seed_base + n_matches).
    assert sorted(m["seed"] for m in payload["per_match"]) == [0, 1, 2]


def test_evaluate_writes_events_log(evaluate_run):
    events = _read_events(evaluate_run)
    assert len(events) >= 5  # start + 3 matches + summary + end

    # seq is monotonic from 0.
    assert [e["seq"] for e in events] == list(range(len(events)))

    assert events[0]["type"] == "experiment_start"
    assert events[0]["experiment_type"] == "fitness"
    assert events[-1]["type"] == "experiment_end"

    types = [e["type"] for e in events]
    assert types.count("match_result") == 3
    assert types.count("fitness_summary") == 1

    # environment snapshot has what we promised.
    env = events[0]["environment"]
    for key in ("python", "platform", "cpu_count", "team_a", "team_b"):
        assert key in env, key
    assert len(env["team_a"]["sha256"]) == 64


def test_evaluate_summary_matches_fitness_json(evaluate_run):
    """fitness_summary event and fitness.json must agree — they're the
    same data taking two paths out of the evaluator."""
    fj = json.loads((evaluate_run / "fitness.json").read_text())
    events = _read_events(evaluate_run)
    summary = next(e for e in events if e["type"] == "fitness_summary")
    for key in ("wins_a", "wins_b", "draws", "invalid", "mean", "stdev",
                "ci_low", "ci_high"):
        assert summary[key] == fj[key], key


# ---------------------------------------------------------------------------
# evaluate — error paths
# ---------------------------------------------------------------------------


def test_evaluate_missing_team_a_exits_2(tmp_path):
    out = tmp_path / "missing_a"
    result = _run([
        "evaluate",
        "--team-a", str(tmp_path / "nope.cpp"),
        "--team-b", str(BASELINES / "cluster_v1.cpp"),
        "--n-matches", "1",
        "--out-dir", str(out),
    ])
    assert result.returncode == 2
    assert "missing-team-a-source" in result.stderr
    # No fitness.json when input validation fails before any work.
    assert not (out / "fitness.json").exists()


def test_evaluate_missing_team_b_exits_2(tmp_path):
    out = tmp_path / "missing_b"
    result = _run([
        "evaluate",
        "--team-a", str(BASELINES / "pursuit_v1.cpp"),
        "--team-b", str(tmp_path / "nope.cpp"),
        "--n-matches", "1",
        "--out-dir", str(out),
    ])
    assert result.returncode == 2
    assert "missing-team-b-source" in result.stderr


def test_evaluate_compile_failure_exits_3(tmp_path):
    broken = tmp_path / "broken.cpp"
    broken.write_text(
        "// Intentionally malformed.\n"
        "namespace TEAM_NS_PLACEHOLDER {\n"
        "  this is not valid C++;\n"
        "}\n"
    )
    out = tmp_path / "compile_fail"
    result = _run([
        "evaluate",
        "--team-a", str(broken),
        "--team-b", str(BASELINES / "cluster_v1.cpp"),
        "--n-matches", "1",
        "--workers", "1",
        "--out-dir", str(out),
    ])
    assert result.returncode == 3
    # No fitness.json because we failed before aggregating.
    assert not (out / "fitness.json").exists()
    # But events.jsonl exists and records the compile_failed event.
    events = _read_events(out)
    assert any(e["type"] == "compile_failed" for e in events)
    assert events[-1]["type"] == "experiment_end"


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def test_replay_parity(evaluate_run):
    """Replaying a completed run must return exit 0 — bit-identical summary."""
    # -v lifts the logger above WARNING so we can assert on replay-ok.
    result = _run(["-v", "replay", str(evaluate_run)])
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "replay-ok" in result.stderr


def test_replay_missing_dir_exits_2(tmp_path):
    result = _run(["replay", str(tmp_path / "does_not_exist")])
    assert result.returncode == 2
    assert "missing-run-dir" in result.stderr


def test_replay_empty_dir_exits_2(tmp_path):
    # Directory exists but no events.jsonl.
    target = tmp_path / "empty"
    target.mkdir()
    result = _run(["replay", str(target)])
    assert result.returncode == 2
    assert "bad-events-log" in result.stderr
