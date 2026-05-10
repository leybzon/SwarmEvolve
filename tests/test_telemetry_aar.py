"""Tests for ``scripts/telemetry_aar.py`` (M15b).

Three layers of coverage:

  1. Handcrafted 3-tick v2 fixture with known attack events: every
     derived metric is asserted to an exact rational value. This is the
     primary guarantee that AAR metrics mean what their names suggest.

  2. Baseline replay (pursuit_v1 vs cluster_v1, seed=42): smoke-tests the
     full CLI pipeline end-to-end, asserts invariants that must hold on
     any real match (shots_fired >= shots_hit, cooldown_utilization in
     [0, 1], etc.).

  3. Determinism: two successive calls on the same trace produce
     byte-identical JSON (no dict iteration order leaks, no time.time(),
     no floats with implementation-defined rounding in the formatter).

The fixture is constructed as an in-memory list of records that we feed
directly to ``compute_metrics`` to avoid a compiler dependency for layer 1.
Layer 2 and 3 use the full build helper.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._build_helper import CXX, build_matchup

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from telemetry_aar import (
    AARReport,
    compute_metrics,
    estimate_tokens,
    load_v2_trace,
    render_aar,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _drone(did: int, x: float, y: float, cd: int = 0, alive: bool = True) -> dict:
    return {"id": did, "x": x, "y": y, "cooldown": cd, "alive": alive}


def _action(
    did: int,
    alive: bool = True,
    vx: float = 0.0,
    vy: float = 0.0,
    target_id: int = -1,
    msg=(0.0, 0.0, 0.0, 0.0),
) -> dict:
    return {
        "id": did,
        "alive": alive,
        "vx": vx,
        "vy": vy,
        "target_id": target_id,
        "msg": list(msg),
    }


def _attack(atk_team: int, atk_id: int, tgt_team: int, tgt_id: int, hit: bool, dist: float) -> dict:
    return {
        "atk_team": atk_team,
        "atk_id": atk_id,
        "tgt_team": tgt_team,
        "tgt_id": tgt_id,
        "hit": hit,
        "dist": dist,
    }


def _build_fixture() -> list[dict]:
    """3-tick match: 2v2, Team A kills both Team B drones with 3 shots.

    Tick 0: spawn. No actions, no attacks.
    Tick 1: A0 shoots B0 (hit, dist=20), A1 shoots B0 (hit, dist=25)
            -> B0 dies. FOCUS FIRE: two A shots at same (tick, tgt).
    Tick 2: A0 shoots B1 (miss, dist=60, out of range).
    Tick 3 (last): A1 shoots B1 (hit, dist=15) -> B1 dies. A wins.

    Expected:
      outcome=TEAM_A_WIN, ticks=3
      shots_fired_us=4 (A), shots_fired_them=0
      shots_hit_us=3
      focus_fire_redundancy: tick 1 has 2 shots at (1,0) -> 1 extra /
        4 total attempts = 0.25
      cooldown_utilization_us: sum cooldown==0 / alive-cells. Drones
        are alive the whole match; cooldowns flip on successful hits.
        See explicit counting below.
      alive_final_us=2, alive_final_them=0
    """
    # Tick 0 snapshot: all alive, cooldown 0.
    t0 = {
        "schema_version": 2,
        "tick": 0,
        "team_a": [_drone(0, 0, 0), _drone(1, 10, 0)],
        "team_b": [_drone(0, 20, 0), _drone(1, 100, 0)],
        "actions_a": [_action(0, alive=True), _action(1, alive=True)],
        "actions_b": [_action(0, alive=True), _action(1, alive=True)],
        "attacks": [],
    }
    # Tick 1: A shoots; B0 dies; A drones now on cooldown 10.
    t1 = {
        "schema_version": 2,
        "tick": 1,
        "team_a": [_drone(0, 0, 0, cd=10), _drone(1, 10, 0, cd=10)],
        "team_b": [_drone(0, 20, 0, cd=0, alive=False), _drone(1, 100, 0)],
        "actions_a": [_action(0, target_id=0), _action(1, target_id=0)],
        "actions_b": [_action(0, target_id=-1), _action(1, target_id=-1)],
        "attacks": [
            _attack(0, 0, 1, 0, hit=True, dist=20.0),
            _attack(0, 1, 1, 0, hit=True, dist=25.0),
        ],
    }
    # Tick 2: A0 shoots out of range. Cooldowns decrement (10 -> 9). Miss.
    t2 = {
        "schema_version": 2,
        "tick": 2,
        "team_a": [_drone(0, 0, 0, cd=9), _drone(1, 10, 0, cd=9)],
        "team_b": [_drone(0, 20, 0, alive=False), _drone(1, 100, 0)],
        "actions_a": [_action(0, target_id=1), _action(1, target_id=-1)],
        "actions_b": [_action(0, target_id=-1), _action(1, target_id=-1)],
        # A0 has cooldown=9 > 0, so engine would NOT have recorded this
        # attempt (compute_attack_events skips cd>0). So attacks is empty.
        "attacks": [],
    }
    # Tick 3: A1 shoots B1 (hit, 15). Close to zero cooldown by now but
    # we keep the attempts consistent with compute_attack_events rules
    # (requires cd==0). Set A1 cd to 0 this tick.
    t3 = {
        "schema_version": 2,
        "tick": 3,
        "team_a": [_drone(0, 0, 0, cd=0), _drone(1, 85, 0, cd=10)],
        "team_b": [_drone(0, 20, 0, alive=False), _drone(1, 100, 0, alive=False)],
        "actions_a": [_action(0, target_id=-1), _action(1, target_id=1)],
        "actions_b": [_action(0, target_id=-1), _action(1, target_id=-1)],
        "attacks": [
            _attack(0, 1, 1, 1, hit=True, dist=15.0),
        ],
        "outcome": "TEAM_A_WIN",
    }
    return [t0, t1, t2, t3]


# ---------------------------------------------------------------------------
# Layer 1: handcrafted fixture with known metrics
# ---------------------------------------------------------------------------


def test_handcrafted_metrics_exact() -> None:
    records = _build_fixture()
    m = compute_metrics(records, "A")

    # Basic outcome/ticks.
    assert m["outcome"] == "TEAM_A_WIN"
    assert m["ticks"] == 3
    assert m["alive_final_us"] == 2
    assert m["alive_final_them"] == 0

    # Attack aggregates. us=Team A fired 3 recorded attempts (tick 1: 2;
    # tick 3: 1). All three hit. Tick-2 attempt is filtered by the engine
    # (attacker on cooldown) so no event was emitted — consistent with
    # the compute_attack_events contract.
    assert m["shots_fired_us"] == 3
    assert m["shots_fired_them"] == 0
    assert m["shots_hit_us"] == 3
    assert m["shots_hit_them"] == 0

    # Focus-fire: tick 1 has 2 A-shots at (tgt_team=1, tgt_id=0). That is
    # 1 "extra" shot beyond the first. Denominator = 3 total A attempts.
    # Expected: 1/3 = 0.3333
    assert abs(m["focus_fire_redundancy"] - 1 / 3) < 1e-4

    # Cooldown utilization for us: count alive-cells with cd==0.
    # Tick 0: 2 drones, both cd=0 -> 2/2.
    # Tick 1: 2 drones, both cd=10 -> 0/2.
    # Tick 2: 2 drones, both cd=9 -> 0/2.
    # Tick 3: 2 drones, A0 cd=0, A1 cd=10 -> 1/2.
    # Total: 3 ready cells / 8 alive cells = 0.375.
    assert abs(m["cooldown_utilization_us"] - 3 / 8) < 1e-4

    # Engagement range mean for hits: (20 + 25 + 15) / 3 = 20.0.
    assert abs(m["engagement_range_mean"] - 20.0) < 1e-4

    # Kiting score (them): them fired 0 shots -> defined as 0.
    assert m["kiting_score_them"] == 0.0


def test_handcrafted_perspective_b_flips() -> None:
    """Reporting from perspective B must flip us/them everywhere."""
    records = _build_fixture()
    m = compute_metrics(records, "B")
    assert m["outcome"] == "TEAM_A_WIN"  # outcome is absolute, not flipped
    assert m["alive_final_us"] == 0
    assert m["alive_final_them"] == 2
    assert m["shots_fired_us"] == 0
    assert m["shots_fired_them"] == 3


def test_load_v1_trace_rejected(tmp_path: Path) -> None:
    """Loading a v1 trace (no actions/attacks) must abort with a clear error."""
    v1 = tmp_path / "v1.jsonl"
    v1.write_text(
        '{"tick":0,"team_a":[{"id":0,"x":1,"y":2,"cooldown":0,"alive":true}],'
        '"team_b":[{"id":0,"x":3,"y":4,"cooldown":0,"alive":true}]}\n'
    )
    with pytest.raises(ValueError, match="v1 trace detected"):
        load_v2_trace(v1)


def test_markdown_contains_key_numbers() -> None:
    """Markdown must embed the metric numbers verbatim (no lossy summary)."""
    records = _build_fixture()
    m = compute_metrics(records, "A")
    md = render_markdown(m)
    # Outcome and tick count literal.
    assert "TEAM_A_WIN" in md
    assert "3 ticks" in md
    # Survivor counts.
    assert "us=2" in md
    assert "them=0" in md


# ---------------------------------------------------------------------------
# Layer 2: full baseline replay (requires compiler)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(CXX is None, reason="no C++17 compiler available")
def test_baseline_replay_invariants(tmp_path: Path) -> None:
    binary = build_matchup(tmp_path, "pursuit_v1.cpp", "cluster_v1.cpp")
    trace = tmp_path / "v2.jsonl"
    subprocess.run(
        [str(binary), "--seed", "42", "--record", str(trace), "--record-actions"],
        check=False,
        capture_output=True,
    )
    report = render_aar(trace, perspective="A")
    m = report.structured
    assert m["shots_hit_us"] <= m["shots_fired_us"]
    assert m["shots_hit_them"] <= m["shots_fired_them"]
    assert 0.0 <= m["cooldown_utilization_us"] <= 1.0
    assert 0.0 <= m["cooldown_utilization_them"] <= 1.0
    assert m["ticks"] >= 1
    assert report.token_estimate > 0


# ---------------------------------------------------------------------------
# Layer 3: determinism
# ---------------------------------------------------------------------------


def test_metrics_deterministic() -> None:
    records = _build_fixture()
    a = compute_metrics(records, "A")
    b = compute_metrics(records, "A")
    assert a == b
    # Stable JSON serialisation (sort_keys + no timestamps).
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_render_aar_stable(tmp_path: Path) -> None:
    """``render_aar`` on a file is idempotent."""
    # Dump the fixture to disk (as actual JSONL) and call render_aar twice.
    fx = tmp_path / "fx.jsonl"
    fx.write_text("\n".join(json.dumps(r) for r in _build_fixture()) + "\n")
    r1 = render_aar(fx, perspective="A", fmt="both")
    r2 = render_aar(fx, perspective="A", fmt="both")
    assert r1.structured == r2.structured
    assert r1.markdown == r2.markdown


def test_estimate_tokens_sane() -> None:
    assert estimate_tokens("") >= 1
    # "abcd" is 4 chars -> 1 token.
    assert estimate_tokens("abcd") == 1
    # "x" * 40 -> 10 tokens.
    assert estimate_tokens("x" * 40) == 10


def test_aar_report_is_frozen_dataclass() -> None:
    """AARReport is immutable; mutations must raise."""
    from dataclasses import FrozenInstanceError

    report = AARReport(structured={"a": 1}, markdown="m", token_estimate=1)
    with pytest.raises(FrozenInstanceError):
        report.markdown = "other"  # type: ignore[misc]
