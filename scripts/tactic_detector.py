#!/usr/bin/env python3
"""Tactic Detector (M18; schema 2 after the v2 calibration).

Deterministic feature extractor over v2 traces (see M15a). Fires one
first-appearance event per (track, model, seed, generation) per tactic,
written to ``tactic_events.jsonl`` (one record per line). Each detector
has a ``measure_*`` function that returns its underlying quantities
whether or not it fires; ``detect_*`` wraps it into a ``TacticEvent``.

Every detector ignores tick 0: it holds the spawn positions and
zero-filled actions (``target_id`` 0 for every drone), not decisions.

Detected tactics (frozen thresholds; any change bumps ``SCHEMA_VERSION``):

Flanking
    Two distinct ally groups closing on the enemy from sides at least 90°
    apart, sustained >= 50 ticks. Per tick: alive allies are split at the
    median projection on the axis perpendicular to (enemy centroid ->
    ally centroid); the angle between the two group centroids seen from
    the enemy centroid is >= 90°; each group's mean distance to its own
    centroid is <= 0.5 x the distance between the group centroids (two
    groups, not one scattered cloud); each group centroid is within 250
    units of the enemy centroid; and the allies' mean speed is >= 1.0
    (the team is manoeuvring).

Kiting
    Sustained retreat velocity aligned with the (enemy centroid -> ally)
    vector. A drone is "kiting" this tick when its velocity has cosine
    similarity >= cos(30°) with ``(ally - enemy_centroid)``. A tick is a
    "kite tick" when at least 50% of moving allies satisfy this. Fires on
    >= 100 consecutive kite ticks.

Focus fire
    Concentrated fire: a hit is *focused* when at least one other ally
    hits the same target in the same tick. Fires when at least half of the
    team's hits are focused, over at least as many hits as the enemy team
    had drones.

Message-coded targeting
    Normalised mutual information (NMI) between a drone's ``target_id``
    and one channel of its outgoing message, over the ticks where it has
    a target. A channel with at most 16 distinct values is used as
    categories; otherwise it is cut into 4 equal-width bins over its
    observed range (no clipping). Fires when the best channel's NMI is
    >= 0.25 and above the 99th percentile of the same statistic with the
    targets permuted (200 permutations), on >= 20 target-change events
    and >= 100 observation ticks. ``measure_message_coded_targeting``
    accepts several traces and pools them, since single matches are too
    short to reach the minimums.

Schema history: 1 = M18 (flanking without the group, range and speed
conditions; ``focus_fire_discipline`` = few shots at dead targets over a
200-attempt window; message channels clipped to [-1, 1]; tick 0
included). Calibration showed schema 1 firing flanking from spawn
geometry and for a stationary team, and the other two unable to fire
(reports/calibration/CALIBRATION.md).

CLI
---

    tactic_detector.py scan <trace.jsonl> <out.jsonl> \
        --track A --model claude-opus-4-7 --seed 42 --generation 3 \
        [--perspective a|b] [--pretty]
    tactic_detector.py dump-config      # prints the frozen thresholds

Acceptance: each detector must fire on a handcrafted positive fixture
and stay silent on handcrafted counter-examples. See
``tests/test_tactic_detector.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --- local imports: scripts/ on sys.path -----------------------------------
_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import telemetry_aar  # noqa: E402

# ---------------------------------------------------------------------------
# Constants (frozen; bump SCHEMA_VERSION on change)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2

#: All tactic names in canonical order.
TACTICS: tuple[str, ...] = (
    "flanking",
    "kiting",
    "focus_fire",
    "message_coded_targeting",
)

#: Tick 0 holds spawn positions and zero-filled actions, not decisions.
FIRST_DECISION_TICK: int = 1

# --- Flanking thresholds
FLANK_MIN_ANGLE_DEG: float = 90.0
FLANK_MIN_STREAK_TICKS: int = 50
FLANK_MIN_ALLIES_ALIVE: int = 3  # need >=3 so the two clusters both have >=1
FLANK_MAX_SPREAD_RATIO: float = 0.5  # group spread / distance between groups
FLANK_MAX_RANGE: float = 250.0  # group centroid to enemy centroid
FLANK_MIN_MEAN_SPEED: float = 1.0  # units per tick (max velocity is 5)

# --- Kiting thresholds
KITE_COS_MIN: float = math.cos(math.radians(30.0))  # ~0.866
KITE_MIN_FRACTION: float = 0.5
KITE_MIN_STREAK_TICKS: int = 100
KITE_MIN_VEL_MAG: float = 0.05  # ignore near-stationary drones

# --- Focus-fire thresholds
FF_MIN_FOCUSED_SHARE: float = 0.5
FF_MIN_HITS_PER_ENEMY: float = 1.0  # minimum hits = this x enemy team size

# --- Message-coded targeting thresholds
MSG_MIN_NMI: float = 0.25
MSG_MIN_TARGET_EVENTS: int = 20
MSG_MIN_TICKS: int = 100
MSG_CHANNEL_BINS: int = 4
MSG_MAX_CATEGORIES: int = 16
MSG_PERMUTATIONS: int = 200
MSG_PERM_QUANTILE: float = 0.99
MSG_SIZE: int = 4  # matches engine MSG_SIZE

_LOG = logging.getLogger("swarmevolve.tactic_detector")


# ---------------------------------------------------------------------------
# Output record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TacticEvent:
    """One first-appearance record emitted by a detector."""

    schema_version: int
    tactic: str
    first_tick: int
    sustained_ticks: int
    track: str
    model: str
    seed: int
    generation: int
    perspective: str  # "a" or "b"
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_jsonl_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


# ---------------------------------------------------------------------------
# Small math utilities (no numpy dep to match the rest of the codebase)
# ---------------------------------------------------------------------------


def _alive(drones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in drones if d.get("alive")]


def _centroid(drones: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not drones:
        return None
    cx = sum(d["x"] for d in drones) / len(drones)
    cy = sum(d["y"] for d in drones) / len(drones)
    return cx, cy


def _norm(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _angle_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Angle in degrees between two vectors (both assumed non-zero).
    Returns 0 for the degenerate case."""
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    c = _dot(a, b) / (na * nb)
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


# ---------------------------------------------------------------------------
# Flanking detector
# ---------------------------------------------------------------------------


def _split_allies_by_median_projection(
    allies: list[dict[str, Any]],
    enemy_centroid: tuple[float, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic 2-cluster split of ``allies`` relative to the enemy
    centroid. Projects each ally onto an axis perpendicular to the
    (ally_centroid → enemy_centroid) vector, then splits at the median
    projection. Returns (low, high).
    """
    ally_centroid = _centroid(allies)
    assert ally_centroid is not None
    ex, ey = enemy_centroid
    ax, ay = ally_centroid
    # Radial direction (from enemy to ally_centroid).
    rx, ry = ax - ex, ay - ey
    rn = math.sqrt(rx * rx + ry * ry)
    if rn == 0.0:
        # Degenerate: fall back to the x-axis.
        rx, ry, rn = 1.0, 0.0, 1.0
    rx, ry = rx / rn, ry / rn
    # Perpendicular axis (rotate 90° CCW).
    px, py = -ry, rx
    projected = [(px * (d["x"] - ex) + py * (d["y"] - ey), d) for d in allies]
    # Sort by projection then by drone id for stability.
    projected.sort(key=lambda t: (t[0], t[1]["id"]))
    half = len(projected) // 2
    low = [t[1] for t in projected[:half]]
    high = [t[1] for t in projected[half:]]
    return low, high


def _decisions(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in ticks if int(t["tick"]) >= FIRST_DECISION_TICK]


def _event(
    tactic: str, perspective: str, first_tick: int, sustained: int, evidence: dict
) -> TacticEvent:
    return TacticEvent(
        schema_version=SCHEMA_VERSION,
        tactic=tactic,
        first_tick=first_tick,
        sustained_ticks=sustained,
        track="",
        model="",
        seed=-1,
        generation=-1,
        perspective=perspective,
        evidence=evidence,
    )


def _mean_dist(drones: list[dict[str, Any]], c: tuple[float, float]) -> float:
    return sum(math.hypot(d["x"] - c[0], d["y"] - c[1]) for d in drones) / len(drones)


def measure_flanking(ticks: list[dict[str, Any]], perspective: str) -> dict[str, Any]:
    """Per-tick flank conditions, the longest streak meeting all of them,
    and the first streak that reaches FLANK_MIN_STREAK_TICKS."""
    us_key, them_key = _perspective_teams(perspective)
    act_key = "actions_a" if perspective == "a" else "actions_b"
    counts = {"eligible": 0, "angle": 0, "groups": 0, "range": 0, "moving": 0, "all": 0}
    streak = best = 0
    start: int | None = None
    fired: dict[str, Any] | None = None
    for t in _decisions(ticks):
        allies, enemies = _alive(t[us_key]), _alive(t[them_key])
        ok = False
        if len(allies) >= FLANK_MIN_ALLIES_ALIVE and enemies:
            ec = _centroid(enemies)
            assert ec is not None
            low, high = _split_allies_by_median_projection(allies, ec)
            if low and high:
                counts["eligible"] += 1
                cl, ch = _centroid(low), _centroid(high)
                assert cl is not None and ch is not None
                angle = _angle_between(
                    (cl[0] - ec[0], cl[1] - ec[1]), (ch[0] - ec[0], ch[1] - ec[1])
                )
                gap = math.hypot(cl[0] - ch[0], cl[1] - ch[1])
                spread = max(_mean_dist(low, cl), _mean_dist(high, ch))
                reach = max(
                    math.hypot(cl[0] - ec[0], cl[1] - ec[1]),
                    math.hypot(ch[0] - ec[0], ch[1] - ec[1]),
                )
                actions = {a["id"]: a for a in t.get(act_key, [])}
                speeds = [
                    math.hypot(actions[d["id"]]["vx"], actions[d["id"]]["vy"])
                    if d["id"] in actions
                    else 0.0
                    for d in allies
                ]
                conds = {
                    "angle": angle >= FLANK_MIN_ANGLE_DEG,
                    "groups": gap > 0 and spread <= FLANK_MAX_SPREAD_RATIO * gap,
                    "range": reach <= FLANK_MAX_RANGE,
                    "moving": sum(speeds) / len(speeds) >= FLANK_MIN_MEAN_SPEED,
                }
                for k, v in conds.items():
                    counts[k] += int(v)
                ok = all(conds.values())
                if ok:
                    counts["all"] += 1
        if ok:
            if streak == 0:
                start = int(t["tick"])
            streak += 1
            best = max(best, streak)
            if fired is None and streak >= FLANK_MIN_STREAK_TICKS and start is not None:
                fired = {
                    "first_tick": start,
                    "angle_deg": round(angle, 2),
                    "cluster_sizes": [len(low), len(high)],
                    "spread_ratio": round(spread / gap, 3),
                    "max_range": round(reach, 1),
                }
        else:
            streak = 0
    return {
        "tick_counts": counts,
        "longest_streak": best,
        "fires": fired is not None,
        "event": fired,
    }


def detect_flanking(ticks: list[dict[str, Any]], perspective: str) -> TacticEvent | None:
    m = measure_flanking(ticks, perspective)
    if not m["fires"]:
        return None
    ev = dict(m["event"])
    first = ev.pop("first_tick")
    ev.update(
        threshold_angle_deg=FLANK_MIN_ANGLE_DEG,
        threshold_streak_ticks=FLANK_MIN_STREAK_TICKS,
        threshold_spread_ratio=FLANK_MAX_SPREAD_RATIO,
        threshold_range=FLANK_MAX_RANGE,
        threshold_mean_speed=FLANK_MIN_MEAN_SPEED,
    )
    return _event("flanking", perspective, first, FLANK_MIN_STREAK_TICKS, ev)


# ---------------------------------------------------------------------------
# Kiting detector
# ---------------------------------------------------------------------------


def measure_kiting(ticks: list[dict[str, Any]], perspective: str) -> dict[str, Any]:
    """Kite fraction per tick, longest streak of kite ticks, first streak
    that reaches KITE_MIN_STREAK_TICKS."""
    us_key, them_key = _perspective_teams(perspective)
    act_key = "actions_a" if perspective == "a" else "actions_b"
    streak = best = 0
    start: int | None = None
    best_frac = 0.0
    fired: dict[str, Any] | None = None
    for t in _decisions(ticks):
        allies, enemies = _alive(t[us_key]), _alive(t[them_key])
        actions = {a["id"]: a for a in t.get(act_key, [])}
        frac: float | None = None
        if allies and enemies and actions:
            ec = _centroid(enemies)
            assert ec is not None
            considered = kiting = 0
            for d in allies:
                a = actions.get(d["id"])
                if a is None or not a.get("alive"):
                    continue
                v = (float(a["vx"]), float(a["vy"]))
                if _norm(v) < KITE_MIN_VEL_MAG:
                    continue
                considered += 1
                radial = (d["x"] - ec[0], d["y"] - ec[1])
                if _norm(radial) == 0.0:
                    continue
                if _dot(v, radial) / (_norm(v) * _norm(radial)) >= KITE_COS_MIN:
                    kiting += 1
            if considered:
                frac = kiting / considered
        if frac is not None and frac >= KITE_MIN_FRACTION:
            if streak == 0:
                start = int(t["tick"])
            streak += 1
            best = max(best, streak)
            best_frac = max(best_frac, frac)
            if fired is None and streak >= KITE_MIN_STREAK_TICKS and start is not None:
                fired = {
                    "first_tick": start,
                    "kiting_fraction": round(frac, 3),
                    "max_kiting_fraction": round(best_frac, 3),
                }
        else:
            streak = 0
    return {"longest_streak": best, "fires": fired is not None, "event": fired}


def detect_kiting(ticks: list[dict[str, Any]], perspective: str) -> TacticEvent | None:
    m = measure_kiting(ticks, perspective)
    if not m["fires"]:
        return None
    ev = dict(m["event"])
    first = ev.pop("first_tick")
    ev.update(
        threshold_fraction=KITE_MIN_FRACTION,
        threshold_streak_ticks=KITE_MIN_STREAK_TICKS,
        cos_min=round(KITE_COS_MIN, 4),
    )
    return _event("kiting", perspective, first, KITE_MIN_STREAK_TICKS, ev)


# ---------------------------------------------------------------------------
# Focus-fire detector
# ---------------------------------------------------------------------------


def measure_focus_fire(ticks: list[dict[str, Any]], perspective: str) -> dict[str, Any]:
    """Share of the team's hits that land on a target another ally hits in
    the same tick."""
    us_team_id = 0 if perspective == "a" else 1
    _, them_key = _perspective_teams(perspective)
    enemy_team_size = len(ticks[0][them_key]) if ticks else 0
    hits = focused = 0
    first_focused: int | None = None
    for t in _decisions(ticks):
        per_target: dict[int, int] = {}
        for atk in t.get("attacks", []):
            if atk["atk_team"] == us_team_id and atk["hit"]:
                per_target[atk["tgt_id"]] = per_target.get(atk["tgt_id"], 0) + 1
        for n in per_target.values():
            hits += n
            if n >= 2:
                focused += n
                if first_focused is None:
                    first_focused = int(t["tick"])
    min_hits = math.ceil(FF_MIN_HITS_PER_ENEMY * enemy_team_size)
    share = focused / hits if hits else 0.0
    return {
        "hits": hits,
        "focused_hits": focused,
        "focused_share": round(share, 4),
        "min_hits": min_hits,
        "first_focused_tick": first_focused,
        "fires": hits >= max(1, min_hits) and share >= FF_MIN_FOCUSED_SHARE,
    }


def detect_focus_fire(ticks: list[dict[str, Any]], perspective: str) -> TacticEvent | None:
    m = measure_focus_fire(ticks, perspective)
    if not m["fires"]:
        return None
    return _event(
        "focus_fire",
        perspective,
        int(m["first_focused_tick"]),
        int(m["hits"]),
        {
            "focused_share": m["focused_share"],
            "hits": m["hits"],
            "focused_hits": m["focused_hits"],
            "threshold_focused_share": FF_MIN_FOCUSED_SHARE,
            "threshold_min_hits": m["min_hits"],
        },
    )


# ---------------------------------------------------------------------------
# Message-coded targeting detector
# ---------------------------------------------------------------------------


def _quantise(x: float, bins: int, lo: float = -1.0, hi: float = 1.0) -> int:
    """Uniform bucket in [lo, hi] → {0, .., bins-1}. Out-of-range clips."""
    if x <= lo:
        return 0
    if x >= hi:
        return bins - 1
    span = hi - lo
    return min(bins - 1, max(0, int((x - lo) / span * bins)))


def _nmi(xs: list[int], ys: list[int]) -> float:
    """Normalised mutual information (arithmetic-mean normalisation).
    Returns 0.0 on degenerate single-category inputs. Expects
    non-empty, equal-length integer sequences."""
    assert len(xs) == len(ys)
    n = len(xs)
    if n == 0:
        return 0.0
    px: dict[int, int] = {}
    py: dict[int, int] = {}
    pxy: dict[tuple[int, int], int] = {}
    for x, y in zip(xs, ys, strict=False):
        px[x] = px.get(x, 0) + 1
        py[y] = py.get(y, 0) + 1
        pxy[(x, y)] = pxy.get((x, y), 0) + 1
    hx = -sum((c / n) * math.log2(c / n) for c in px.values() if c > 0)
    hy = -sum((c / n) * math.log2(c / n) for c in py.values() if c > 0)
    if hx == 0.0 or hy == 0.0:
        return 0.0
    mi = 0.0
    for (x, y), c in pxy.items():
        pxy_val = c / n
        pxv = px[x] / n
        pyv = py[y] / n
        if pxy_val > 0 and pxv > 0 and pyv > 0:
            mi += pxy_val * math.log2(pxy_val / (pxv * pyv))
    return 2.0 * mi / (hx + hy)


def _channel_codes(values: list[float]) -> list[int]:
    """Categories when the channel takes few distinct values, otherwise
    MSG_CHANNEL_BINS equal-width bins over its observed range."""
    distinct = sorted(set(values))
    if len(distinct) <= MSG_MAX_CATEGORIES:
        index = {v: i for i, v in enumerate(distinct)}
        return [index[v] for v in values]
    lo, hi = distinct[0], distinct[-1]
    return [_quantise(v, MSG_CHANNEL_BINS, lo, hi) for v in values]


def measure_message_coded_targeting(
    traces: list[list[dict[str, Any]]], perspective: str
) -> dict[str, Any]:
    """NMI between target_id and each message channel, pooled over
    ``traces`` (one list of ticks per match), with a permutation null."""
    act_key = "actions_a" if perspective == "a" else "actions_b"
    xs: list[int] = []
    chans: list[list[float]] = [[] for _ in range(MSG_SIZE)]
    target_events = observation_ticks = 0
    first_tick: int | None = None
    for ticks in traces:
        prev_target: dict[int, int] = {}
        for t in _decisions(ticks):
            observed = False
            for a in t.get(act_key, []):
                if not a.get("alive") or int(a["target_id"]) < 0:
                    continue
                tid = int(a["target_id"])
                if prev_target.get(a["id"]) != tid:
                    target_events += 1
                    prev_target[a["id"]] = tid
                msg = a.get("msg", [0.0] * MSG_SIZE)
                xs.append(tid)
                for c in range(MSG_SIZE):
                    chans[c].append(float(msg[c]))
                observed = True
            if observed:
                observation_ticks += 1
                if first_tick is None:
                    first_tick = int(t["tick"])
    codes = [_channel_codes(ch) for ch in chans] if xs else [[] for _ in chans]
    nmi = [round(_nmi(xs, ys), 4) if xs else 0.0 for ys in codes]
    best_c = max(range(MSG_SIZE), key=lambda c: nmi[c])
    null_q: float | None = None
    enough = target_events >= MSG_MIN_TARGET_EVENTS and observation_ticks >= MSG_MIN_TICKS
    if enough and nmi[best_c] >= MSG_MIN_NMI:
        rng = random.Random(len(xs))
        shuffled = list(xs)
        null = []
        for _ in range(MSG_PERMUTATIONS):
            rng.shuffle(shuffled)
            null.append(max(_nmi(shuffled, ys) for ys in codes))
        null.sort()
        null_q = round(null[math.ceil(MSG_PERM_QUANTILE * MSG_PERMUTATIONS) - 1], 4)
    return {
        "target_events": target_events,
        "observation_ticks": observation_ticks,
        "nmi_per_channel": nmi,
        "best_channel": best_c,
        "null_quantile": null_q,
        "first_tick": first_tick,
        "fires": null_q is not None and nmi[best_c] > null_q,
    }


def detect_message_coded_targeting(
    ticks: list[dict[str, Any]], perspective: str
) -> TacticEvent | None:
    m = measure_message_coded_targeting([ticks], perspective)
    if not m["fires"]:
        return None
    return _event(
        "message_coded_targeting",
        perspective,
        int(m["first_tick"]),
        int(m["observation_ticks"]),
        {
            "best_channel": m["best_channel"],
            "nmi": m["nmi_per_channel"][m["best_channel"]],
            "null_quantile": m["null_quantile"],
            "target_events": m["target_events"],
            "observation_ticks": m["observation_ticks"],
            "threshold_nmi": MSG_MIN_NMI,
        },
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _perspective_teams(perspective: str) -> tuple[str, str]:
    p = perspective.lower()
    if p == "a":
        return "team_a", "team_b"
    if p == "b":
        return "team_b", "team_a"
    raise ValueError(f"perspective must be 'a' or 'b', got {perspective!r}")


_DETECTORS = {
    "flanking": detect_flanking,
    "kiting": detect_kiting,
    "focus_fire": detect_focus_fire,
    "message_coded_targeting": detect_message_coded_targeting,
}


def scan_trace(
    ticks: list[dict[str, Any]],
    *,
    perspective: str = "a",
    track: str = "",
    model: str = "",
    seed: int = -1,
    generation: int = -1,
    tactics: Iterable[str] | None = None,
) -> list[TacticEvent]:
    """Run every requested detector and return the fired events.

    Each detector is *pure* — it returns either a ``TacticEvent`` or
    ``None`` — so ``scan_trace`` just forwards metadata into the result.
    Missing/unknown tactic names raise ``ValueError``.
    """
    selected = list(tactics) if tactics is not None else list(TACTICS)
    for name in selected:
        if name not in _DETECTORS:
            raise ValueError(f"unknown tactic: {name!r}")
    events: list[TacticEvent] = []
    for name in selected:
        fn = _DETECTORS[name]
        ev = fn(ticks, perspective)
        if ev is None:
            continue
        # Rehydrate metadata that the detectors don't see.
        events.append(
            TacticEvent(
                schema_version=ev.schema_version,
                tactic=ev.tactic,
                first_tick=ev.first_tick,
                sustained_ticks=ev.sustained_ticks,
                track=track,
                model=model,
                seed=seed,
                generation=generation,
                perspective=ev.perspective,
                evidence=ev.evidence,
            )
        )
    return events


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_events(events: Iterable[TacticEvent], out_path: Path, *, pretty: bool = False) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            if pretty:
                fh.write(json.dumps(asdict(ev), indent=2, sort_keys=True))
                fh.write("\n")
            else:
                fh.write(ev.as_jsonl_line())
                fh.write("\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError as e:
            # Tolerate a trailing partial line (kill-9 on appender).
            if i == len(path.read_text().splitlines()) - 1:
                continue
            raise ValueError(f"{path}:{i}: invalid JSON ({e.msg})") from e
    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_scan(args: argparse.Namespace) -> int:
    trace_path = Path(args.trace).resolve()
    out_path = Path(args.out).resolve()
    ticks = telemetry_aar.load_v2_trace(trace_path)
    events = scan_trace(
        ticks,
        perspective=args.perspective,
        track=args.track,
        model=args.model,
        seed=args.seed,
        generation=args.generation,
        tactics=args.tactic or None,
    )
    write_events(events, out_path, pretty=args.pretty)
    # Summary for callers.
    fired = {ev.tactic for ev in events}
    silent = [t for t in TACTICS if t not in fired]
    print(
        f"tactic_detector: wrote {len(events)} events to {out_path} "
        f"fired={sorted(fired) or 'none'} silent={silent}"
    )
    return 0


def _cmd_dump_config(_: argparse.Namespace) -> int:
    cfg = {
        "schema_version": SCHEMA_VERSION,
        "tactics": list(TACTICS),
        "first_decision_tick": FIRST_DECISION_TICK,
        "flanking": {
            "min_angle_deg": FLANK_MIN_ANGLE_DEG,
            "min_streak_ticks": FLANK_MIN_STREAK_TICKS,
            "min_allies_alive": FLANK_MIN_ALLIES_ALIVE,
            "max_spread_ratio": FLANK_MAX_SPREAD_RATIO,
            "max_range": FLANK_MAX_RANGE,
            "min_mean_speed": FLANK_MIN_MEAN_SPEED,
        },
        "kiting": {
            "cos_min": KITE_COS_MIN,
            "min_fraction": KITE_MIN_FRACTION,
            "min_streak_ticks": KITE_MIN_STREAK_TICKS,
            "min_vel_mag": KITE_MIN_VEL_MAG,
        },
        "focus_fire": {
            "min_focused_share": FF_MIN_FOCUSED_SHARE,
            "min_hits_per_enemy": FF_MIN_HITS_PER_ENEMY,
        },
        "message_coded_targeting": {
            "min_nmi": MSG_MIN_NMI,
            "min_target_events": MSG_MIN_TARGET_EVENTS,
            "min_ticks": MSG_MIN_TICKS,
            "channel_bins": MSG_CHANNEL_BINS,
            "max_categories": MSG_MAX_CATEGORIES,
            "permutations": MSG_PERMUTATIONS,
            "permutation_quantile": MSG_PERM_QUANTILE,
            "msg_size": MSG_SIZE,
        },
    }
    print(json.dumps(cfg, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SwarmEvolve M18 tactic detector.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan a v2 trace → tactic_events.jsonl")
    s.add_argument("trace", help="path to v2 trace.jsonl")
    s.add_argument("out", help="path to output tactic_events.jsonl")
    s.add_argument("--perspective", choices=("a", "b"), default="a")
    s.add_argument("--track", default="", help="experimental track (A/B/C)")
    s.add_argument("--model", default="", help="model id")
    s.add_argument("--seed", type=int, default=-1, help="lineage seed")
    s.add_argument("--generation", type=int, default=-1)
    s.add_argument(
        "--tactic",
        action="append",
        choices=list(TACTICS),
        help="restrict to a single tactic (repeatable)",
    )
    s.add_argument(
        "--pretty", action="store_true", help="indent output JSON (not JSON-lines; for debugging)"
    )
    s.set_defaults(func=_cmd_scan)

    d = sub.add_parser("dump-config", help="print the frozen threshold configuration")
    d.set_defaults(func=_cmd_dump_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    # Thresholds (exported for tests + docs):
    "FF_MIN_FOCUSED_SHARE",
    "FF_MIN_HITS_PER_ENEMY",
    "FIRST_DECISION_TICK",
    "FLANK_MAX_RANGE",
    "FLANK_MAX_SPREAD_RATIO",
    "FLANK_MIN_ALLIES_ALIVE",
    "FLANK_MIN_ANGLE_DEG",
    "FLANK_MIN_MEAN_SPEED",
    "FLANK_MIN_STREAK_TICKS",
    "KITE_COS_MIN",
    "KITE_MIN_FRACTION",
    "KITE_MIN_STREAK_TICKS",
    "KITE_MIN_VEL_MAG",
    "MSG_CHANNEL_BINS",
    "MSG_MAX_CATEGORIES",
    "MSG_MIN_NMI",
    "MSG_MIN_TARGET_EVENTS",
    "MSG_MIN_TICKS",
    "MSG_PERMUTATIONS",
    "MSG_PERM_QUANTILE",
    "MSG_SIZE",
    "SCHEMA_VERSION",
    "TACTICS",
    "TacticEvent",
    "build_parser",
    "detect_flanking",
    "detect_focus_fire",
    "detect_kiting",
    "detect_message_coded_targeting",
    "main",
    "measure_flanking",
    "measure_focus_fire",
    "measure_kiting",
    "measure_message_coded_targeting",
    "read_events",
    "scan_trace",
    "write_events",
]
