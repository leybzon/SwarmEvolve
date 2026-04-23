#!/usr/bin/env python3
"""Tactic Detector (M18).

Deterministic feature extractor over v2 traces (see M15a). Fires one
first-appearance event per (track, model, seed, generation) per tactic,
written to ``tactic_events.jsonl`` (one record per line).

Detected tactics (all frozen thresholds; any change bumps
``SCHEMA_VERSION``):

Flanking
    Two ally clusters with ≥ 90° angular separation from the enemy
    centroid sustained ≥ 50 ticks. Clustering is done deterministically
    by projecting alive allies onto the principal axis passing through
    the enemy centroid and splitting at the median projection value.

Kiting
    Sustained retreat velocity aligned with the (enemy centroid → ally)
    vector. A drone is "kiting" this tick when its velocity has a
    positive component along ``(ally - enemy_centroid)`` with cosine
    similarity ≥ cos(30°) ≈ 0.866. A tick is a "kite tick" when at least
    50% of alive allies satisfy this. The detector fires when the team
    sustains ≥ 100 consecutive kite ticks.

Focus-fire discipline
    Low redundant fire: the fraction of attack attempts aimed at an
    already-dead target stays ≤ 0.10 over a window of ≥ 200 attempts.
    (We use a 200-attempt rolling window rather than a tick window so
    early-game no-combat phases don't starve the detector.)

Message-coded targeting
    Normalised mutual information (NMI) between a drone's ``target_id``
    and any single float channel of its outgoing message bus, computed
    over the set of ticks where target changed. Fires when
    ``max_channel_NMI ≥ 0.25`` on ≥ 20 target-change events with ≥ 100
    observation ticks.

CLI
---

    tactic_detector.py scan <trace.jsonl> <out.jsonl> \
        --track A --model claude-opus-4-7 --seed 42 --generation 3 \
        [--perspective a|b] [--pretty]
    tactic_detector.py dump-config      # prints the frozen thresholds

Acceptance (NEXT_PHASE_PLAN.md §M18): each detector must fire on a
handcrafted positive fixture and stay silent on a handcrafted
counter-example. See ``tests/test_tactic_detector.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable, Iterator

# --- local imports: scripts/ on sys.path -----------------------------------
_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import telemetry_aar  # noqa: E402

# ---------------------------------------------------------------------------
# Constants (frozen; bump SCHEMA_VERSION on change)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

#: All tactic names in canonical order.
TACTICS: tuple[str, ...] = (
    "flanking",
    "kiting",
    "focus_fire_discipline",
    "message_coded_targeting",
)

# --- Flanking thresholds
FLANK_MIN_ANGLE_DEG: float = 90.0
FLANK_MIN_STREAK_TICKS: int = 50
FLANK_MIN_ALLIES_ALIVE: int = 3  # need ≥3 so the two clusters both have ≥1

# --- Kiting thresholds
KITE_COS_MIN: float = math.cos(math.radians(30.0))  # ≈ 0.866
KITE_MIN_FRACTION: float = 0.5
KITE_MIN_STREAK_TICKS: int = 100
KITE_MIN_VEL_MAG: float = 0.05  # ignore near-stationary drones

# --- Focus-fire discipline thresholds
FF_WINDOW_ATTEMPTS: int = 200
FF_MAX_REDUNDANT_RATIO: float = 0.10

# --- Message-coded targeting thresholds
MSG_MIN_NMI: float = 0.25
MSG_MIN_TARGET_EVENTS: int = 20
MSG_MIN_TICKS: int = 100
MSG_CHANNEL_BINS: int = 4
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
    projected = [
        (px * (d["x"] - ex) + py * (d["y"] - ey), d) for d in allies
    ]
    # Sort by projection then by drone id for stability.
    projected.sort(key=lambda t: (t[0], t[1]["id"]))
    half = len(projected) // 2
    low = [t[1] for t in projected[:half]]
    high = [t[1] for t in projected[half:]]
    return low, high


def detect_flanking(
    ticks: list[dict[str, Any]],
    perspective: str,
) -> TacticEvent | None:
    """Fire if two ally clusters maintain ≥ FLANK_MIN_ANGLE_DEG angular
    separation (as seen from enemy centroid) for ≥ FLANK_MIN_STREAK_TICKS
    consecutive ticks."""
    us_key, them_key = _perspective_teams(perspective)
    streak = 0
    best_streak = 0
    streak_start: int | None = None
    best_start: int | None = None
    best_angle_at_fire = 0.0
    for t in ticks:
        allies = _alive(t[us_key])
        enemies = _alive(t[them_key])
        if len(allies) < FLANK_MIN_ALLIES_ALIVE or not enemies:
            streak = 0
            streak_start = None
            continue
        enemy_c = _centroid(enemies)
        assert enemy_c is not None
        low, high = _split_allies_by_median_projection(allies, enemy_c)
        if not low or not high:
            streak = 0
            streak_start = None
            continue
        c_low = _centroid(low)
        c_high = _centroid(high)
        assert c_low is not None and c_high is not None
        v_low = (c_low[0] - enemy_c[0], c_low[1] - enemy_c[1])
        v_high = (c_high[0] - enemy_c[0], c_high[1] - enemy_c[1])
        angle = _angle_between(v_low, v_high)
        if angle >= FLANK_MIN_ANGLE_DEG:
            if streak == 0:
                streak_start = int(t["tick"])
            streak += 1
            if streak > best_streak:
                best_streak = streak
                best_start = streak_start
                best_angle_at_fire = angle
                if streak >= FLANK_MIN_STREAK_TICKS and best_start is not None:
                    return TacticEvent(
                        schema_version=SCHEMA_VERSION,
                        tactic="flanking",
                        first_tick=best_start,
                        sustained_ticks=streak,
                        track="", model="", seed=-1, generation=-1,
                        perspective=perspective,
                        evidence={
                            "angle_deg": round(angle, 2),
                            "cluster_sizes": [len(low), len(high)],
                            "threshold_angle_deg": FLANK_MIN_ANGLE_DEG,
                            "threshold_streak_ticks": FLANK_MIN_STREAK_TICKS,
                        },
                    )
        else:
            streak = 0
            streak_start = None
    return None


# ---------------------------------------------------------------------------
# Kiting detector
# ---------------------------------------------------------------------------


def detect_kiting(
    ticks: list[dict[str, Any]],
    perspective: str,
) -> TacticEvent | None:
    """Fire on the longest streak of ≥ KITE_MIN_STREAK_TICKS consecutive
    ticks where ≥ KITE_MIN_FRACTION of alive allies are moving *away*
    from the enemy centroid (velocity aligned with ally-from-enemy
    vector within KITE_COS_MIN)."""
    us_key, them_key = _perspective_teams(perspective)
    us_actions = "actions_a" if perspective == "a" else "actions_b"
    streak = 0
    streak_start: int | None = None
    best_frac = 0.0
    for t in ticks:
        allies = _alive(t[us_key])
        enemies = _alive(t[them_key])
        actions = t.get(us_actions, [])
        if not allies or not enemies or not actions:
            streak = 0
            streak_start = None
            continue
        enemy_c = _centroid(enemies)
        assert enemy_c is not None
        actions_by_id = {a["id"]: a for a in actions}
        kiting_count = 0
        considered = 0
        for d in allies:
            a = actions_by_id.get(d["id"])
            if a is None or not a.get("alive"):
                continue
            vx, vy = float(a["vx"]), float(a["vy"])
            if _norm((vx, vy)) < KITE_MIN_VEL_MAG:
                continue
            considered += 1
            radial = (d["x"] - enemy_c[0], d["y"] - enemy_c[1])
            rn = _norm(radial)
            if rn == 0.0:
                continue
            vn = _norm((vx, vy))
            cos_sim = (vx * radial[0] + vy * radial[1]) / (vn * rn)
            if cos_sim >= KITE_COS_MIN:
                kiting_count += 1
        if considered == 0:
            streak = 0
            streak_start = None
            continue
        frac = kiting_count / considered
        if frac >= KITE_MIN_FRACTION:
            if streak == 0:
                streak_start = int(t["tick"])
            streak += 1
            if frac > best_frac:
                best_frac = frac
            if streak >= KITE_MIN_STREAK_TICKS and streak_start is not None:
                return TacticEvent(
                    schema_version=SCHEMA_VERSION,
                    tactic="kiting",
                    first_tick=streak_start,
                    sustained_ticks=streak,
                    track="", model="", seed=-1, generation=-1,
                    perspective=perspective,
                    evidence={
                        "kiting_fraction": round(frac, 3),
                        "max_kiting_fraction": round(best_frac, 3),
                        "threshold_fraction": KITE_MIN_FRACTION,
                        "threshold_streak_ticks": KITE_MIN_STREAK_TICKS,
                        "cos_min": round(KITE_COS_MIN, 4),
                    },
                )
        else:
            streak = 0
            streak_start = None
    return None


# ---------------------------------------------------------------------------
# Focus-fire discipline detector
# ---------------------------------------------------------------------------


def detect_focus_fire_discipline(
    ticks: list[dict[str, Any]],
    perspective: str,
) -> TacticEvent | None:
    """Fire when the redundant-shot ratio over a rolling
    FF_WINDOW_ATTEMPTS window stays ≤ FF_MAX_REDUNDANT_RATIO.

    A "redundant" shot is one whose target was already dead at resolution
    time — the engine emits those as AttackEvent(hit=False, dist=…) when
    the target had ``alive=False`` on its team snapshot the *previous*
    tick (we approximate that from the trace by flagging any attempt
    against an id that is not alive in the current tick's team snapshot
    and whose attack has ``hit=False``).
    """
    us_team_id = 0 if perspective == "a" else 1
    them_team_key = "team_b" if perspective == "a" else "team_a"
    window_attempts: list[int] = []  # 1 = redundant, 0 = not redundant
    first_tick_of_qualifying_window: int | None = None
    for t in ticks:
        tgt_alive = {d["id"]: d["alive"] for d in t[them_team_key]}
        for atk in t.get("attacks", []):
            if atk["atk_team"] != us_team_id:
                continue
            # Redundant ↔ target id was not alive at resolution time.
            is_redundant = (
                not atk["hit"] and not tgt_alive.get(atk["tgt_id"], True)
            )
            window_attempts.append(1 if is_redundant else 0)
            if len(window_attempts) > FF_WINDOW_ATTEMPTS:
                window_attempts.pop(0)
            if len(window_attempts) < FF_WINDOW_ATTEMPTS:
                continue
            ratio = sum(window_attempts) / len(window_attempts)
            if ratio <= FF_MAX_REDUNDANT_RATIO:
                if first_tick_of_qualifying_window is None:
                    first_tick_of_qualifying_window = int(t["tick"])
                return TacticEvent(
                    schema_version=SCHEMA_VERSION,
                    tactic="focus_fire_discipline",
                    first_tick=first_tick_of_qualifying_window,
                    sustained_ticks=FF_WINDOW_ATTEMPTS,
                    track="", model="", seed=-1, generation=-1,
                    perspective=perspective,
                    evidence={
                        "redundant_ratio": round(ratio, 4),
                        "window_attempts": FF_WINDOW_ATTEMPTS,
                        "threshold_max_ratio": FF_MAX_REDUNDANT_RATIO,
                    },
                )
    return None


# ---------------------------------------------------------------------------
# Message-coded targeting detector
# ---------------------------------------------------------------------------


def _quantise(x: float, bins: int,
              lo: float = -1.0, hi: float = 1.0) -> int:
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
    for x, y in zip(xs, ys):
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


def detect_message_coded_targeting(
    ticks: list[dict[str, Any]],
    perspective: str,
) -> TacticEvent | None:
    """Fire when max-over-channels NMI between the team's collective
    ``target_id`` and a single float channel of the outgoing message bus
    exceeds ``MSG_MIN_NMI`` on a sufficiently long observation window."""
    us_actions = "actions_a" if perspective == "a" else "actions_b"
    # Collect (target_id, msg_channels[]) per alive ally with a valid target.
    per_channel_xs: dict[int, list[int]] = {c: [] for c in range(MSG_SIZE)}
    per_channel_ys: dict[int, list[int]] = {c: [] for c in range(MSG_SIZE)}
    target_events = 0
    prev_target: dict[int, int] = {}
    ticks_counted = 0
    for t in ticks:
        actions = t.get(us_actions, [])
        if not actions:
            continue
        any_obs = False
        for a in actions:
            if not a.get("alive"):
                continue
            tid = int(a["target_id"])
            if tid < 0:
                continue
            if prev_target.get(a["id"]) != tid:
                target_events += 1
                prev_target[a["id"]] = tid
            msg = a.get("msg", [0.0] * MSG_SIZE)
            for c in range(MSG_SIZE):
                per_channel_xs[c].append(tid)
                per_channel_ys[c].append(_quantise(msg[c], MSG_CHANNEL_BINS))
            any_obs = True
        if any_obs:
            ticks_counted += 1
    if (ticks_counted < MSG_MIN_TICKS
            or target_events < MSG_MIN_TARGET_EVENTS):
        return None
    best_c = -1
    best_nmi = -1.0
    for c in range(MSG_SIZE):
        nmi = _nmi(per_channel_xs[c], per_channel_ys[c])
        if nmi > best_nmi:
            best_nmi = nmi
            best_c = c
    if best_nmi < MSG_MIN_NMI:
        return None
    # Find the earliest tick with any observation.
    first_tick = 0
    for t in ticks:
        if any(a.get("alive") and int(a["target_id"]) >= 0
               for a in t.get(us_actions, [])):
            first_tick = int(t["tick"])
            break
    return TacticEvent(
        schema_version=SCHEMA_VERSION,
        tactic="message_coded_targeting",
        first_tick=first_tick,
        sustained_ticks=ticks_counted,
        track="", model="", seed=-1, generation=-1,
        perspective=perspective,
        evidence={
            "best_channel": best_c,
            "nmi": round(best_nmi, 4),
            "target_events": target_events,
            "observation_ticks": ticks_counted,
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
    "focus_fire_discipline": detect_focus_fire_discipline,
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
        events.append(TacticEvent(
            schema_version=ev.schema_version,
            tactic=ev.tactic,
            first_tick=ev.first_tick,
            sustained_ticks=ev.sustained_ticks,
            track=track, model=model, seed=seed, generation=generation,
            perspective=ev.perspective,
            evidence=ev.evidence,
        ))
    return events


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_events(events: Iterable[TacticEvent], out_path: Path,
                 *, pretty: bool = False) -> None:
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
            raise ValueError(
                f"{path}:{i}: invalid JSON ({e.msg})"
            ) from e
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
        "flanking": {
            "min_angle_deg": FLANK_MIN_ANGLE_DEG,
            "min_streak_ticks": FLANK_MIN_STREAK_TICKS,
            "min_allies_alive": FLANK_MIN_ALLIES_ALIVE,
        },
        "kiting": {
            "cos_min": KITE_COS_MIN,
            "min_fraction": KITE_MIN_FRACTION,
            "min_streak_ticks": KITE_MIN_STREAK_TICKS,
            "min_vel_mag": KITE_MIN_VEL_MAG,
        },
        "focus_fire_discipline": {
            "window_attempts": FF_WINDOW_ATTEMPTS,
            "max_redundant_ratio": FF_MAX_REDUNDANT_RATIO,
        },
        "message_coded_targeting": {
            "min_nmi": MSG_MIN_NMI,
            "min_target_events": MSG_MIN_TARGET_EVENTS,
            "min_ticks": MSG_MIN_TICKS,
            "channel_bins": MSG_CHANNEL_BINS,
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

    s = sub.add_parser("scan",
                       help="scan a v2 trace → tactic_events.jsonl")
    s.add_argument("trace", help="path to v2 trace.jsonl")
    s.add_argument("out", help="path to output tactic_events.jsonl")
    s.add_argument("--perspective", choices=("a", "b"), default="a")
    s.add_argument("--track", default="", help="experimental track (A/B/C)")
    s.add_argument("--model", default="", help="model id")
    s.add_argument("--seed", type=int, default=-1, help="lineage seed")
    s.add_argument("--generation", type=int, default=-1)
    s.add_argument("--tactic", action="append",
                   choices=list(TACTICS),
                   help="restrict to a single tactic (repeatable)")
    s.add_argument("--pretty", action="store_true",
                   help="indent output JSON (not JSON-lines; for debugging)")
    s.set_defaults(func=_cmd_scan)

    d = sub.add_parser("dump-config",
                       help="print the frozen threshold configuration")
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
    "SCHEMA_VERSION", "TACTICS", "TacticEvent",
    "detect_flanking", "detect_kiting",
    "detect_focus_fire_discipline", "detect_message_coded_targeting",
    "scan_trace", "write_events", "read_events",
    "build_parser", "main",
    # Thresholds (exported for tests + docs):
    "FLANK_MIN_ANGLE_DEG", "FLANK_MIN_STREAK_TICKS", "FLANK_MIN_ALLIES_ALIVE",
    "KITE_COS_MIN", "KITE_MIN_FRACTION", "KITE_MIN_STREAK_TICKS",
    "KITE_MIN_VEL_MAG",
    "FF_WINDOW_ATTEMPTS", "FF_MAX_REDUNDANT_RATIO",
    "MSG_MIN_NMI", "MSG_MIN_TARGET_EVENTS", "MSG_MIN_TICKS",
    "MSG_CHANNEL_BINS", "MSG_SIZE",
]
