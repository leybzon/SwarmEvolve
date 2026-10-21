"""Tests for the setup report scripts: host_report, health_check and
check_model_access. Offline: the Anthropic client is replaced by a fake."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_model_access
import health_check
import host_report

FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKE"


def test_host_report_renders_from_collected_data(tmp_path):
    assert host_report.main(["--out-dir", str(tmp_path)]) == 0
    info = json.loads((tmp_path / "host.json").read_text())
    md = (tmp_path / "HOST.md").read_text()
    assert info["logical_cpus"] >= 1
    assert info["cpu_model"] in md
    assert "**dev**" in md


def _neutral(mean, lo, hi, invalid=0):
    return {
        "team_a": "x",
        "team_b": "x",
        "result": {
            "seed_base": 100000,
            "n_matches": 10,
            "wins_a": 1,
            "wins_b": 1,
            "draws": 8,
            "invalid": invalid,
            "mean": mean,
            "ci_low": lo,
            "ci_high": hi,
        },
    }


def _results(neutrality):
    return {
        "platform_label": "dev",
        "git_sha": "0" * 40,
        "compiler": "g++",
        "compiler_family": "gcc",
        "workers": 2,
        "started_utc": "2026-01-01T00:00:00Z",
        "neutrality": neutrality,
        "throughput": {
            "team_a": "a",
            "team_b": "b",
            "wall_seconds_measured": 2.0,
            "result": {
                "n_matches": 10,
                "seed_base": 200000,
                "workers": 2,
                "invalid": 0,
                "per_match": [{"wall_ms": 1}] * 10,
            },
        },
    }


def test_health_render_passes_only_when_every_ci_includes_zero():
    ok = health_check.render(_results([_neutral(0.0, -0.1, 0.1)]), "r.json")
    bad = health_check.render(
        _results([_neutral(0.0, -0.1, 0.1), _neutral(0.2, 0.1, 0.3)]), "r.json"
    )
    assert "Side neutrality overall: **pass**" in ok
    assert "Side neutrality overall: **FAIL**" in bad
    assert "| Matches per second (wall) | 5.0 |" in ok


def test_health_invalid_matches_fail_neutrality():
    md = health_check.render(_results([_neutral(0.0, -0.1, 0.1, invalid=1)]), "r.json")
    assert "**FAIL**" in md


def test_health_check_refuses_to_overwrite_results(tmp_path):
    (tmp_path / "health_results.json").write_text("{}")
    with pytest.raises(SystemExit):
        health_check.main(["--out-dir", str(tmp_path), "--n-matches", "2"])


class _FakeModels:
    def __init__(self, anthropic_mod):
        self.a = anthropic_mod

    def retrieve(self, model_id):
        import httpx

        req = httpx.Request("GET", f"https://api.anthropic.com/v1/models/{model_id}")
        if model_id == "missing":
            raise self.a.NotFoundError(
                "not found", response=httpx.Response(404, request=req), body=None
            )
        if model_id == "denied":
            raise self.a.PermissionDeniedError(
                f"key {FAKE_KEY} lacks access",
                response=httpx.Response(403, request=req),
                body=None,
            )

        class M:
            display_name = "Claude Test"

        return M()


def test_model_access_statuses_and_key_never_written(tmp_path, monkeypatch, capsys):
    import anthropic

    class FakeClient:
        def __init__(self, api_key, max_retries):
            assert api_key == FAKE_KEY
            self.models = _FakeModels(anthropic)

    monkeypatch.setenv("SWARMEVOLVE_ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    rc = check_model_access.main(
        ["--out-dir", str(tmp_path), "--models", "ok-model", "missing", "denied"]
    )
    assert rc == 0
    data = json.loads((tmp_path / "model_access.json").read_text())
    assert [r["status"] for r in data["models"]] == [
        "available",
        "not_found",
        "permission_denied",
    ]
    written = (tmp_path / "model_access.json").read_text() + (
        tmp_path / "MODEL_ACCESS.md"
    ).read_text()
    out = capsys.readouterr()
    for text in (written, out.out, out.err):
        assert FAKE_KEY not in text
        assert "sk-ant-" not in text


def test_model_access_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARMEVOLVE_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(check_model_access, "REPO_ROOT", tmp_path)
    assert check_model_access.main(["--out-dir", str(tmp_path)]) == 1
