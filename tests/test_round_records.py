"""scripts/round_records.py: AAR recording, prompt files, similarity,
grounding and lesson formatting for the evolution loops."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dual_llm
import round_records as rr
import sandbox
import tactic_spec as ts
import telemetry_aar
from llm_client import LLMResponse, MockClient

from tests.test_dual_llm import PLANNER_JSON

BASELINES = REPO_ROOT / "src" / "baselines"

AAR = {
    "metrics_schema_version": 2,
    "perspective": "A",
    "outcome": "TEAM_B_WIN",
    "ticks": 412,
    "alive_final_us": 0,
    "alive_final_them": 3,
    "shots_fired_us": 12,
    "shots_fired_them": 16,
    "shots_hit_us": 7,
    "shots_hit_them": 10,
    "focus_fire_redundancy": 0.5833,
    "cooldown_utilization_us": 0.3125,
    "cooldown_utilization_them": 0.8912,
    "mean_pairwise_distance_us": 12.3456,
    "mean_pairwise_distance_them": 80.0,
    "dispersion_index_us": 0.1,
    "dispersion_index_them": 0.2,
    "message_bus_used": False,
    "message_bus_entropy": 0.0,
    "long_range_shot_share_them": 0.25,
    "engagement_range_mean": 41.2,
}


def _spec(key_metrics, changes):
    return ts.TacticSpec(
        key_metrics=key_metrics,
        expected_changes=[ts.MetricChange(m, old, 0, "r") for m, old in changes],
    )


@pytest.mark.parametrize(
    ("line", "matched"),
    [
        ("Cooldown utilization: ours=0.31", True),  # cited to 2 decimals
        ("Cooldown utilization: ours=31%", True),  # as a percentage
        ("Cooldown utilization: ours=31.25%", True),
        ("Cooldown utilization: ours=0.35", False),
        ("Hit rate: us=58.33%", True),  # derived: 7 / 12
        ("Mean pairwise: 12.35", True),
        ("Mean pairwise: 12.4", False),
        ("Ticks: 412", True),
        ("Ticks: 4120%", False),  # % only matches fraction fields
        ("Shots fired: 99", False),
    ],
)
def test_key_metric_numbers_match_at_cited_precision(line, matched):
    g = rr.grounding(_spec([line], []), AAR)
    assert g["cited"] == 1 and g["items"][0]["matched"] is matched


@pytest.mark.parametrize(
    ("metric", "old", "matched"),
    [
        ("cooldown_utilization_us", 0.31, True),
        ("cooldown_utilization_us", 0.89, False),  # right number, wrong metric
        ("ticks", 412, True),
        ("ticks", "412", True),
        ("outcome", "LOSS", True),  # Team A's side of TEAM_B_WIN
        ("outcome", "WIN", False),
        ("outcome", "TEAM_B_WIN", True),
        ("long_range_shot_share_them", 0.7, False),
        ("kiting_score_them", 0.25, False),  # not an AAR metric
    ],
)
def test_expected_change_old_values_match_their_own_metric(metric, old, matched):
    g = rr.grounding(_spec([], [(metric, old)]), AAR)
    assert g["items"] == [
        {
            "source": "expected_changes[0].old_value",
            "metric": metric,
            "cited": str(old),
            "matched": matched,
            "aar_field": metric if matched else None,
        }
    ]


def test_grounding_counts():
    g = rr.grounding(_spec(["Survivors: us=0, them=3", "Shots: 99"], [("ticks", 500)]), AAR)
    assert (g["cited"], g["matched"], g["unmatched"], g["share_matched"]) == (4, 2, 2, 0.5)
    assert rr.grounding(_spec([], []), AAR)["share_matched"] is None


def test_similarity():
    same = rr.similarity("int a;\nint b;\n", "int a;\nint b;\n")
    assert same["line_ratio"] == same["token_ratio"] == 1.0
    diff = rr.similarity("int a;\nint b;\n", "int a;\nint c;\n")
    assert diff["line_ratio"] == 0.5 and diff["token_ratio"] == pytest.approx(5 / 6, abs=1e-4)
    assert rr.similarity("", "int a;")["token_ratio"] == 0.0


def test_prompt_saver_names_retries(tmp_path):
    bad = LLMResponse(text="no json here", model="p")
    planner = MockClient([bad, LLMResponse(text=PLANNER_JSON, model="p")])
    coder = MockClient([LLMResponse(text="```cpp\nnamespace TeamA {}\n```", model="c")])
    saver = rr.PromptSaver(tmp_path)
    dual_llm.dual_llm_generate(
        planner_client=planner, coder_client=coder, team_letter="A", namespace="TeamA",
        opponent_name="o", opponent_source="// o", parent_source="// p",
        aar_markdown="# AAR\n", on_prompt=saver,
    )  # fmt: skip
    assert saver.planner_attempts == 2
    assert (tmp_path / "planner_prompt.md").read_text() == planner.calls[0]
    assert (tmp_path / "planner_prompt_2.md").read_text() == planner.calls[1]
    assert (tmp_path / "coder_prompt.md").read_text() == coder.calls[0]


def test_format_lessons_keeps_the_last_three():
    entries = [
        {"generation": g, "verdict": "rejected", "fitness": 0.1 * g, "hypothesis_tested": f"h{g}"}
        for g in range(5)
    ]
    entries[4]["fitness"] = None
    assert rr.format_lessons(entries) == (
        "Gen 2: rejected (fitness=0.200) - h2\n"
        "Gen 3: rejected (fitness=0.300) - h3\n"
        "Gen 4: rejected (fitness=n/a) - h4"
    )
    assert rr.format_lessons([]) is None


def test_aar_seed_is_fixed_per_round_and_apart_from_evaluation_seeds():
    seeds = {rr.aar_seed(42, r) for r in range(200)}
    assert len(seeds) == 200
    assert not seeds & {42 + r * 1000 + i for r in range(200) for i in range(400)}


def test_record_aar_on_the_host_writes_md_and_json(tmp_path):
    """Trusted baselines, so the host backend (the suite's default) is fine."""
    report = rr.record_aar(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        team="B",
        seed=7,
        round_dir=tmp_path,
    )
    metrics = json.loads((tmp_path / "aar.json").read_text())
    assert metrics == report.structured and metrics["perspective"] == "B"
    assert (tmp_path / "aar.md").read_text() == telemetry_aar.render_markdown(metrics)
    assert (tmp_path / "aar_trace.jsonl").is_file()


@pytest.fixture()
def home_tmp() -> Iterator[Path]:
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="round_records_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.docker
def test_record_aar_in_the_container_matches_the_host(home_tmp, monkeypatch, tmp_path):
    try:
        rt = sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")
    if not sandbox.image_present(sandbox.DEFAULT_IMAGE, runtime=rt):
        pytest.skip(f"eval image '{sandbox.DEFAULT_IMAGE}' not found")
    host = rr.record_aar(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        team="A",
        seed=11,
        round_dir=tmp_path,
    )
    monkeypatch.setenv("SWARMEVOLVE_FITNESS_BACKEND", "sandbox")
    box = rr.record_aar(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        team="A",
        seed=11,
        round_dir=home_tmp,
    )
    assert box.structured == host.structured and box.markdown == host.markdown
