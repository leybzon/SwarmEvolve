"""End-to-end tests for ``scripts/orchestrator.py run``.

Three layers of coverage:

* **Happy path** — compile both frozen baselines and run one match; assert
  ``results.json`` is written, validates against
  ``docs/results_schema.json``, has ``status == "ok"``, a reasonable
  ``outcome``, and (when ``--record`` is on) a real trace file.
* **Deterministic outcome** — the same pairing at ``seed=42`` must produce
  the golden outcome (``DRAW`` at tick 116). This catches wiring
  regressions that still "run" but silently call the wrong engine.
* **Error handling** — missing AI file, compilation failure, engine
  timeout: each surfaces the documented exit code + structured
  ``results.json`` (where applicable) without crashing the orchestrator.
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
SCHEMA_PATH = REPO_ROOT / "docs" / "results_schema.json"

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


jsonschema = pytest.importorskip("jsonschema")


def _run(args: list[str], env_cxx: str | None = None) -> subprocess.CompletedProcess:
    import os as _os

    env = dict(_os.environ)
    if env_cxx is not None:
        env["CXX"] = env_cxx
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_schema_is_well_formed():
    schema = _load_schema()
    jsonschema.Draft7Validator.check_schema(schema)


def test_run_happy_path(tmp_path):
    out = tmp_path / "run_ok"
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "42",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 0, result.stderr

    results_path = out / "results.json"
    assert results_path.is_file(), "results.json missing"
    payload = json.loads(results_path.read_text())

    jsonschema.validate(payload, _load_schema())

    assert payload["status"] == "ok"
    assert payload["schema_version"] == 1
    assert payload["match"] is not None
    assert payload["match"]["outcome"] in {"A_WIN", "B_WIN", "DRAW"}
    assert payload["match"]["ticks"] > 0
    assert payload["match"]["wall_ms"] > 0
    # Both SHAs are lower-hex, 64 chars
    assert len(payload["team_a"]["source_sha256"]) == 64
    assert len(payload["team_b"]["source_sha256"]) == 64
    # Trace path exists by default.
    trace_path = payload["artifacts"]["trace_path"]
    assert trace_path is not None and Path(trace_path).is_file()


def test_run_matches_golden_outcome_seed42(tmp_path):
    out = tmp_path / "run_golden"
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "42",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads((out / "results.json").read_text())
    # Matches tests/fixtures/golden/seed42_pursuit_vs_cluster.jsonl
    assert payload["match"]["outcome"] == "DRAW"
    assert payload["match"]["ticks"] == 116


def test_run_no_record_no_trace(tmp_path):
    out = tmp_path / "no_record"
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(BASELINES / "stationary_v1.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
            "--no-record",
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((out / "results.json").read_text())
    assert payload["status"] == "ok"
    assert payload["artifacts"]["trace_path"] is None


def test_run_missing_team_a_exits_2(tmp_path):
    out = tmp_path / "missing"
    result = _run(
        [
            "run",
            "--team-a",
            str(tmp_path / "does_not_exist.cpp"),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 2
    # Structured JSON error on stderr.
    assert "missing-team-a-source" in result.stderr
    # No results.json when input validation fails before any work.
    assert not (out / "results.json").exists()


def test_run_missing_team_b_exits_2(tmp_path):
    out = tmp_path / "missing_b"
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(tmp_path / "nope.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 2
    assert "missing-team-b-source" in result.stderr


def test_run_compile_failure_exits_3_and_writes_results(tmp_path):
    broken = tmp_path / "broken.cpp"
    broken.write_text(
        "// Intentionally malformed AI source to exercise compile_failed path.\n"
        "namespace TEAM_NS_PLACEHOLDER {\n"
        "  this is not valid C++;\n"
        "}\n"
    )
    out = tmp_path / "compile_fail"
    result = _run(
        [
            "run",
            "--team-a",
            str(broken),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 3
    payload = json.loads((out / "results.json").read_text())
    jsonschema.validate(payload, _load_schema())
    assert payload["status"] == "compile_failed"
    assert payload["compile"]["return_code"] != 0
    # Compiler stderr is captured so developers can debug without re-running.
    assert payload["compile"]["stderr"] != ""
    assert payload["match"] is None


def test_run_timeout_exits_4(tmp_path):
    out = tmp_path / "timeout"
    # Force a timeout by setting it absurdly small; compile still succeeds.
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
            "--timeout",
            "0.001",
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 4
    payload = json.loads((out / "results.json").read_text())
    jsonschema.validate(payload, _load_schema())
    assert payload["status"] == "timeout"
    assert payload["match"] is None


def test_run_writes_only_inside_out_dir(tmp_path):
    out = tmp_path / "isolation"
    result = _run(
        [
            "run",
            "--team-a",
            str(BASELINES / "pursuit_v1.cpp"),
            "--team-b",
            str(BASELINES / "cluster_v1.cpp"),
            "--seed",
            "0",
            "--out-dir",
            str(out),
        ],
        env_cxx=CXX,
    )
    assert result.returncode == 0
    # Every artifact mentioned in results.json must live inside out_dir
    # (resolved, to dodge symlink tricks).
    payload = json.loads((out / "results.json").read_text())
    out_resolved = out.resolve()
    for key in ("trace_path", "video_path", "binary_path"):
        art = payload["artifacts"][key]
        if art is None:
            continue
        art_resolved = Path(art).resolve()
        assert str(art_resolved).startswith(str(out_resolved) + "/") or art_resolved == out_resolved
