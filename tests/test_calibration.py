"""Tests for scripts/calibration.py helpers (host backend, trusted baselines)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import calibration
import fitness
import tactic_detector as td
import telemetry_aar

BASELINES = REPO_ROOT / "src" / "baselines"


def test_tilt_reaches_target_mean_and_keeps_support():
    support = np.array([-20.0, -10.0, 0.0, 10.0, 20.0])
    probs = np.array([0.1, 0.2, 0.4, 0.2, 0.1])
    for delta in (2.0, 4.0, 6.0):
        tilted = calibration._tilt(support, probs, delta)
        assert tilted is not None
        assert abs((tilted * support).sum() - delta) < 1e-6
        assert (tilted > 0).all() and abs(tilted.sum() - 1) < 1e-12
    assert calibration._tilt(support, probs, 25.0) is None
    assert calibration._tilt(support, probs, 0.0) is probs


def test_power_is_calibrated_under_the_null_and_grows_with_n():
    model = {
        "support_units": [-20.0, -10.0, 0.0, 10.0, 20.0],
        "probs": [0.15, 0.15, 0.4, 0.15, 0.15],
        "sd": 0.6,
    }
    power = calibration.simulate_power(model, seed=1)
    for n in calibration.POWER_MATCHES:
        assert power[f"0.0|{n}"] <= calibration.POWER_ALPHA + 0.03
    p = [power[f"0.1|{n}"] for n in calibration.POWER_MATCHES]
    assert p == sorted(p) and p[-1] > p[0]


def test_power_skips_zero_spread_models():
    model = {"support_units": [0.0], "probs": [1.0], "sd": 0.0}
    assert all(v is None for v in calibration.simulate_power(model, seed=1).values())


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("calib_trace")
    trace = tmp / "t.jsonl"
    r = fitness.evaluate_fitness(
        BASELINES / "cluster_v1.cpp",
        BASELINES / "pursuit_v1.cpp",
        n_matches=1,
        seed_base=5,
        workers=1,
        backend="host",
        scratch_root=tmp,
        record_trace_for_match_idx=0,
        trace_output_path=trace,
    )
    return trace, r.per_match[0]


@pytest.mark.parametrize("perspective", ["A", "B"])
def test_aar_checks_pass_on_a_real_trace(recorded, perspective):
    trace, row = recorded
    records = telemetry_aar.load_v2_trace(trace)
    aar = telemetry_aar.render_aar(trace, perspective=perspective).structured
    checks = calibration.check_aar_against_trace(records, aar, row, perspective)
    assert checks and all(checks.values()), checks


def test_aar_checks_catch_a_wrong_number(recorded):
    trace, row = recorded
    records = telemetry_aar.load_v2_trace(trace)
    aar = dict(telemetry_aar.render_aar(trace, perspective="A").structured)
    aar["shots_hit_us"] += 1
    aar["cooldown_utilization_us"] += 0.01
    checks = calibration.check_aar_against_trace(records, aar, row, "A")
    assert not checks["shots_hit_us"] and not checks["cooldown_utilization_us"]


@pytest.mark.parametrize("perspective", ["a", "b"])
def test_measures_agree_with_detector_events(recorded, perspective):
    trace, _ = recorded
    records = telemetry_aar.load_v2_trace(trace)
    measures = calibration._measures(records, perspective)
    fired = {ev.tactic for ev in td.scan_trace(records, perspective=perspective)}
    for tactic in td.TACTICS:
        assert (tactic in fired) == measures[tactic]["fires"], tactic
    assert measures["max_speed"] > 0  # cluster_v1 and pursuit_v1 both move


def test_instrument_jobs_cover_every_ordered_pairing():
    jobs = calibration.instrument_jobs()
    assert len(jobs) == calibration.INSTRUMENT_MATCHES
    assert len({(j["team_a"], j["team_b"]) for j in jobs}) == 9
    assert len({j["seed"] for j in jobs}) == len(jobs)


@pytest.mark.parametrize("perspective", ["A", "B"])
def test_outcome_check_maps_trace_names_on_a_win(tmp_path, perspective):
    """Regression: the trace says TEAM_A_WIN where the result row says
    A_WIN, and the check compared the raw strings, failing every win."""
    trace = tmp_path / "win.jsonl"
    r = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "stationary_v1.cpp",
        n_matches=1,
        seed_base=7,
        workers=1,
        backend="host",
        scratch_root=tmp_path,
        record_trace_for_match_idx=0,
        trace_output_path=trace,
    )
    row = r.per_match[0]
    assert row["outcome"] == "A_WIN"
    records = telemetry_aar.load_v2_trace(trace)
    aar = telemetry_aar.render_aar(trace, perspective=perspective).structured
    checks = calibration.check_aar_against_trace(records, aar, row, perspective)
    assert checks["outcome_matches_engine"], checks
    assert all(checks.values()), checks
