"""Offline end-to-end test for ``scripts/evolve_once.py``.

Uses the ``--client=mock --mock-response-path=...`` path to skip the
network, feeding a canned "LLM response" (the frozen ``pursuit_v1``
baseline wrapped in a cpp fence). Asserts the full pipeline runs:
parse → lint → inject → compile (via orchestrator) → match. Serves as
the regression gate for the scaffolding even when the real API is
unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._build_helper import CXX

REPO_ROOT = Path(__file__).resolve().parents[1]
EVOLVE_ONCE = REPO_ROOT / "scripts" / "evolve_once.py"
BASELINES = REPO_ROOT / "src" / "baselines"

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


def _run(args, env_cxx=CXX):
    import os as _os
    env = dict(_os.environ)
    if env_cxx is not None:
        env["CXX"] = env_cxx
    return subprocess.run(
        [sys.executable, str(EVOLVE_ONCE), *args],
        capture_output=True, text=True, check=False, env=env,
    )


def _make_fenced_response(src_path: Path, namespace: str, tmp_path: Path) -> Path:
    """Wrap a frozen baseline (with placeholder → namespace substitution
    applied) in a cpp fence so the pipeline treats it like an LLM reply."""
    text = src_path.read_text().replace("TEAM_NS_PLACEHOLDER", namespace)
    out = tmp_path / "mock_response.md"
    out.write_text("Here you go:\n\n```cpp\n" + text + "\n```\n")
    return out


def test_dry_run_produces_prompt_only(tmp_path):
    out_dir = tmp_path / "dry"
    result = _run([
        "--opponent", str(BASELINES / "stationary_v1.cpp"),
        "--dry-run",
        "--out-dir", str(out_dir),
    ])
    assert result.returncode == 0, result.stderr
    assert (out_dir / "prompt.md").is_file()
    summary = json.loads((out_dir / "evolve_once.json").read_text())
    assert summary["status"] == "dry_run"


def test_mock_pipeline_end_to_end(tmp_path):
    """Pursuit (mocked) vs stationary should win — validates the whole
    chain without touching the network."""
    mock_resp = _make_fenced_response(BASELINES / "pursuit_v1.cpp",
                                       "TeamA", tmp_path)
    out_dir = tmp_path / "mock_run"
    result = _run([
        "--opponent", str(BASELINES / "stationary_v1.cpp"),
        "--as-team", "A",
        "--seed", "0",
        "--max-ticks", "1000",
        "--client", "mock",
        "--mock-response-path", str(mock_resp),
        "--out-dir", str(out_dir),
    ])
    assert result.returncode == 0, result.stderr

    summary = json.loads((out_dir / "evolve_once.json").read_text())
    assert summary["status"] == "ok"
    assert summary["match_outcome"] == "A_WIN"
    assert summary["match_ticks"] is not None and summary["match_ticks"] > 0

    # Artifacts exist.
    assert (out_dir / "candidate.cpp").is_file()
    assert (out_dir / "candidate.injected.cpp").is_file()
    # Guard marker should appear because the injector ran.
    injected = (out_dir / "candidate.injected.cpp").read_text()
    assert "@swarmevolve:guards-injected" in injected


def test_mock_pipeline_lint_rejects_banned_tokens(tmp_path):
    """A fenced response that violates the banned-token linter must be
    caught before compilation, with ``status="lint_failed"``."""
    dirty = tmp_path / "dirty_response.md"
    dirty.write_text(
        "```cpp\n"
        "#include \"../ai_abi.h\"\n"
        "#include \"../types.h\"\n"
        "#include <vector>\n"  # banned
        "namespace TeamA {\n"
        "#pragma acc routine seq\n"
        "void drone_ai(int, const GameParams*, const AllyState*,\n"
        "              const EnemyState*, const float[][MSG_SIZE],\n"
        "              float*, Action* out) {\n"
        "    std::vector<int> v; (void)v;\n"
        "    out->velocity.x = 0; out->velocity.y = 0; out->target_id = -1;\n"
        "    for (int i = 0; i < MSG_SIZE; ++i) out->message_out[i] = 0;\n"
        "}\n"
        "}\n"
        "```\n"
    )
    out_dir = tmp_path / "lint_fail"
    result = _run([
        "--opponent", str(BASELINES / "stationary_v1.cpp"),
        "--client", "mock",
        "--mock-response-path", str(dirty),
        "--out-dir", str(out_dir),
    ])
    # evolve_once returns its own EXIT_LINT_FAILED=22.
    assert result.returncode == 22
    summary = json.loads((out_dir / "evolve_once.json").read_text())
    assert summary["status"] == "lint_failed"
    assert summary["lint_violations"], "expected at least one lint violation"
    # The std::vector line is the one we planted.
    assert any("vector" in v["reason"] for v in summary["lint_violations"])


def test_mock_pipeline_parse_fail_when_no_fence(tmp_path):
    noresp = tmp_path / "no_fence.md"
    noresp.write_text("sorry i have no cpp for you today")
    out_dir = tmp_path / "parse_fail"
    result = _run([
        "--opponent", str(BASELINES / "stationary_v1.cpp"),
        "--client", "mock",
        "--mock-response-path", str(noresp),
        "--out-dir", str(out_dir),
    ])
    assert result.returncode == 21  # EXIT_PARSE_FAILED
    summary = json.loads((out_dir / "evolve_once.json").read_text())
    assert summary["status"] == "parse_failed"
