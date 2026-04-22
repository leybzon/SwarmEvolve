"""Pure-CLI tests for ``scripts/orchestrator.py``.

These tests do not need a C++ compiler — they exercise argument parsing,
help text, error messages, and the stub sub-commands. Kept separate from
``test_orchestrator_run.py`` so they can run in environments that skip
the compile-heavy tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = REPO_ROOT / "scripts" / "orchestrator.py"

# Import the module directly for in-process parser tests.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import orchestrator  # noqa: E402


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR), *args],
        capture_output=True, text=True, check=False,
    )


def test_help_exits_zero():
    result = _run(["--help"])
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "evaluate" in result.stdout
    assert "evolve" in result.stdout
    assert "tournament" in result.stdout


def test_run_help_mentions_required_flags():
    result = _run(["run", "--help"])
    assert result.returncode == 0
    assert "--team-a" in result.stdout
    assert "--team-b" in result.stdout
    assert "--seed" in result.stdout


def test_missing_subcommand_exits_nonzero():
    result = _run([])
    assert result.returncode != 0


def test_unknown_subcommand_exits_2():
    result = _run(["banana"])
    assert result.returncode == 2


def test_run_requires_team_a():
    result = _run(["run", "--team-b", "/tmp/x.cpp"])
    assert result.returncode != 0
    assert "--team-a" in result.stderr


def test_run_rejects_negative_seed():
    result = _run(["run", "--team-a", "/tmp/a.cpp", "--team-b", "/tmp/b.cpp",
                   "--seed", "-1"])
    assert result.returncode != 0


def test_run_rejects_zero_max_ticks():
    result = _run(["run", "--team-a", "/tmp/a.cpp", "--team-b", "/tmp/b.cpp",
                   "--max-ticks", "0"])
    assert result.returncode != 0


def test_run_rejects_zero_drones():
    result = _run(["run", "--team-a", "/tmp/a.cpp", "--team-b", "/tmp/b.cpp",
                   "--drones-a", "0"])
    assert result.returncode != 0


def test_evaluate_requires_team_paths():
    # M9 promoted `evaluate` from stub → real. Missing required args are
    # argparse errors (exit 2) rather than the old "not-implemented" stub.
    result = _run(["evaluate"])
    assert result.returncode == 2  # argparse's own usage-error code
    assert "--team-a" in result.stderr


def test_evolve_stub_returns_invalid_input():
    result = _run(["evolve"])
    assert result.returncode == orchestrator.EXIT_INVALID_INPUT
    assert "not-implemented" in result.stderr


def test_tournament_stub_returns_invalid_input():
    result = _run(["tournament"])
    assert result.returncode == orchestrator.EXIT_INVALID_INPUT
    assert "not-implemented" in result.stderr


# -----------------------------------------------------------------------
# In-process tests for helper functions
# -----------------------------------------------------------------------

def test_parser_builds_without_error():
    parser = orchestrator.build_parser()
    ns = parser.parse_args(["run",
                            "--team-a", "/tmp/a.cpp",
                            "--team-b", "/tmp/b.cpp"])
    assert ns.command == "run"
    assert ns.seed == 0
    assert ns.max_ticks == 1000
    assert ns.drones_a == 10
    assert ns.drones_b == 10
    assert ns.record is True
    assert ns.video is False


def test_parser_no_record_flag():
    parser = orchestrator.build_parser()
    ns = parser.parse_args(["run",
                            "--team-a", "/tmp/a.cpp",
                            "--team-b", "/tmp/b.cpp",
                            "--no-record"])
    assert ns.record is False


def test_detect_compiler_respects_cxx_env(monkeypatch, tmp_path):
    fake = tmp_path / "fakecxx"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CXX", str(fake))
    assert orchestrator.detect_compiler() == str(fake)


def test_detect_compiler_returns_none_when_nothing_available(monkeypatch):
    monkeypatch.delenv("CXX", raising=False)
    # Force every candidate lookup to miss. ``detect_compiler`` uses both
    # ``shutil.which`` (absolute candidates and bare names) and
    # ``Path.is_file`` (absolute Homebrew paths) — stub both.
    import shutil as _sh
    import pathlib as _pl
    original_is_file = _pl.Path.is_file

    def _never_file(self):
        s = str(self)
        if s.endswith("/clang++") or s.endswith("/nvc++") or s.endswith("/g++"):
            return False
        return original_is_file(self)

    monkeypatch.setattr(_sh, "which", lambda _cmd: None)
    monkeypatch.setattr(_pl.Path, "is_file", _never_file)
    assert orchestrator.detect_compiler() is None


def test_json_formatter_emits_single_line():
    import io
    import json
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(orchestrator.JsonFormatter())
    logger = logging.getLogger("test-json-formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.info("hello", extra={"extra_fields": {"k": "v"}})
    line = stream.getvalue().strip()
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["msg"] == "hello"
    assert payload["k"] == "v"
    assert payload["level"] == "INFO"


def test_stubs_raise_not_implemented():
    import llm_client as _llm  # noqa: E402 — scripts/ on sys.path above

    with pytest.raises(NotImplementedError):
        _llm.StubClient().generate("hi")
    # NB: fitness.evaluate_fitness is no longer a stub as of M9 — see
    # test_fitness.py for its real-behaviour contract tests.


def test_mock_llm_client_returns_queued_responses():
    import llm_client as _llm  # noqa: E402

    client = _llm.MockClient([
        _llm.LLMResponse(text="first", model="mock"),
        _llm.LLMResponse(text="second", model="mock"),
    ])
    assert client.generate("p1").text == "first"
    assert client.generate("p2").text == "second"
    assert client.calls == ["p1", "p2"]

    with pytest.raises(RuntimeError):
        client.generate("p3")
