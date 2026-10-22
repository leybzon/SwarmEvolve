#!/usr/bin/env python3
"""Calibration before any LLM run (v2 study): match noise, power, instruments, engine limits.

Baselines only (pursuit_v1, cluster_v1, stationary_v1); no paid API calls.
Every match runs in the eval container (``fitness.evaluate_fitness`` with
``backend="sandbox"``). Raw outputs go to ``reports/calibration/``; the
analysis reads only those files and writes ``calibration_results.json``,
from which ``CALIBRATION.md`` is rendered.

Steps (each a subcommand, run in this order)::

    python scripts/calibration.py crossplay    # 6 pairings x 2,000 seeds x both sides
    python scripts/calibration.py instruments  # 20 recorded matches, AAR + tactic detector
    python scripts/calibration.py limits       # 5-50 drones x arena scale 1.0-2.0
    python scripts/calibration.py analyze      # stats, noise, power -> calibration_results.json
    python scripts/calibration.py render       # CALIBRATION.md from the results file

Seed blocks (disjoint from each other and from the setup checks):
crossplay 400000-401999, instruments 410000-410019, limits 420000-420049.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fitness  # noqa: E402
import tactic_detector as td  # noqa: E402
import telemetry_aar  # noqa: E402

BASELINES = ("pursuit_v1", "cluster_v1", "stationary_v1")
BASELINE_DIR = REPO_ROOT / "src" / "baselines"
OUT = REPO_ROOT / "reports" / "calibration"

CROSSPLAY_SEED_BASE, CROSSPLAY_SEEDS = 400000, 2000
INSTRUMENT_SEED_BASE, INSTRUMENT_MATCHES = 410000, 20
LIMITS_SEED_BASE, LIMITS_SEEDS = 420000, 50
LIMITS_DRONES = (5, 10, 20, 50)
LIMITS_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)

MAX_TICKS = 1000
DEFAULT_DRONES = 10
DISABLE_RANGE = 50.0  # engine.cpp init_world
MAX_COOLDOWN = 10  # engine.cpp init_world

POWER_MATCHES = (100, 200, 400, 800)
POWER_DELTAS = (0.05, 0.1, 0.2, 0.3)
POWER_SIMS = 2000
POWER_FLIPS = 2000
POWER_ALPHA = 0.10
POWER_TARGET = 0.80
BOOTSTRAP = 10000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write_status(name: str, **fields: Any) -> None:
    fields["updated_utc"] = _now()
    (OUT / f"{name}_status.json").write_text(json.dumps(fields, sort_keys=True) + "\n")


def _scratch() -> Path:
    base = Path.home() / ".pytest-sandbox"
    base.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="calib_", dir=base))


def _src(name: str) -> Path:
    return BASELINE_DIR / f"{name}.cpp"


def _evaluate(a: str, b: str, **kwargs: Any) -> fitness.FitnessResult:
    return fitness.evaluate_fitness(
        _src(a), _src(b), backend="sandbox", raise_on_build_failure=False, **kwargs
    )


def _run_record(result: fitness.FitnessResult, **extra: Any) -> dict[str, Any]:
    d = {k: v for k, v in result.to_dict().items() if k not in ("per_match", "platform")}
    d["platform"] = result.platform
    d.update(extra)
    return d


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# Step 1: cross-play
# ---------------------------------------------------------------------------


def pairings() -> list[tuple[str, str]]:
    """Unordered pairings, mirrors included, in BASELINES order."""
    return list(itertools.combinations_with_replacement(BASELINES, 2))


def cmd_crossplay(args: argparse.Namespace) -> None:
    scratch = _scratch()
    rows: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    jobs = [(x, y, side) for x, y in pairings() for side in ("x_as_A", "x_as_B")]
    for i, (x, y, side) in enumerate(jobs):
        _write_status("crossplay", state="running", done=i, total=len(jobs))
        a, b = (x, y) if side == "x_as_A" else (y, x)
        r = _evaluate(
            a,
            b,
            n_matches=CROSSPLAY_SEEDS,
            seed_base=CROSSPLAY_SEED_BASE,
            scratch_root=scratch,
        )
        runs.append(_run_record(r, x=x, y=y, assignment=side, team_a=a, team_b=b))
        for m in r.per_match:
            rows.append({"x": x, "y": y, "assignment": side, "team_a": a, "team_b": b, **m})
    with (OUT / "crossplay_matches.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    meta = {"started_git_sha": _git_sha(), "finished_utc": _now(), "runs": runs}
    (OUT / "crossplay_runs.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    shutil.rmtree(scratch, ignore_errors=True)
    _write_status("crossplay", state="done", done=len(jobs), total=len(jobs))


# ---------------------------------------------------------------------------
# Step 3: instruments (AAR + tactic detector) on recorded matches
# ---------------------------------------------------------------------------


def instrument_jobs() -> list[dict[str, Any]]:
    """Match i uses seed base+i and ordered pairing i mod 9 (mirrors included)."""
    ordered = list(itertools.product(BASELINES, BASELINES))
    return [
        {
            "match": i,
            "seed": INSTRUMENT_SEED_BASE + i,
            "team_a": ordered[i % len(ordered)][0],
            "team_b": ordered[i % len(ordered)][1],
        }
        for i in range(INSTRUMENT_MATCHES)
    ]


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )


def check_aar_against_trace(
    records: list[dict[str, Any]], aar: dict[str, Any], row: dict[str, Any], perspective: str
) -> dict[str, bool]:
    """Recompute AAR quantities from the raw trace (and the engine's own
    summary row) without telemetry_aar's code; True where they agree."""
    us, them = ("team_a", "team_b") if perspective == "A" else ("team_b", "team_a")
    us_id = 0 if perspective == "A" else 1
    alive_us_row, alive_them_row = (
        (row["a_alive"], row["b_alive"]) if perspective == "A" else (row["b_alive"], row["a_alive"])
    )
    last = records[-1]
    checks: dict[str, bool] = {}
    checks["outcome_matches_engine"] = last.get("outcome") == row["outcome"]
    checks["ticks_match_engine"] = aar["ticks"] == row["ticks"] == len(records) - 1
    checks["alive_us_matches_engine"] = aar["alive_final_us"] == alive_us_row
    checks["alive_them_matches_engine"] = aar["alive_final_them"] == alive_them_row

    fired = {0: 0, 1: 0}
    hits = {0: 0, 1: 0}
    events_consistent = True
    deaths_explained = True
    for t in range(1, len(records)):
        prev, cur = records[t - 1], records[t]
        hit_targets: set[tuple[int, int]] = set()
        for ev in cur.get("attacks", []):
            atk_team = "team_a" if ev["atk_team"] == 0 else "team_b"
            tgt_team = "team_a" if ev["tgt_team"] == 0 else "team_b"
            attacker_prev = prev[atk_team][ev["atk_id"]]
            attacker_cur = cur[atk_team][ev["atk_id"]]
            target_prev = prev[tgt_team][ev["tgt_id"]]
            target_cur = cur[tgt_team][ev["tgt_id"]]
            # Positions after movement = this tick's snapshot (deaths do not move drones).
            dist = math.hypot(
                attacker_cur["x"] - target_cur["x"], attacker_cur["y"] - target_cur["y"]
            )
            ok = (
                attacker_prev["alive"]
                and attacker_prev["cooldown"] == 0
                and abs(dist - ev["dist"]) <= 0.02  # trace positions are printed to 2 dp
                and ev["hit"] == (ev["dist"] <= DISABLE_RANGE and target_prev["alive"])
            )
            if ev["hit"]:
                # A successful attacker pays max_cooldown; the end-of-tick
                # decrement skips drones that died this tick.
                paid = MAX_COOLDOWN - 1 if attacker_cur["alive"] else MAX_COOLDOWN
                ok = ok and attacker_cur["cooldown"] == paid and not target_cur["alive"]
                hit_targets.add((ev["tgt_team"], ev["tgt_id"]))
                hits[ev["atk_team"]] += 1
            fired[ev["atk_team"]] += 1
            events_consistent = events_consistent and ok
        for team_id, key in ((0, "team_a"), (1, "team_b")):
            for d_prev, d_cur in zip(prev[key], cur[key], strict=True):
                if (
                    d_prev["alive"]
                    and not d_cur["alive"]
                    and (team_id, d_cur["id"]) not in hit_targets
                ):
                    deaths_explained = False
    checks["attack_events_follow_engine_rules"] = events_consistent
    checks["every_death_has_a_hit"] = deaths_explained
    checks["shots_fired_us"] = aar["shots_fired_us"] == fired[us_id]
    checks["shots_fired_them"] = aar["shots_fired_them"] == fired[1 - us_id]
    checks["shots_hit_us"] = aar["shots_hit_us"] == hits[us_id]
    checks["shots_hit_them"] = aar["shots_hit_them"] == hits[1 - us_id]

    def cd_util(key: str) -> float:
        cells = [d for rec in records for d in rec[key] if d["alive"]]
        return sum(d["cooldown"] == 0 for d in cells) / len(cells) if cells else 0.0

    def mean_pd(key: str) -> float:
        per_tick = []
        for rec in records:
            pts = [(d["x"], d["y"]) for d in rec[key] if d["alive"]]
            pairs = list(itertools.combinations(pts, 2))
            per_tick.append(sum(math.dist(p, q) for p, q in pairs) / len(pairs) if pairs else 0.0)
        return sum(per_tick) / len(per_tick)

    checks["cooldown_utilization_us"] = abs(aar["cooldown_utilization_us"] - cd_util(us)) < 1e-4
    checks["cooldown_utilization_them"] = (
        abs(aar["cooldown_utilization_them"] - cd_util(them)) < 1e-4
    )
    checks["mean_pairwise_distance_us"] = abs(aar["mean_pairwise_distance_us"] - mean_pd(us)) < 1e-3
    checks["mean_pairwise_distance_them"] = (
        abs(aar["mean_pairwise_distance_them"] - mean_pd(them)) < 1e-3
    )
    msgs = [v for rec in records for a in rec[f"actions_{us[-1]}"] if a["alive"] for v in a["msg"]]
    checks["message_bus_used"] = aar["message_bus_used"] == any(v != 0.0 for v in msgs)
    return checks


def probe_detectors(records: list[dict[str, Any]], perspective: str) -> dict[str, Any]:
    """The quantities behind each tactic detector, computed with the
    detector's own helpers and per-tick rules, whether or not it fires."""
    us_key, them_key = td._perspective_teams(perspective)
    act_key = "actions_a" if perspective == "a" else "actions_b"
    us_team_id = 0 if perspective == "a" else 1

    # Flanking: angle between the two median-split ally clusters seen from the enemy centroid.
    flank_streak = flank_best = flank_eligible = flank_wide = 0
    flank_first_wide_tick = None
    for t in records:
        allies, enemies = td._alive(t[us_key]), td._alive(t[them_key])
        if len(allies) < td.FLANK_MIN_ALLIES_ALIVE or not enemies:
            flank_streak = 0
            continue
        ec = td._centroid(enemies)
        assert ec is not None
        low, high = td._split_allies_by_median_projection(allies, ec)
        if not low or not high:
            flank_streak = 0
            continue
        flank_eligible += 1
        cl, ch = td._centroid(low), td._centroid(high)
        assert cl is not None and ch is not None
        angle = td._angle_between((cl[0] - ec[0], cl[1] - ec[1]), (ch[0] - ec[0], ch[1] - ec[1]))
        if angle >= td.FLANK_MIN_ANGLE_DEG:
            flank_wide += 1
            flank_streak += 1
            flank_best = max(flank_best, flank_streak)
            if flank_first_wide_tick is None:
                flank_first_wide_tick = int(t["tick"])
        else:
            flank_streak = 0

    # Kiting: fraction of moving allies retreating from the enemy centroid.
    kite_streak = kite_best = 0
    max_speed = 0.0
    for t in records:
        allies, enemies = td._alive(t[us_key]), td._alive(t[them_key])
        actions = {a["id"]: a for a in t.get(act_key, [])}
        for a in actions.values():
            if a.get("alive"):
                max_speed = max(max_speed, math.hypot(a["vx"], a["vy"]))
        if not allies or not enemies or not actions:
            kite_streak = 0
            continue
        ec = td._centroid(enemies)
        assert ec is not None
        considered = kiting = 0
        for d in allies:
            a = actions.get(d["id"])
            if a is None or not a.get("alive"):
                continue
            v = (float(a["vx"]), float(a["vy"]))
            if td._norm(v) < td.KITE_MIN_VEL_MAG:
                continue
            considered += 1
            radial = (d["x"] - ec[0], d["y"] - ec[1])
            if td._norm(radial) == 0.0:
                continue
            if td._dot(v, radial) / (td._norm(v) * td._norm(radial)) >= td.KITE_COS_MIN:
                kiting += 1
        if considered and kiting / considered >= td.KITE_MIN_FRACTION:
            kite_streak += 1
            kite_best = max(kite_best, kite_streak)
        else:
            kite_streak = 0

    # Focus-fire discipline: share of attempts at a target already dead.
    attempts: list[int] = []
    for t in records:
        tgt_alive = {d["id"]: d["alive"] for d in t[them_key]}
        for atk in t.get("attacks", []):
            if atk["atk_team"] == us_team_id:
                attempts.append(int(not atk["hit"] and not tgt_alive.get(atk["tgt_id"], True)))
    w = td.FF_WINDOW_ATTEMPTS
    windows = [sum(attempts[i - w : i]) / w for i in range(w, len(attempts) + 1)]

    # Message-coded targeting: NMI(target_id, quantised channel) as the detector computes it,
    # plus the same NMI with each channel quantised over its observed range.
    xs: list[int] = []
    chans: list[list[float]] = [[] for _ in range(td.MSG_SIZE)]
    target_events = 0
    prev_target: dict[int, int] = {}
    for t in records:
        for a in t.get(act_key, []):
            if not a.get("alive") or int(a["target_id"]) < 0:
                continue
            tid = int(a["target_id"])
            if prev_target.get(a["id"]) != tid:
                target_events += 1
                prev_target[a["id"]] = tid
            xs.append(tid)
            for c in range(td.MSG_SIZE):
                chans[c].append(float(a["msg"][c]))
    nmi_detector = [
        round(td._nmi(xs, [td._quantise(v, td.MSG_CHANNEL_BINS) for v in ch]), 4) if xs else 0.0
        for ch in chans
    ]

    def ranged(ch: list[float]) -> list[int]:
        lo, hi = min(ch), max(ch)
        return [0 if hi <= lo else td._quantise(v, td.MSG_CHANNEL_BINS, lo, hi) for v in ch]

    nmi_ranged = [round(td._nmi(xs, ranged(ch)), 4) if xs else 0.0 for ch in chans]
    msg_range = [[min(ch), max(ch)] if ch else [0.0, 0.0] for ch in chans]
    return {
        "flanking_eligible_ticks": flank_eligible,
        "flanking_wide_ticks": flank_wide,
        "flanking_longest_streak": flank_best,
        "flanking_first_wide_tick": flank_first_wide_tick,
        "kiting_longest_streak": kite_best,
        "max_speed": round(max_speed, 4),
        "focus_fire_attempts": len(attempts),
        "focus_fire_redundant": sum(attempts),
        "focus_fire_min_window_ratio": round(min(windows), 4) if windows else None,
        "message_target_events": target_events,
        "message_observations": len(xs),
        "message_nmi_detector": nmi_detector,
        "message_nmi_observed_range": nmi_ranged,
        "message_channel_range": msg_range,
    }


def cmd_instruments(args: argparse.Namespace) -> None:
    scratch = _scratch()
    trace_dir = OUT / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    jobs = instrument_jobs()
    results = []
    for job in jobs:
        _write_status("instruments", state="running", done=job["match"], total=len(jobs))
        trace = scratch / f"trace_{job['match']:02d}.jsonl"
        r = _evaluate(
            job["team_a"],
            job["team_b"],
            n_matches=1,
            seed_base=job["seed"],
            workers=1,
            scratch_root=scratch,
            record_trace_for_match_idx=0,
            trace_output_path=trace,
        )
        row = r.per_match[0]
        data = trace.read_bytes()
        stored = trace_dir / f"{trace.name}.gz"
        stored.write_bytes(gzip.compress(data, mtime=0))
        records = telemetry_aar.load_v2_trace(trace)
        entry: dict[str, Any] = {
            **job,
            "row": row,
            "platform_label": r.platform["label"],
            "trace": str(stored.relative_to(REPO_ROOT)),
            "trace_sha256": hashlib.sha256(data).hexdigest(),
            "perspectives": {},
        }
        for persp in ("A", "B"):
            aar_json = scratch / f"aar_{job['match']:02d}_{persp}.json"
            cli = _run_cli(
                [
                    "scripts/telemetry_aar.py",
                    "--trace",
                    str(trace),
                    "--perspective",
                    persp,
                    "--format",
                    "json",
                    "--json-out",
                    str(aar_json),
                ]
            )
            aar = json.loads(aar_json.read_text()) if cli.returncode == 0 else {}
            events_path = scratch / f"tactics_{job['match']:02d}_{persp}.jsonl"
            det = _run_cli(
                [
                    "scripts/tactic_detector.py",
                    "scan",
                    str(trace),
                    str(events_path),
                    "--perspective",
                    persp.lower(),
                    "--seed",
                    str(job["seed"]),
                ]
            )
            events = td.read_events(events_path) if det.returncode == 0 else []
            entry["perspectives"][persp] = {
                "program": job["team_a"] if persp == "A" else job["team_b"],
                "aar_rc": cli.returncode,
                "aar": aar,
                "aar_checks": check_aar_against_trace(records, aar, row, persp) if aar else {},
                "detector_rc": det.returncode,
                "detector_events": events,
                "probe": probe_detectors(records, persp.lower()),
            }
        results.append(entry)
    meta = {
        "started_git_sha": _git_sha(),
        "finished_utc": _now(),
        "detector_config": json.loads(
            _run_cli(["scripts/tactic_detector.py", "dump-config"]).stdout
        ),
        "aar_metrics_schema_version": telemetry_aar.METRICS_SCHEMA_VERSION,
        "matches": results,
    }
    (OUT / "instruments.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    shutil.rmtree(scratch, ignore_errors=True)
    _write_status("instruments", state="done", done=len(jobs), total=len(jobs))


# ---------------------------------------------------------------------------
# Step 4: engine limits
# ---------------------------------------------------------------------------


def cmd_limits(args: argparse.Namespace) -> None:
    scratch = _scratch()
    ordered = list(itertools.product(BASELINES, BASELINES))
    configs = [(n, s, a, b) for n in LIMITS_DRONES for s in LIMITS_SCALES for a, b in ordered]
    rows: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for i, (n, scale, a, b) in enumerate(configs):
        _write_status("limits", state="running", done=i, total=len(configs))
        t0 = time.monotonic()
        r = _evaluate(
            a,
            b,
            n_matches=LIMITS_SEEDS,
            seed_base=LIMITS_SEED_BASE,
            workers=8,
            drones_a=n,
            drones_b=n,
            arena_scale=scale,
            scratch_root=scratch,
        )
        runs.append(
            _run_record(
                r,
                drones=n,
                arena_scale=scale,
                team_a=a,
                team_b=b,
                host_wall_seconds=round(time.monotonic() - t0, 3),
            )
        )
        for m in r.per_match:
            rows.append({"drones": n, "arena_scale": scale, "team_a": a, "team_b": b, **m})
    with (OUT / "limits_matches.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    meta = {"started_git_sha": _git_sha(), "finished_utc": _now(), "runs": runs}
    (OUT / "limits_runs.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    shutil.rmtree(scratch, ignore_errors=True)
    _write_status("limits", state="done", done=len(configs), total=len(configs))


# ---------------------------------------------------------------------------
# Step 2 + analysis
# ---------------------------------------------------------------------------


def _bootstrap_ci(values: Any, rng: Any) -> tuple[float, float]:
    import numpy as np

    v = np.asarray(values, dtype=float)
    idx = rng.integers(0, len(v), size=(BOOTSTRAP, len(v)))
    means = v[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)


def _rates(outcomes: list[str], n: int) -> dict[str, float]:
    return {o: round(outcomes.count(o) / n, 4) for o in sorted(set(outcomes))}


def _length(ticks: list[int]) -> dict[str, Any]:
    import numpy as np

    t = np.asarray(ticks)
    return {
        "mean": round(float(t.mean()), 1),
        "median": float(np.median(t)),
        "p10": float(np.quantile(t, 0.1)),
        "p90": float(np.quantile(t, 0.9)),
        "max": int(t.max()),
        "share_at_max_ticks": round(float((t >= MAX_TICKS).mean()), 4),
    }


def analyze_crossplay(rows: list[dict[str, Any]], rng: Any) -> dict[str, Any]:
    """Per pairing, from the perspective of x (the first-named program)."""
    import numpy as np

    out: dict[str, Any] = {}
    for x, y in pairings():
        sel = {
            side: sorted(
                (r for r in rows if r["x"] == x and r["y"] == y and r["assignment"] == side),
                key=lambda r: r["seed"],
            )
            for side in ("x_as_A", "x_as_B")
        }
        e1, e2 = sel["x_as_A"], sel["x_as_B"]
        assert [r["seed"] for r in e1] == [r["seed"] for r in e2]
        key = f"{x} vs {y}"
        if x == y:
            # Both assignments are the same match; report it once (A's view).
            identical = all(
                {k: v for k, v in r1.items() if k not in ("assignment", "wall_ms")}
                == {k: v for k, v in r2.items() if k not in ("assignment", "wall_ms")}
                for r1, r2 in zip(e1, e2, strict=True)
            )
            scores = [r["score"] for r in e1]
            surv = [r["a_alive"] - r["b_alive"] for r in e1]
            outcomes = [
                {"A_WIN": "win", "B_WIN": "loss", "DRAW": "draw"}.get(r["outcome"], "invalid")
                for r in e1
            ]
            n = len(e1)
            out[key] = {
                "mirror": True,
                "swap_rows_identical": identical,
                "n_matches": n,
                "n_seeds": n,
                "rates_side_A": _rates(outcomes, n),
                "invalid_rate": round(sum(o == "invalid" for o in outcomes) / n, 4),
                "mean_score_side_A": round(float(np.mean(scores)), 4),
                "mean_score_ci95": [round(v, 4) for v in _bootstrap_ci(scores, rng)],
                "survivor_diff_mean_side_A": round(float(np.mean(surv)), 4),
                "survivor_diff_ci95": [round(v, 4) for v in _bootstrap_ci(surv, rng)],
                "survivor_diff_sd": round(float(np.std(surv)), 4),
                "length": _length([r["ticks"] for r in e1]),
            }
            continue
        x_scores_1 = [r["score"] for r in e1]
        x_scores_2 = [-r["score"] for r in e2]
        surv_1 = [r["a_alive"] - r["b_alive"] for r in e1]
        surv_2 = [r["b_alive"] - r["a_alive"] for r in e2]
        win = {"A_WIN": "win", "B_WIN": "loss", "DRAW": "draw"}
        lose = {"A_WIN": "loss", "B_WIN": "win", "DRAW": "draw"}
        outcomes = [win.get(r["outcome"], "invalid") for r in e1] + [
            lose.get(r["outcome"], "invalid") for r in e2
        ]
        per_seed = (np.asarray(x_scores_1) + np.asarray(x_scores_2)) / 2
        per_seed_surv = (np.asarray(surv_1) + np.asarray(surv_2)) / 2
        n = len(e1) + len(e2)
        out[key] = {
            "mirror": False,
            "n_matches": n,
            "n_seeds": len(e1),
            "rates_for_x": _rates(outcomes, n),
            "invalid_rate": round(sum(o == "invalid" for o in outcomes) / n, 4),
            "mean_score_x": round(float(per_seed.mean()), 4),
            "mean_score_ci95": [round(v, 4) for v in _bootstrap_ci(per_seed, rng)],
            "mean_score_x_as_A": round(float(np.mean(x_scores_1)), 4),
            "mean_score_x_as_B": round(float(np.mean(x_scores_2)), 4),
            "survivor_diff_mean_x": round(float(per_seed_surv.mean()), 4),
            "survivor_diff_ci95": [round(v, 4) for v in _bootstrap_ci(per_seed_surv, rng)],
            "survivor_diff_sd_per_match": round(float(np.std(surv_1 + surv_2)), 4),
            "length": _length([r["ticks"] for r in e1 + e2]),
        }
    return out


# --- noise models for the paired per-seed difference d_s ------------------


def noise_models(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Empirical distributions of the per-seed paired difference.

    d_s is the candidate's mean over the two side assignments of seed s:
    match score in [-1, 1], or survivor differential divided by the team
    size (also in [-1, 1]). Two kinds of model:

    * ``pairing``: d_s for a non-mirror baseline pairing, centred on its
      mean and symmetrised (x and -x), so it describes the spread across
      seeds of two programs of known different strength.
    * ``mirror-independent``: for near-identical programs. A mirror's two
      assignments are the same match (d_s = 0 exactly), so this treats the
      two assignments as independent draws from the mirror's per-match
      distribution: d_s = (m1 + m2) / 2. An upper bound on the spread for
      programs that differ only slightly.

    Values are stored in integer units of 1/20 (exact for both scores).
    """
    import numpy as np

    models: dict[str, dict[str, Any]] = {}
    for x, y in pairings():
        e1 = sorted(
            (r for r in rows if r["x"] == x and r["y"] == y and r["assignment"] == "x_as_A"),
            key=lambda r: r["seed"],
        )
        e2 = sorted(
            (r for r in rows if r["x"] == x and r["y"] == y and r["assignment"] == "x_as_B"),
            key=lambda r: r["seed"],
        )
        for metric in ("score", "survivor"):

            def per_match(r: dict[str, Any], sign: int, metric: str = metric) -> int:
                if metric == "score":
                    return int(round(20 * sign * r["score"]))
                return 2 * sign * (r["a_alive"] - r["b_alive"])  # 20 * diff / 10 drones

            if x != y:
                d = [
                    (per_match(r1, 1) + per_match(r2, -1)) / 2
                    for r1, r2 in zip(e1, e2, strict=True)
                ]
                raw = np.asarray(d)
                centred = raw - raw.mean()
                values = np.concatenate([centred, -centred])
                kind = "pairing"
            else:
                m = np.asarray([per_match(r, 1) for r in e1], dtype=float)
                # Distribution of (m1 + m2) / 2 for iid m1, m2: the full outer sum.
                values = ((m[:, None] + m[None, :]) / 2).ravel()
                values = np.concatenate([values, -values])
                kind = "mirror-independent"
            uniq, counts = np.unique(values, return_counts=True)
            probs = counts / counts.sum()
            sd = float(np.sqrt((probs * uniq**2).sum())) / 20
            models[f"{metric}|{kind}|{x} vs {y}"] = {
                "metric": metric,
                "kind": kind,
                "pairing": f"{x} vs {y}",
                "support_units": uniq.tolist(),
                "probs": probs.tolist(),
                "sd": round(sd, 4),
                "share_zero": round(float(probs[uniq == 0].sum()), 4),
            }
    return models


def _tilt(support: Any, probs: Any, delta_units: float) -> Any | None:
    """Exponentially tilt a symmetric distribution to mean ``delta_units``.

    Keeps the support (scores stay in range). None when the target mean
    is outside the support."""
    import numpy as np

    if delta_units <= 0:
        return probs
    if support.max() <= delta_units:
        return None
    lo, hi = 0.0, 1.0
    s = support / 20.0

    def mean(theta: float) -> float:
        w = probs * np.exp(theta * (s - s.max()))
        return float((w * support).sum() / w.sum())

    while mean(hi) < delta_units:
        hi *= 2
        if hi > 1e6:
            return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if mean(mid) < delta_units:
            lo = mid
        else:
            hi = mid
    w = probs * np.exp(hi * (s - s.max()))
    return w / w.sum()


def simulate_power(model: dict[str, Any], seed: int) -> dict[str, Any]:
    """Power of the paired sign-flip test (one-sided, accept when the mean
    paired difference > 0 and p < POWER_ALPHA) by simulation."""
    import numpy as np

    support = np.asarray(model["support_units"], dtype=float)
    probs = np.asarray(model["probs"], dtype=float)
    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    for delta in (0.0, *POWER_DELTAS):
        tilted = _tilt(support, probs, 20 * delta)
        for n_matches in POWER_MATCHES:
            cell = f"{delta}|{n_matches}"
            if tilted is None or model["sd"] == 0:
                out[cell] = None
                continue
            n_seeds = n_matches // 2
            d = rng.choice(support, size=(POWER_SIMS, n_seeds), p=tilted)
            obs = d.sum(axis=1)
            signs = rng.choice((-1.0, 1.0), size=(n_seeds, POWER_FLIPS))
            flipped = np.abs(d) @ signs  # (sims, flips)
            p = (1 + (flipped >= obs[:, None] - 1e-9).sum(axis=1)) / (POWER_FLIPS + 1)
            accept = (obs > 0) & (p < POWER_ALPHA)
            out[cell] = round(float(accept.mean()), 4)
    return out


def analyze_power(rows: list[dict[str, Any]]) -> dict[str, Any]:
    models = noise_models(rows)
    power = {}
    for i, (name, model) in enumerate(sorted(models.items())):
        power[name] = simulate_power(model, seed=1000 + i)
    recommendation = {}
    for metric in ("score", "survivor"):
        usable = [n for n, m in models.items() if m["metric"] == metric and m["sd"] > 0]
        rec = None
        for n_matches in POWER_MATCHES:
            vals = [power[n][f"0.1|{n_matches}"] for n in usable]
            if vals and all(v is not None and v >= POWER_TARGET for v in vals):
                rec = n_matches
                break
        worst = max(usable, key=lambda n: models[n]["sd"]) if usable else None
        recommendation[metric] = {
            "rule": (
                f"smallest matches per evaluation in {list(POWER_MATCHES)} with power >= "
                f"{POWER_TARGET} at a true difference of 0.1 under every noise model with sd > 0"
            ),
            "matches_per_evaluation": rec,
            "largest_sd_model": worst,
        }
    return {
        "models": {
            k: {kk: vv for kk, vv in v.items() if kk not in ("support_units", "probs")}
            for k, v in models.items()
        },
        "power": power,
        "settings": {
            "matches": list(POWER_MATCHES),
            "deltas": list(POWER_DELTAS),
            "sims": POWER_SIMS,
            "flips": POWER_FLIPS,
            "alpha": POWER_ALPHA,
            "target_power": POWER_TARGET,
        },
        "recommendation": recommendation,
    }


def analyze_instruments(inst: dict[str, Any]) -> dict[str, Any]:
    per_program: dict[str, dict[str, Any]] = {}
    check_totals: dict[str, list[int]] = {}
    aar_failures = 0
    for m in inst["matches"]:
        for persp, p in m["perspectives"].items():
            if p["aar_rc"] != 0 or p["detector_rc"] != 0:
                aar_failures += 1
            for k, ok in p["aar_checks"].items():
                check_totals.setdefault(k, [0, 0])
                check_totals[k][0] += int(ok)
                check_totals[k][1] += 1
            prog = per_program.setdefault(
                p["program"], {"perspectives": 0, "fired": {t: 0 for t in td.TACTICS}, "probes": []}
            )
            prog["perspectives"] += 1
            for ev in p["detector_events"]:
                prog["fired"][ev["tactic"]] += 1
            pr = dict(p["probe"])
            pr["fired"] = sorted(ev["tactic"] for ev in p["detector_events"])
            pr["aar"] = p["aar"]
            pr["match"] = m["match"]
            pr["perspective"] = persp
            prog["probes"].append(pr)

    summary: dict[str, Any] = {}
    flags: list[dict[str, Any]] = []
    probe_agreement = True
    for name, d in sorted(per_program.items()):
        probes = d["probes"]

        def agg(key: str, probes: list[dict[str, Any]] = probes) -> dict[str, float] | None:
            vals = [p[key] for p in probes if p[key] is not None]
            if not vals:
                return None
            return {"min": min(vals), "max": max(vals), "mean": round(sum(vals) / len(vals), 4)}

        def agg_aar(key: str, probes: list[dict[str, Any]] = probes) -> dict[str, float] | None:
            vals = [p["aar"][key] for p in probes if p["aar"]]
            vals = [float(v) for v in vals]
            if not vals:
                return None
            return {"min": min(vals), "max": max(vals), "mean": round(sum(vals) / len(vals), 4)}

        for p in probes:
            expect = {
                "flanking": p["flanking_longest_streak"] >= td.FLANK_MIN_STREAK_TICKS,
                "kiting": p["kiting_longest_streak"] >= td.KITE_MIN_STREAK_TICKS,
                "focus_fire_discipline": p["focus_fire_min_window_ratio"] is not None
                and p["focus_fire_min_window_ratio"] <= td.FF_MAX_REDUNDANT_RATIO,
            }
            for tactic, should in expect.items():
                if should != (tactic in p["fired"]):
                    probe_agreement = False
        summary[name] = {
            "perspectives": d["perspectives"],
            "fire_rate": {t: round(c / d["perspectives"], 4) for t, c in d["fired"].items()},
            "flanking_longest_streak": agg("flanking_longest_streak"),
            "flanking_first_wide_tick": agg("flanking_first_wide_tick"),
            "kiting_longest_streak": agg("kiting_longest_streak"),
            "max_speed": agg("max_speed"),
            "focus_fire_attempts": agg("focus_fire_attempts"),
            "focus_fire_min_window_ratio": agg("focus_fire_min_window_ratio"),
            "message_nmi_detector_max": {
                "max": max(max(p["message_nmi_detector"]) for p in probes),
            },
            "message_nmi_observed_range_max": {
                "max": max(max(p["message_nmi_observed_range"]) for p in probes),
            },
            "message_channel_abs_max": max(
                max(abs(v) for rng in p["message_channel_range"] for v in rng) for p in probes
            ),
            "aar": {
                k: agg_aar(k)
                for k in (
                    "shots_fired_us",
                    "shots_hit_us",
                    "focus_fire_redundancy",
                    "cooldown_utilization_us",
                    "mean_pairwise_distance_us",
                    "dispersion_index_us",
                    "message_bus_entropy",
                    "kiting_score_them",
                    "engagement_range_mean",
                )
            },
        }
        s = summary[name]
        # --- implausibility rules -------------------------------------
        if s["max_speed"] and s["max_speed"]["max"] == 0:
            for tactic in ("flanking", "kiting"):
                if s["fire_rate"][tactic] > 0:
                    flags.append(
                        {
                            "program": name,
                            "tactic": tactic,
                            "rule": "fires for a program that never moves",
                            "fire_rate": s["fire_rate"][tactic],
                        }
                    )
        for tactic, rate in s["fire_rate"].items():
            if rate > 0:
                flags.append(
                    {
                        "program": name,
                        "tactic": tactic,
                        "rule": "fires on a human-written baseline (round-0 level, so not emergent)",
                        "fire_rate": rate,
                    }
                )
        if (
            s["fire_rate"]["flanking"] > 0
            and s["flanking_first_wide_tick"]
            and s["flanking_first_wide_tick"]["min"] == 0
        ):
            flags.append(
                {
                    "program": name,
                    "tactic": "flanking",
                    "rule": "the wide-angle condition already holds at spawn (tick 0)",
                    "fire_rate": s["fire_rate"]["flanking"],
                }
            )
        if (
            s["fire_rate"]["message_coded_targeting"] == 0
            and s["message_nmi_observed_range_max"]["max"] >= td.MSG_MIN_NMI
            and s["message_channel_abs_max"] > 1.0
        ):
            flags.append(
                {
                    "program": name,
                    "tactic": "message_coded_targeting",
                    "rule": (
                        "silent although a message channel carries the target (NMI over the "
                        "channel's observed range reaches the threshold); the detector clips "
                        "channels to [-1, 1] and this program's messages exceed that range"
                    ),
                    "nmi_detector_max": s["message_nmi_detector_max"]["max"],
                    "nmi_observed_range_max": s["message_nmi_observed_range_max"]["max"],
                    "message_channel_abs_max": s["message_channel_abs_max"],
                }
            )
    return {
        "n_matches": len(inst["matches"]),
        "n_perspectives": sum(len(m["perspectives"]) for m in inst["matches"]),
        "cli_failures": aar_failures,
        "aar_checks": {k: {"passed": v[0], "total": v[1]} for k, v in check_totals.items()},
        "aar_checks_all_pass": all(v[0] == v[1] for v in check_totals.values()),
        "probe_agrees_with_detector": probe_agreement,
        "per_program": summary,
        "flags": flags,
        "platform_labels": sorted({m["platform_label"] for m in inst["matches"]}),
    }


def analyze_limits(rows: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    import numpy as np

    cells = []
    for n in LIMITS_DRONES:
        for scale in LIMITS_SCALES:
            sel = [r for r in rows if r["drones"] == n and r["arena_scale"] == scale]
            moving = [
                r for r in sel if r["team_a"] != "stationary_v1" or r["team_b"] != "stationary_v1"
            ]
            ticks = np.asarray([r["ticks"] for r in moving])
            walls = np.asarray([r["wall_ms"] for r in sel])
            cells.append(
                {
                    "drones": n,
                    "arena_scale": scale,
                    "matches": len(sel),
                    "invalid": {
                        o: sum(r["outcome"] == o for r in sel) for o in fitness.INVALID_OUTCOMES
                    },
                    "draw_rate_excluding_stationary_mirror": round(
                        float(np.mean([r["outcome"] == "DRAW" for r in moving])), 4
                    ),
                    "share_at_max_ticks_excluding_stationary_mirror": round(
                        float((ticks >= MAX_TICKS).mean()), 4
                    ),
                    "median_ticks_excluding_stationary_mirror": float(np.median(ticks)),
                    "max_wall_ms": int(walls.max()),
                    "mean_wall_ms": round(float(walls.mean()), 2),
                }
            )
    return {
        "matches": len(rows),
        "invalid_total": sum(r["outcome"] in fitness.INVALID_OUTCOMES for r in rows),
        "cells": cells,
        "platform_labels": sorted({run["platform"]["label"] for run in runs}),
    }


def cmd_analyze(args: argparse.Namespace) -> None:
    import numpy as np

    rng = np.random.default_rng(20261022)
    cross_rows = _read_jsonl(OUT / "crossplay_matches.jsonl")
    cross_meta = json.loads((OUT / "crossplay_runs.json").read_text())
    inst = json.loads((OUT / "instruments.json").read_text())
    lim_rows = _read_jsonl(OUT / "limits_matches.jsonl")
    lim_meta = json.loads((OUT / "limits_runs.json").read_text())
    platforms = [r["platform"] for r in cross_meta["runs"]] + [
        r["platform"] for r in lim_meta["runs"]
    ]
    canonical = {
        k: platforms[0][k] for k in ("image_id", "compiler_version", "cpu_model", "kernel")
    }
    results = {
        "generated_utc": _now(),
        "git_sha": _git_sha(),
        "sources": [
            "crossplay_matches.jsonl",
            "crossplay_runs.json",
            "instruments.json",
            "limits_matches.jsonl",
            "limits_runs.json",
        ],
        "platform": {**canonical, "labels": sorted({p["label"] for p in platforms})},
        "platform_consistent": all({k: p[k] for k in canonical} == canonical for p in platforms),
        "crossplay": {
            "seeds": [CROSSPLAY_SEED_BASE, CROSSPLAY_SEED_BASE + CROSSPLAY_SEEDS - 1],
            "matches": len(cross_rows),
            "pairings": analyze_crossplay(cross_rows, rng),
        },
        "power": analyze_power(cross_rows),
        "instruments": analyze_instruments(inst),
        "limits": analyze_limits(lim_rows, lim_meta["runs"]),
    }
    (OUT / "calibration_results.json").write_text(
        json.dumps(results, indent=1, sort_keys=True) + "\n"
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _ci(ci: list[float]) -> str:
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


def render(res: dict[str, Any]) -> str:
    cp = res["crossplay"]
    pw = res["power"]
    ins = res["instruments"]
    lim = res["limits"]
    plat = res["platform"]
    L: list[str] = []
    a = L.append
    a("# Calibration: match noise, power, instruments and engine limits")
    a("")
    a(
        "Generated by `scripts/calibration.py render` from `calibration_results.json`, which "
        "`scripts/calibration.py analyze` computes from the raw outputs in this directory "
        f"({', '.join('`' + s + '`' for s in res['sources'])}). Analysis commit "
        f"`{res['git_sha'][:12]}`, generated {res['generated_utc']}."
    )
    a("")
    a(
        f"Every match ran in the eval container: image `{plat['image_id']}`, "
        f"{plat['compiler_version']}, {plat['cpu_model']}, kernel {plat['kernel']}. "
        f"Platform label(s): **{', '.join(plat['labels'])}**; one platform across all runs: "
        f"{'yes' if res['platform_consistent'] else '**no**'}. Baselines only, no LLM calls."
    )
    a("")

    # ---- 1. Cross-play
    a("## 1. Cross-play of the baselines")
    a("")
    a(
        f"{cp['matches']} matches: every pairing of pursuit_v1, cluster_v1 and stationary_v1 "
        f"(mirrors included), seeds {cp['seeds'][0]}-{cp['seeds'][1]}, each seed with both side "
        "assignments, 10 drones per side, arena scale 1.0, 1000-tick cap. Rates and scores are "
        "from the first-named program's view (score +1 win, -1 loss, 0 draw). Survivor "
        "differential = own survivors minus enemy survivors at the end. 95% CIs are percentile "
        f"bootstraps over seeds ({BOOTSTRAP} resamples). In a mirror both side assignments "
        "are the same match, so mirrors are reported once per seed from side A's view."
    )
    a("")
    a(
        "| Pairing | Matches | Win | Loss | Draw | Invalid | Mean score [95% CI] | Survivor diff [95% CI] | Ticks mean / median / p90 | At 1000 ticks |"
    )
    a("|---|---|---|---|---|---|---|---|---|---|")
    for key, p in cp["pairings"].items():
        rates = p["rates_side_A"] if p["mirror"] else p["rates_for_x"]
        score = p["mean_score_side_A"] if p["mirror"] else p["mean_score_x"]
        surv = p["survivor_diff_mean_side_A"] if p["mirror"] else p["survivor_diff_mean_x"]
        ln = p["length"]
        a(
            f"| {key}{' (mirror)' if p['mirror'] else ''} | {p['n_matches']} "
            f"| {_pct(rates.get('win', 0))} | {_pct(rates.get('loss', 0))} "
            f"| {_pct(rates.get('draw', 0))} | {_pct(p['invalid_rate'])} "
            f"| {score:+.3f} {_ci(p['mean_score_ci95'])} | {surv:+.2f} {_ci(p['survivor_diff_ci95'])} "
            f"| {ln['mean']} / {ln['median']:.0f} / {ln['p90']:.0f} | {_pct(ln['share_at_max_ticks'])} |"
        )
    a("")
    mirrors_ok = all(p["swap_rows_identical"] for p in cp["pairings"].values() if p["mirror"])
    a(
        "Side check: in every mirror the swapped assignment reproduced the same rows: "
        f"{'yes' if mirrors_ok else '**no**'}. Side effect in non-mirror pairings (mean score "
        "with the first-named program as A, then as B):"
    )
    a("")
    for key, p in cp["pairings"].items():
        if not p["mirror"]:
            a(f"- {key}: {p['mean_score_x_as_A']:+.3f} as A, {p['mean_score_x_as_B']:+.3f} as B")
    a("")

    # ---- 2. Noise and power
    a("## 2. Noise and power")
    a("")
    a(
        "Unit of analysis: the per-seed paired difference d, the candidate's mean over the two "
        "side assignments of one seed, so 2 matches per seed. Two scores: match score, and "
        "survivor differential divided by team size (10), so both lie in [-1, 1] and the true "
        "differences below are on the same scale. Noise models (from `noise_models`):"
    )
    a("")
    a(
        "- **pairing**: d across seeds for a non-mirror baseline pairing, centred and "
        "symmetrised: the seed-to-seed spread between two programs of different strength."
    )
    a(
        "- **mirror-independent**: a mirror's two assignments are the same match (d = 0), so "
        "this treats them as two independent draws from the mirror's per-match distribution, "
        "an upper bound on the spread for programs that differ only slightly."
    )
    a("")
    a("| Score | Model | Source pairing | SD of d | Share of d = 0 |")
    a("|---|---|---|---|---|")
    for _name, m in sorted(pw["models"].items()):
        a(
            f"| {m['metric']} | {m['kind']} | {m['pairing']} | {m['sd']:.4f} | {_pct(m['share_zero'])} |"
        )
    a("")
    s = pw["settings"]
    a(
        f"Power: probability that the acceptance rule (mean d > 0 and one-sided sign-flip "
        f"p < {s['alpha']}) accepts, from {s['sims']} simulated evaluations per cell, "
        f"{s['flips']} random sign flips each. A true difference is imposed by exponentially "
        "tilting the symmetric noise distribution to that mean, which keeps d inside [-1, 1]. "
        "The 0.0 column is the false-acceptance rate when there is no difference. Blank: the "
        "model has zero spread or cannot reach that mean."
    )
    a("")
    for metric in ("score", "survivor"):
        a(f"### {'Match score' if metric == 'score' else 'Survivor differential / 10'}")
        a("")
        header = "| Model | Matches | " + " | ".join(f"δ={d}" for d in (0.0, *s["deltas"])) + " |"
        a(header)
        a("|---" * (2 + 1 + len(s["deltas"])) + "|")
        for name in sorted(pw["power"]):
            m = pw["models"][name]
            if m["metric"] != metric:
                continue
            for n in s["matches"]:
                cells = []
                for d in (0.0, *s["deltas"]):
                    v = pw["power"][name][f"{d}|{n}"]
                    cells.append("" if v is None else f"{v:.2f}")
                a(f"| {m['kind']}: {m['pairing']} | {n} | " + " | ".join(cells) + " |")
        a("")
        rec = pw["recommendation"][metric]
        a(
            f"Rule: {rec['rule']}. Result: "
            + (
                f"**{rec['matches_per_evaluation']} matches**"
                if rec["matches_per_evaluation"]
                else "**none of the tested sizes**"
            )
            + f" (largest-spread model: {rec['largest_sd_model']})."
        )
        a("")

    # ---- 3. Instruments
    a("## 3. Instruments: AAR and tactic detector")
    a("")
    a(
        f"{ins['n_matches']} recorded matches (schema v2, `--record-actions`), seeds "
        f"{INSTRUMENT_SEED_BASE}-{INSTRUMENT_SEED_BASE + INSTRUMENT_MATCHES - 1}, match i on "
        "ordered pairing i mod 9 (mirrors included); traces in `traces/` (gzip). "
        "`scripts/telemetry_aar.py` and `scripts/tactic_detector.py scan` ran from their "
        f"CLIs on both perspectives of each trace ({ins['n_perspectives']} runs; CLI failures: "
        f"{ins['cli_failures']})."
    )
    a("")
    a(
        "AAR against the traces. `check_aar_against_trace` recomputes each quantity from the "
        "raw trace and the engine's own summary line, without telemetry_aar's code: outcome, "
        "ticks and survivors against the engine; every attack event against the engine's "
        "combat rules (attacker alive and off cooldown on the previous tick, distance from "
        "this tick's positions, hit iff in range and target alive, after a hit the target dead "
        "and the attacker's cooldown at 9, or 10 if it died the same tick); every death explained by a hit; then shot counts, cooldown utilisation, "
        "mean pairwise distance and message-bus use."
    )
    a("")
    a("| Check | Passed |")
    a("|---|---|")
    for k, v in ins["aar_checks"].items():
        a(f"| {k} | {v['passed']} / {v['total']} |")
    a("")
    a(f"All AAR checks pass: **{'yes' if ins['aar_checks_all_pass'] else 'no'}**.")
    a("")
    a(
        "Detector values per baseline (the program on the perspective side). Fire rate = share "
        "of perspectives in which the detector fired. Because the detectors only report when "
        "they fire, `probe_detectors` recomputes the underlying quantities with the detectors' "
        "own helper functions and per-tick rules; probe and detector agree on every firing "
        f"decision: {'yes' if ins['probe_agrees_with_detector'] else '**no**'}."
    )
    a("")
    a("| Baseline | Runs | Flanking | Kiting | Focus-fire discipline | Message-coded targeting |")
    a("|---|---|---|---|---|---|")
    for prog, d in ins["per_program"].items():
        fr = d["fire_rate"]
        a(
            f"| {prog} | {d['perspectives']} | {_pct(fr['flanking'])} | {_pct(fr['kiting'])} "
            f"| {_pct(fr['focus_fire_discipline'])} | {_pct(fr['message_coded_targeting'])} |"
        )
    a("")

    def mm(v: dict[str, float] | None, fmt: str = "{:.0f}") -> str:
        if v is None:
            return "-"
        return f"{fmt.format(v['min'])}-{fmt.format(v['max'])} (mean {fmt.format(v['mean'])})"

    a(
        "| Baseline | Flanking: longest ≥90° streak (ticks) | Flanking: first ≥90° tick | Kiting: longest streak | "
        "Max speed | Shots at targets | Focus fire: lowest redundant share (200-shot window) "
        "| Message NMI, detector | Message NMI, observed range | Largest message value |"
    )
    a("|---|---|---|---|---|---|---|---|---|---|")
    for prog, d in ins["per_program"].items():
        a(
            f"| {prog} | {mm(d['flanking_longest_streak'])} | {mm(d['flanking_first_wide_tick'])} "
            f"| {mm(d['kiting_longest_streak'])} | {mm(d['max_speed'], '{:.2f}')} "
            f"| {mm(d['focus_fire_attempts'])} | {mm(d['focus_fire_min_window_ratio'], '{:.3f}')} "
            f"| {d['message_nmi_detector_max']['max']:.3f} | {d['message_nmi_observed_range_max']['max']:.3f} "
            f"| {d['message_channel_abs_max']:.1f} |"
        )
    a("")
    a("AAR values per baseline (min-max, mean over its perspectives):")
    a("")
    keys = list(next(iter(ins["per_program"].values()))["aar"].keys())
    a("| Baseline | " + " | ".join(keys) + " |")
    a("|---" * (1 + len(keys)) + "|")
    for prog, d in ins["per_program"].items():
        a(f"| {prog} | " + " | ".join(mm(d["aar"][k], "{:.3g}") for k in keys) + " |")
    a("")
    a("### Flags")
    a("")
    if not ins["flags"]:
        a("None.")
    for f in ins["flags"]:
        extra = ", ".join(
            f"{k} {v}" for k, v in f.items() if k not in ("program", "tactic", "rule")
        )
        a(f"- **{f['tactic']}** on {f['program']}: {f['rule']} ({extra}).")
    a("")

    # ---- 4. Limits
    a("## 4. Engine limits for E4")
    a("")
    a(
        f"{lim['matches']} matches: all 9 ordered baseline pairings (mirrors included), "
        f"{LIMITS_SEEDS} seeds each (seeds {LIMITS_SEED_BASE}-{LIMITS_SEED_BASE + LIMITS_SEEDS - 1}), at "
        f"{', '.join(str(n) for n in LIMITS_DRONES)} drones per team and arena scales "
        f"{', '.join(str(s) for s in LIMITS_SCALES)}; 1000-tick cap, 10 s per-match timeout. "
        f"Invalid matches (guard, compile, crash, timeout): **{lim['invalid_total']}**. "
        f"Platform label(s): {', '.join(lim['platform_labels'])}. Draw and tick columns leave out "
        "the stationary mirror, which can only end at the cap."
    )
    a("")
    a(
        "| Drones | Arena scale | Matches | Invalid | Draw rate | At 1000 ticks | Median ticks | Mean / max match wall (ms) |"
    )
    a("|---|---|---|---|---|---|---|---|")
    for c in lim["cells"]:
        inv = sum(c["invalid"].values())
        a(
            f"| {c['drones']} | {c['arena_scale']} | {c['matches']} | {inv} "
            f"| {_pct(c['draw_rate_excluding_stationary_mirror'])} "
            f"| {_pct(c['share_at_max_ticks_excluding_stationary_mirror'])} "
            f"| {c['median_ticks_excluding_stationary_mirror']:.0f} | {c['mean_wall_ms']} / {c['max_wall_ms']} |"
        )
    a("")
    return "\n".join(L) + "\n"


def cmd_render(args: argparse.Namespace) -> None:
    res = json.loads((OUT / "calibration_results.json").read_text())
    (OUT / "CALIBRATION.md").write_text(render(res))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("step", choices=["crossplay", "instruments", "limits", "analyze", "render"])
    args = p.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.pop(fitness.BACKEND_ENV, None)  # every match in the container
    {
        "crossplay": cmd_crossplay,
        "instruments": cmd_instruments,
        "limits": cmd_limits,
        "analyze": cmd_analyze,
        "render": cmd_render,
    }[args.step](args)
    print(f"calibration {args.step}: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
