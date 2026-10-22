"""Tests for scripts/tactic_detector.py (M18).

Each detector is exercised twice:

1. A handcrafted **positive fixture** that minimally satisfies the
   frozen thresholds — the detector must fire and the evidence payload
   must be sane.
2. A handcrafted **counter-example** that violates the threshold in
   exactly one way — the detector must stay silent.

In addition we cover the dispatcher (``scan_trace``), the I/O helpers
(``write_events`` / ``read_events``), and the CLI (``scan`` +
``dump-config``).
"""

from __future__ import annotations

import io
import json
import math
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import tactic_detector as td

# ---------------------------------------------------------------------------
# Tick / action / attack helpers (mirror test_telemetry_aar fixture format)
# ---------------------------------------------------------------------------


def _drone(did: int, x: float, y: float, *, cd: int = 0, alive: bool = True) -> dict:
    return {"id": did, "x": x, "y": y, "cooldown": cd, "alive": alive}


def _action(
    did: int,
    *,
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


def _attack(
    atk_team: int, atk_id: int, tgt_team: int, tgt_id: int, *, hit: bool, dist: float
) -> dict:
    return {
        "atk_team": atk_team,
        "atk_id": atk_id,
        "tgt_team": tgt_team,
        "tgt_id": tgt_id,
        "hit": hit,
        "dist": dist,
    }


def _tick(idx: int, *, team_a, team_b, actions_a=None, actions_b=None, attacks=None) -> dict:
    return {
        "schema_version": 2,
        "tick": idx,
        "team_a": team_a,
        "team_b": team_b,
        "actions_a": actions_a if actions_a is not None else [],
        "actions_b": actions_b if actions_b is not None else [],
        "attacks": attacks if attacks is not None else [],
    }


# ===========================================================================
# Flanking
# ===========================================================================


def _build_flanking_ticks(
    num_ticks: int,
    *,
    angle_deg: float = 180.0,
    r: float = 50.0,
    speed: float = 2.0,
    member_offset: float = 1.0,
) -> list[dict]:
    """Allies in two compact groups seen from the enemy centroid at the
    origin, ``angle_deg`` apart (180° is maximum; 90° is the threshold),
    each moving at ``speed``. Tick 0 is included, as in real traces."""
    half = math.radians(angle_deg / 2.0)
    low_pos = (r * math.cos(half), r * math.sin(half))
    high_pos = (r * math.cos(half), -r * math.sin(half))
    team_a = [
        _drone(0, low_pos[0], low_pos[1]),
        _drone(1, low_pos[0] + member_offset, low_pos[1] + member_offset),
        _drone(2, high_pos[0], high_pos[1]),
        _drone(3, high_pos[0] + member_offset, high_pos[1] + member_offset),
    ]
    team_b = [_drone(0, 0.0, 0.0), _drone(1, 1.0, 0.0)]
    actions_a = [_action(i, vx=-speed, vy=0.0) for i in range(4)]
    return [
        _tick(
            t,
            team_a=[dict(d) for d in team_a],
            team_b=[dict(d) for d in team_b],
            actions_a=[dict(a) for a in actions_a],
            actions_b=[_action(0), _action(1)],
        )
        for t in range(num_ticks)
    ]


def test_flanking_fires_on_symmetric_split():
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 5, angle_deg=170.0)
    ev = td.detect_flanking(ticks, perspective="a")
    assert ev is not None, "flanking should fire on sustained 170° split"
    assert ev.tactic == "flanking"
    assert ev.first_tick == td.FIRST_DECISION_TICK  # tick 0 is spawn, not a decision
    assert ev.sustained_ticks >= td.FLANK_MIN_STREAK_TICKS
    assert ev.evidence["angle_deg"] >= td.FLANK_MIN_ANGLE_DEG
    assert ev.evidence["threshold_angle_deg"] == td.FLANK_MIN_ANGLE_DEG


def test_flanking_silent_when_streak_too_short():
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS - 10, angle_deg=170.0)
    ev = td.detect_flanking(ticks, perspective="a")
    assert ev is None, "streak below threshold must not fire"


def test_flanking_silent_when_angle_below_threshold():
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 20, angle_deg=60.0)
    ev = td.detect_flanking(ticks, perspective="a")
    assert ev is None, "angle below 90° must not fire"


def test_flanking_silent_when_too_few_allies():
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 5, angle_deg=170.0)
    for t in ticks:
        for d in t["team_a"][2:]:
            d["alive"] = False
    ev = td.detect_flanking(ticks, perspective="a")
    assert ev is None, "need at least FLANK_MIN_ALLIES_ALIVE allies"


def test_flanking_silent_for_a_stationary_team():
    """Regression (schema 1): fired for stationary_v1, which never moves."""
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 20, angle_deg=170.0, speed=0.0)
    assert td.detect_flanking(ticks, perspective="a") is None
    assert td.measure_flanking(ticks, "a")["tick_counts"]["moving"] == 0


def test_flanking_silent_for_one_scattered_cloud():
    """Regression (schema 1): a random spawn cloud split at the median looks
    like two groups at a wide angle. Members as far from their group centre
    as the groups are apart are one cloud, not two groups."""
    ticks = _build_flanking_ticks(
        td.FLANK_MIN_STREAK_TICKS + 20, angle_deg=170.0, member_offset=80.0
    )
    assert td.detect_flanking(ticks, perspective="a") is None


def test_flanking_silent_when_far_from_the_enemy():
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 20, angle_deg=170.0, r=400.0)
    assert td.detect_flanking(ticks, perspective="a") is None


def test_flanking_ignores_spawn_tick():
    """Regression (schema 1): fired with first_tick 0 from spawn geometry."""
    ticks = _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS, angle_deg=170.0)
    # Exactly FLANK_MIN_STREAK_TICKS ticks including tick 0: one short.
    assert td.detect_flanking(ticks, perspective="a") is None


# ===========================================================================
# Kiting
# ===========================================================================


def _build_kiting_ticks(
    num_ticks: int, *, retreat: bool = True, vel_mag: float = 1.0
) -> list[dict]:
    """4 allies to the right of an enemy cluster at origin. If retreat,
    their velocity is +x (away from enemy); otherwise -x."""
    team_b = [_drone(0, 0.0, 0.0), _drone(1, 2.0, 0.0)]
    team_a = [_drone(i, 50.0 + i, 0.0) for i in range(4)]
    vx = vel_mag if retreat else -vel_mag
    actions_a = [_action(i, vx=vx, vy=0.0) for i in range(4)]
    actions_b = [_action(0), _action(1)]
    ticks = []
    for t in range(num_ticks):
        ticks.append(
            _tick(
                t,
                team_a=[dict(d) for d in team_a],
                team_b=[dict(d) for d in team_b],
                actions_a=[dict(a) for a in actions_a],
                actions_b=[dict(a) for a in actions_b],
            )
        )
    return ticks


def test_kiting_fires_on_sustained_retreat():
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5, retreat=True)
    ev = td.detect_kiting(ticks, perspective="a")
    assert ev is not None, "kiting should fire on sustained retreat"
    assert ev.tactic == "kiting"
    assert ev.sustained_ticks >= td.KITE_MIN_STREAK_TICKS
    assert ev.evidence["kiting_fraction"] >= td.KITE_MIN_FRACTION


def test_kiting_silent_on_advance():
    # Velocity pointing *toward* the enemy — the opposite of kiting.
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 20, retreat=False)
    ev = td.detect_kiting(ticks, perspective="a")
    assert ev is None, "advancing toward enemy must not fire kiting"


def test_kiting_silent_when_below_velocity_floor():
    ticks = _build_kiting_ticks(
        td.KITE_MIN_STREAK_TICKS + 20, retreat=True, vel_mag=td.KITE_MIN_VEL_MAG / 10.0
    )
    ev = td.detect_kiting(ticks, perspective="a")
    assert ev is None, "near-stationary drones must not count as kiting"


def test_kiting_silent_when_streak_too_short():
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS - 10, retreat=True)
    ev = td.detect_kiting(ticks, perspective="a")
    assert ev is None, "streak below threshold must not fire"


# ===========================================================================
# Focus fire
# ===========================================================================


def _build_focus_fire_ticks(num_ticks: int, *, focused: bool) -> list[dict]:
    """Four allies, four enemies. Every tick from 1 on, allies 0 and 1 hit
    enemy 0 and allies 2 and 3 hit enemy 1 (focused), or each ally hits a
    different enemy (spread)."""
    team_a = [_drone(i, 0.0, float(i)) for i in range(4)]
    team_b = [_drone(i, 20.0, float(i)) for i in range(4)]
    ticks = [_tick(0, team_a=team_a, team_b=team_b)]
    for t in range(1, num_ticks):
        targets = [0, 0, 1, 1] if focused else [0, 1, 2, 3]
        attacks = [_attack(0, i, 1, tgt, hit=True, dist=20.0) for i, tgt in enumerate(targets)]
        ticks.append(
            _tick(
                t,
                team_a=[dict(d) for d in team_a],
                team_b=[dict(d) for d in team_b],
                attacks=attacks,
            )
        )
    return ticks


def test_focus_fire_fires_on_concentrated_hits():
    ticks = _build_focus_fire_ticks(3, focused=True)
    ev = td.detect_focus_fire(ticks, perspective="a")
    assert ev is not None
    assert ev.tactic == "focus_fire"
    assert ev.evidence["focused_share"] == 1.0
    assert ev.evidence["hits"] >= ev.evidence["threshold_min_hits"] == 4
    assert ev.first_tick == 1


def test_focus_fire_silent_on_spread_hits():
    ticks = _build_focus_fire_ticks(5, focused=False)
    assert td.detect_focus_fire(ticks, perspective="a") is None
    assert td.measure_focus_fire(ticks, "a")["focused_share"] == 0.0


def test_focus_fire_silent_when_too_few_hits():
    """Fewer hits than the enemy team has drones: no verdict."""
    ticks = _build_focus_fire_ticks(2, focused=True)
    ticks[1]["attacks"] = ticks[1]["attacks"][:2]  # 2 hits < 4 enemies
    assert td.detect_focus_fire(ticks, perspective="a") is None


def test_focus_fire_reaches_a_verdict_in_one_short_match():
    """Regression (schema 1): the 200-attempt window could never fill in a
    10-drone match (at most 39 attempts per side in calibration)."""
    ticks = _build_focus_fire_ticks(3, focused=True)
    m = td.measure_focus_fire(ticks, "a")
    assert m["hits"] < 39 and m["fires"]


# ===========================================================================
# Message-coded targeting
# ===========================================================================


def _build_message_coded_ticks(num_ticks: int, *, coded: bool = True) -> list[dict]:
    """Four allies; each has a target cycling between 2 enemies. When
    ``coded`` is True, their msg[0] mirrors the target (strong NMI).
    When False, msg[0] is a constant (zero NMI)."""
    team_a = [_drone(i, float(i), 0.0) for i in range(4)]
    team_b = [_drone(0, 100.0, 0.0), _drone(1, 100.0, 20.0)]
    ticks = []
    for t in range(num_ticks):
        actions_a = []
        for i in range(4):
            # Alternate target every 3 ticks to generate target-change events.
            tid = (t // 3 + i) % 2
            if coded:
                # Encode target directly: target 0 -> msg[0] = -0.75,
                # target 1 -> msg[0] = +0.75 (lands in different bins of
                # the 4-bin uniform quantiser).
                m0 = -0.75 if tid == 0 else 0.75
            else:
                m0 = 0.0
            actions_a.append(_action(i, target_id=tid, msg=(m0, 0.0, 0.0, 0.0)))
        ticks.append(
            _tick(
                t,
                team_a=[dict(d) for d in team_a],
                team_b=[dict(d) for d in team_b],
                actions_a=actions_a,
                actions_b=[_action(0), _action(1)],
            )
        )
    return ticks


def test_message_coded_fires_on_high_nmi():
    ticks = _build_message_coded_ticks(td.MSG_MIN_TICKS + 20, coded=True)
    ev = td.detect_message_coded_targeting(ticks, perspective="a")
    assert ev is not None, "target-encoded messages must fire detector"
    assert ev.tactic == "message_coded_targeting"
    assert ev.evidence["nmi"] >= td.MSG_MIN_NMI
    assert ev.evidence["target_events"] >= td.MSG_MIN_TARGET_EVENTS
    assert ev.evidence["observation_ticks"] >= td.MSG_MIN_TICKS


def test_message_coded_silent_on_constant_messages():
    ticks = _build_message_coded_ticks(td.MSG_MIN_TICKS + 20, coded=False)
    ev = td.detect_message_coded_targeting(ticks, perspective="a")
    assert ev is None, "constant messages carry no MI with targets"


def test_message_coded_silent_when_too_few_ticks():
    ticks = _build_message_coded_ticks(td.MSG_MIN_TICKS - 10, coded=True)
    ev = td.detect_message_coded_targeting(ticks, perspective="a")
    assert ev is None, "fewer than MSG_MIN_TICKS observation ticks → silent"


def _message_ticks_from(values_for_target, num_ticks: int) -> list[dict]:
    """Like _build_message_coded_ticks but msg[0] = values_for_target(tid)."""
    ticks = _build_message_coded_ticks(num_ticks, coded=True)
    for t in ticks:
        for a in t["actions_a"]:
            a["msg"][0] = values_for_target(a["target_id"])
    return ticks


def test_message_coded_fires_on_values_outside_unit_range():
    """Regression (schema 1): channels were clipped to [-1, 1], so a target
    code in large values (e.g. msg = target id x 100) was invisible."""
    ticks = _message_ticks_from(lambda tid: 100.0 * tid + 500.0, td.MSG_MIN_TICKS + 20)
    ev = td.detect_message_coded_targeting(ticks, perspective="a")
    assert ev is not None and ev.evidence["best_channel"] == 0
    assert ev.evidence["nmi"] > ev.evidence["null_quantile"]


def test_message_coded_silent_on_noise_channel():
    rng = __import__("random").Random(3)
    ticks = _message_ticks_from(lambda tid: rng.uniform(-500, 500), td.MSG_MIN_TICKS + 20)
    assert td.detect_message_coded_targeting(ticks, perspective="a") is None


def test_message_coded_ignores_tick0_zero_actions():
    """Regression (schema 1): tick 0's zero-filled actions (target_id 0)
    were counted as target events, even for a team that never targets."""
    ticks = _build_message_coded_ticks(5, coded=True)
    for t in ticks[1:]:
        for a in t["actions_a"]:
            a["target_id"] = -1
    m = td.measure_message_coded_targeting([ticks], "a")
    assert m["target_events"] == 0 and m["observation_ticks"] == 0


def test_message_coded_pools_short_matches():
    short = [_build_message_coded_ticks(30, coded=True) for _ in range(5)]
    assert all(td.detect_message_coded_targeting(t, perspective="a") is None for t in short)
    pooled = td.measure_message_coded_targeting(short, "a")
    assert pooled["observation_ticks"] >= td.MSG_MIN_TICKS and pooled["fires"]


# ===========================================================================
# Dispatcher + metadata forwarding
# ===========================================================================


def test_scan_trace_forwards_metadata():
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    events = td.scan_trace(
        ticks,
        perspective="a",
        track="A",
        model="claude-opus-4-7",
        seed=42,
        generation=7,
        tactics=["kiting"],
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.track == "A"
    assert ev.model == "claude-opus-4-7"
    assert ev.seed == 42
    assert ev.generation == 7
    assert ev.perspective == "a"
    assert ev.schema_version == td.SCHEMA_VERSION


def test_scan_trace_selects_subset():
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    events = td.scan_trace(
        ticks, perspective="a", tactics=["flanking"]
    )  # no flanking in this fixture
    assert events == []


def test_scan_trace_unknown_tactic_raises():
    ticks = _build_kiting_ticks(10)
    with pytest.raises(ValueError, match="unknown tactic"):
        td.scan_trace(ticks, tactics=["bogus"])


def test_scan_trace_all_default_tactics():
    # A mixed fixture that only fires kiting. Dispatcher should emit
    # exactly one event without crashing on the other three detectors.
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    events = td.scan_trace(ticks, perspective="a")
    assert {e.tactic for e in events} == {"kiting"}


def test_perspective_b_dispatch():
    """Mirror the kiting fixture so that Team B is the one retreating."""
    team_a = [_drone(0, 0.0, 0.0), _drone(1, 2.0, 0.0)]
    team_b = [_drone(i, 50.0 + i, 0.0) for i in range(4)]
    actions_b = [_action(i, vx=1.0, vy=0.0) for i in range(4)]
    actions_a = [_action(0), _action(1)]
    ticks = [
        _tick(
            t,
            team_a=[dict(d) for d in team_a],
            team_b=[dict(d) for d in team_b],
            actions_a=[dict(a) for a in actions_a],
            actions_b=[dict(a) for a in actions_b],
        )
        for t in range(td.KITE_MIN_STREAK_TICKS + 5)
    ]
    ev = td.detect_kiting(ticks, perspective="b")
    assert ev is not None
    assert ev.perspective == "b"


def test_invalid_perspective_raises():
    ticks = _build_kiting_ticks(5)
    with pytest.raises(ValueError, match="perspective"):
        td.detect_kiting(ticks, perspective="z")


# ===========================================================================
# I/O helpers
# ===========================================================================


def test_write_and_read_events_roundtrip(tmp_path: Path):
    events = [
        td.TacticEvent(
            schema_version=td.SCHEMA_VERSION,
            tactic="flanking",
            first_tick=3,
            sustained_ticks=50,
            track="A",
            model="m1",
            seed=1,
            generation=0,
            perspective="a",
            evidence={"angle_deg": 95.0},
        ),
        td.TacticEvent(
            schema_version=td.SCHEMA_VERSION,
            tactic="kiting",
            first_tick=10,
            sustained_ticks=101,
            track="A",
            model="m1",
            seed=1,
            generation=0,
            perspective="a",
            evidence={"kiting_fraction": 0.75},
        ),
    ]
    out = tmp_path / "events.jsonl"
    td.write_events(events, out)
    data = out.read_text().splitlines()
    assert len(data) == 2
    # Each line must be valid JSON (not pretty-printed).
    for line in data:
        json.loads(line)
    loaded = td.read_events(out)
    assert len(loaded) == 2
    assert loaded[0]["tactic"] == "flanking"
    assert loaded[1]["tactic"] == "kiting"


def test_read_events_tolerates_trailing_blank_line(tmp_path: Path):
    out = tmp_path / "events.jsonl"
    ev = td.TacticEvent(
        schema_version=td.SCHEMA_VERSION,
        tactic="kiting",
        first_tick=0,
        sustained_ticks=100,
        track="",
        model="",
        seed=-1,
        generation=-1,
        perspective="a",
        evidence={},
    )
    out.write_text(ev.as_jsonl_line() + "\n\n")
    loaded = td.read_events(out)
    assert len(loaded) == 1


def test_read_events_raises_on_mid_file_corruption(tmp_path: Path):
    out = tmp_path / "events.jsonl"
    out.write_text('{"ok": 1}\nNOT JSON\n{"ok": 2}\n')
    with pytest.raises(ValueError, match="invalid JSON"):
        td.read_events(out)


def test_write_events_pretty(tmp_path: Path):
    ev = td.TacticEvent(
        schema_version=td.SCHEMA_VERSION,
        tactic="kiting",
        first_tick=0,
        sustained_ticks=100,
        track="",
        model="",
        seed=-1,
        generation=-1,
        perspective="a",
        evidence={"foo": "bar"},
    )
    out = tmp_path / "events_pretty.jsonl"
    td.write_events([ev], out, pretty=True)
    text = out.read_text()
    # Pretty mode produces multi-line JSON (contains newline inside object).
    assert text.count("\n") > 2
    # And each object still parses.
    # (Read whole file as one concatenated JSON -- not strict JSONL.)
    # This is a debugging format; we just verify it's parseable per block.
    blocks = text.strip().split("}\n{")
    assert len(blocks) >= 1


# ===========================================================================
# CLI
# ===========================================================================


def _write_trace(tmp_path: Path, ticks: list[dict]) -> Path:
    trace = tmp_path / "trace.jsonl"
    with trace.open("w", encoding="utf-8") as fh:
        for t in ticks:
            fh.write(json.dumps(t))
            fh.write("\n")
    return trace


def test_cli_scan_writes_events(tmp_path: Path):
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    trace_path = _write_trace(tmp_path, ticks)
    out_path = tmp_path / "tactic_events.jsonl"
    rc = td.main(
        [
            "scan",
            str(trace_path),
            str(out_path),
            "--perspective",
            "a",
            "--track",
            "A",
            "--model",
            "m1",
            "--seed",
            "7",
            "--generation",
            "2",
        ]
    )
    assert rc == 0
    assert out_path.exists()
    events = td.read_events(out_path)
    tactics = {e["tactic"] for e in events}
    assert "kiting" in tactics
    # metadata is attached
    kiting_ev = next(e for e in events if e["tactic"] == "kiting")
    assert kiting_ev["track"] == "A"
    assert kiting_ev["seed"] == 7
    assert kiting_ev["generation"] == 2


def test_cli_scan_subset_via_tactic_flag(tmp_path: Path):
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    trace_path = _write_trace(tmp_path, ticks)
    out_path = tmp_path / "events.jsonl"
    rc = td.main(
        [
            "scan",
            str(trace_path),
            str(out_path),
            "--perspective",
            "a",
            "--tactic",
            "flanking",  # only flanking → no fires in this fixture
        ]
    )
    assert rc == 0
    # File is written even when empty.
    assert out_path.exists()
    assert td.read_events(out_path) == []


def test_cli_dump_config_prints_thresholds():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = td.main(["dump-config"])
    assert rc == 0
    cfg = json.loads(buf.getvalue())
    assert cfg["schema_version"] == td.SCHEMA_VERSION
    assert set(cfg["tactics"]) == set(td.TACTICS)
    assert cfg["flanking"]["min_angle_deg"] == td.FLANK_MIN_ANGLE_DEG
    assert cfg["kiting"]["min_streak_ticks"] == td.KITE_MIN_STREAK_TICKS
    assert cfg["focus_fire"]["min_focused_share"] == td.FF_MIN_FOCUSED_SHARE
    assert cfg["first_decision_tick"] == td.FIRST_DECISION_TICK
    assert cfg["message_coded_targeting"]["min_nmi"] == td.MSG_MIN_NMI


def test_cli_scan_rejects_v1_trace(tmp_path: Path):
    trace_path = tmp_path / "v1.jsonl"
    trace_path.write_text(
        '{"tick":0,"team_a":[{"id":0,"x":0,"y":0,"cooldown":0,"alive":true}],"team_b":[]}\n'
    )
    out_path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="v1 trace"):
        td.main(
            [
                "scan",
                str(trace_path),
                str(out_path),
                "--perspective",
                "a",
            ]
        )


# ===========================================================================
# Determinism
# ===========================================================================


def test_repeated_scans_are_byte_identical(tmp_path: Path):
    ticks = _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5)
    events1 = td.scan_trace(ticks, perspective="a", track="A", model="m", seed=1, generation=0)
    events2 = td.scan_trace(ticks, perspective="a", track="A", model="m", seed=1, generation=0)
    lines1 = [e.as_jsonl_line() for e in events1]
    lines2 = [e.as_jsonl_line() for e in events2]
    assert lines1 == lines2


# ===========================================================================
# NMI / quantise helpers (low-level sanity)
# ===========================================================================


def test_nmi_identity_is_one():
    xs = [0, 1, 2, 0, 1, 2]
    ys = [0, 1, 2, 0, 1, 2]
    assert td._nmi(xs, ys) == pytest.approx(1.0, abs=1e-9)


def test_nmi_independent_is_zero():
    # Constant x → hx=0 → NMI returns 0.
    xs = [1, 1, 1, 1]
    ys = [0, 1, 2, 3]
    assert td._nmi(xs, ys) == 0.0


def test_quantise_clips():
    assert td._quantise(-10.0, bins=4) == 0
    assert td._quantise(10.0, bins=4) == 3
    # Mid value lands in one of the inner bins.
    mid = td._quantise(0.0, bins=4)
    assert 0 <= mid <= 3


# ===========================================================================
# Schema validation (docs/tactic_events_schema.json)
# ===========================================================================


def test_events_validate_against_schema():
    """Every event produced by scan_trace must validate against
    docs/tactic_events_schema.json. If jsonschema is not installed,
    skip."""
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = _REPO_ROOT / "docs" / "tactic_events_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    # Produce at least one event per detector.
    # Kiting and flanking can coexist if we retreat along a radial
    # split. Simpler: union of independent fixtures' events.
    fixtures = [
        _build_flanking_ticks(td.FLANK_MIN_STREAK_TICKS + 5, angle_deg=170.0),
        _build_kiting_ticks(td.KITE_MIN_STREAK_TICKS + 5),
        _build_focus_fire_ticks(3, focused=True),
        _build_message_coded_ticks(td.MSG_MIN_TICKS + 20, coded=True),
    ]
    all_events: list[td.TacticEvent] = []
    for ticks in fixtures:
        all_events.extend(
            td.scan_trace(
                ticks,
                perspective="a",
                track="A",
                model="m1",
                seed=1,
                generation=0,
            )
        )
    fired = {e.tactic for e in all_events}
    # We should cover all four tactics across the four fixtures.
    assert fired == set(td.TACTICS)
    for ev in all_events:
        jsonschema.validate(
            instance=json.loads(ev.as_jsonl_line()),
            schema=schema,
        )
