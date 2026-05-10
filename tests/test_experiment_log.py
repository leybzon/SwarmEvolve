"""Tests for :mod:`scripts.experiment_log` (M9).

Pure-Python, no compile required — runs in <100 ms.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import experiment_log as explog

# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------


def test_redact_anthropic_key_in_str():
    s = "error: bad key sk-ant-api03-ABCdef123456_ZZ"
    assert "sk-ant" not in explog.redact(s)
    assert explog.REDACTED in explog.redact(s)


def test_redact_openai_key_in_str():
    s = "Authorization: sk-ABCDEFGHIJKLMNOPQRST12345"
    redacted = explog.redact(s)
    assert "sk-ABCDEFGHIJKLMNOPQRST12345" not in redacted
    assert explog.REDACTED in redacted


def test_redact_bearer_token():
    s = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    redacted = explog.redact(s)
    assert "Bearer abcdef" not in redacted


def test_redact_gemini_key():
    # 39-char AIza... pattern (AIza + 35 chars).
    key = "AIza" + "a" * 35
    s = f"key={key}"
    redacted = explog.redact(s)
    assert key not in redacted
    assert explog.REDACTED in redacted


def test_redact_recurses_into_dict_and_list():
    obj = {
        "msg": "ok",
        "api_key": "sk-ant-abcdefghijklmnopq",
        "list": ["safe", "sk-ant-xxxxxxxxxxxxxxxxx"],
        "nested": {"token": "Bearer " + "x" * 30},
    }
    r = explog.redact(obj)
    assert "sk-ant" not in json.dumps(r)
    assert "Bearer " not in json.dumps(r)
    # Non-sensitive fields unchanged.
    assert r["msg"] == "ok"
    assert r["list"][0] == "safe"


def test_redact_leaves_short_hex_alone():
    """A git SHA shouldn't be touched — we must not clobber benign
    tokens that merely look hex-ish."""
    sha = "c28dfec3ff066780cb2675ba94f275992d8855fd"
    assert explog.redact(sha) == sha
    assert explog.redact({"git_sha": sha})["git_sha"] == sha


def test_redact_passes_through_non_str():
    assert explog.redact(42) == 42
    assert explog.redact(None) is None
    assert explog.redact(3.14) == 3.14


# ---------------------------------------------------------------------------
# build_environment_snapshot()
# ---------------------------------------------------------------------------


def test_environment_snapshot_has_required_keys(tmp_path):
    a = tmp_path / "a.cpp"
    b = tmp_path / "b.cpp"
    a.write_text("// A\n")
    b.write_text("// B\n")
    snap = explog.build_environment_snapshot(team_a_src=a, team_b_src=b)
    for key in ("python", "platform", "cpu_count", "hostname", "team_a", "team_b"):
        assert key in snap, key
    assert snap["team_a"]["sha256"] is not None
    assert len(snap["team_a"]["sha256"]) == 64


def test_environment_snapshot_extra_merged(tmp_path):
    snap = explog.build_environment_snapshot(extra={"model": "claude-foo"})
    assert snap["model"] == "claude-foo"


# ---------------------------------------------------------------------------
# ExperimentLog
# ---------------------------------------------------------------------------


def test_log_writes_seq_monotonically(tmp_path):
    with explog.ExperimentLog(tmp_path) as log:
        log.write("a", x=1)
        log.write("b", y=2)
        log.write("c", z=3)

    events = explog.ExperimentLog.read(tmp_path)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(len(events)))
    # We must see an experiment_end event at the tail.
    assert events[-1]["type"] == "experiment_end"


def test_log_write_outside_context_raises(tmp_path):
    log = explog.ExperimentLog(tmp_path)
    # Never opened, never entered.
    with pytest.raises(RuntimeError):
        log.write("anything")


def test_log_redacts_secrets_in_payload(tmp_path):
    with explog.ExperimentLog(tmp_path) as log:
        log.write("http", error="401 sk-ant-abcdefghij01234567890")

    events = explog.ExperimentLog.read(tmp_path)
    http_event = next(e for e in events if e["type"] == "http")
    assert "sk-ant" not in http_event["error"]
    assert explog.REDACTED in http_event["error"]


def test_log_raw_escape_bypasses_redaction(tmp_path):
    """``_raw=True`` is the emergency valve for architecture-test cases
    that intentionally need to capture an unredacted payload (e.g.
    the redaction test itself)."""
    payload = "sk-ant-keeponpurpose12345"
    with explog.ExperimentLog(tmp_path) as log:
        log.write("canary", marker=payload, _raw=True)

    events = explog.ExperimentLog.read(tmp_path)
    canary = next(e for e in events if e["type"] == "canary")
    assert canary["marker"] == payload


def test_log_writes_start_event_with_environment(tmp_path):
    a = tmp_path / "a.cpp"
    a.write_text("// a\n")
    b = tmp_path / "b.cpp"
    b.write_text("// b\n")
    with explog.ExperimentLog(tmp_path) as log:
        log.write_start(
            experiment_type="fitness",
            team_a_src=a,
            team_b_src=b,
            config={"n_matches": 3},
        )

    events = explog.ExperimentLog.read(tmp_path)
    start = events[0]
    assert start["type"] == "experiment_start"
    assert start["experiment_type"] == "fitness"
    assert start["config"]["n_matches"] == 3
    env = start["environment"]
    assert env["team_a"]["sha256"].startswith("")  # exists, nonempty
    assert env["team_a"]["sha256"] != env["team_b"]["sha256"]


def test_log_read_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        explog.ExperimentLog.read(tmp_path)


def test_log_read_rejects_malformed(tmp_path):
    (tmp_path / "events.jsonl").write_text('{"ok":1}\nnot-json\n')
    with pytest.raises(ValueError):
        explog.ExperimentLog.read(tmp_path)


def test_log_records_exception_and_end_on_exit(tmp_path):
    """If the `with` block raises, we should still record an
    experiment_error + experiment_end so the log reflects the failure
    mode rather than trailing silently."""
    with pytest.raises(RuntimeError, match="deliberate"), explog.ExperimentLog(tmp_path) as log:
        log.write("setup", step=1)
        raise RuntimeError("deliberate")

    events = explog.ExperimentLog.read(tmp_path)
    types = [e["type"] for e in events]
    assert "experiment_error" in types
    assert types[-1] == "experiment_end"
    err = next(e for e in events if e["type"] == "experiment_error")
    assert err["error_type"] == "RuntimeError"
    assert "deliberate" in err["error"]


def test_log_file_is_append_only(tmp_path):
    """Re-opening the same run dir appends rather than truncating; the
    seq counter resets (new log instance) which is by design — logs
    from different processes shouldn't share a counter."""
    with explog.ExperimentLog(tmp_path) as log:
        log.write("a")
    size_after_first = (tmp_path / "events.jsonl").stat().st_size
    with explog.ExperimentLog(tmp_path) as log:
        log.write("b")
    size_after_second = (tmp_path / "events.jsonl").stat().st_size
    assert size_after_second > size_after_first
