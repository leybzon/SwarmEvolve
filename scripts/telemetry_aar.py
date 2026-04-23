#!/usr/bin/env python3
"""telemetry_aar.py — After-Action Report generator (M15b).

Translates a schema-v2 trace (M15a, `--record-actions`) into a compact,
perspective-aware report that an LLM can condition its next mutation on.
All metrics are deterministic functions of the trace: no sampling, no
inference, no smoothing.

Two surfaces are exposed:

  1. CLI entry point. Writes markdown to stdout and (optionally) a
     structured JSON sidecar next to the trace.

  2. Python API — ``render_aar(trace_path, *, perspective, fmt="both")``
     returns an ``AARReport`` dataclass with ``structured``, ``markdown``,
     and ``token_estimate`` fields. Importable without side effects (no
     globals populated at import time).

Design commitments:
  * Input validation is strict: a v1 trace (no `attacks` / `actions_*`)
    aborts with a readable error pointing to ``--record-actions``.
  * Every metric has a single-line formula comment so the M15b test
    fixture can assert exact values.
  * Output is stable: field order is fixed, floats use ``%.4f``,
    markdown headings are immutable. This makes journal-entry
    metric-citation validation (M15c) a pure dict-equality check.

See docs/NEXT_PHASE_PLAN.md §M15b for the authoritative spec.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# SPECIFICATION defaults. We read them from the trace only if present; the
# v1/v2 trace does not serialise `disable_range`, so we fall back to the
# documented spec value. `engagement_range_mean` and `kiting_score_them`
# reference this.
DEFAULT_DISABLE_RANGE = 50.0

# Metric schema version. Bump whenever metric names or formulas change
# so journal entries' `aar_metrics_cited` dict validations can detect
# stale AAR output.
METRICS_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AARReport:
    """Container bundling the three output surfaces."""
    structured: dict[str, Any]
    markdown: str
    token_estimate: int


# ---------------------------------------------------------------------------
# Trace loading / validation
# ---------------------------------------------------------------------------

def load_v2_trace(path: Path) -> list[dict[str, Any]]:
    """Load a schema-v2 trace into memory and validate its presence.

    Returns the full list of tick records. Raises ValueError with a
    human-readable message if the file is empty, malformed, or v1-only.
    """
    lines = path.read_text().splitlines()
    if not lines:
        raise ValueError(f"empty trace: {path}")
    records: list[dict[str, Any]] = []
    for i, raw in enumerate(lines):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i}: invalid JSON ({e.msg})") from e
        records.append(obj)
    # Validate that this is a v2 trace. The first line must carry
    # schema_version: 2 or contain the actions_a/actions_b/attacks
    # fields introduced in M15a. Otherwise the caller almost certainly
    # forgot `--record-actions` when generating the trace.
    first = records[0]
    has_v2_fields = all(
        k in first for k in ("actions_a", "actions_b", "attacks")
    )
    if not has_v2_fields:
        raise ValueError(
            f"{path}: v1 trace detected (missing actions_a/actions_b/attacks). "
            "Re-run the engine with --record-actions to generate a v2 trace."
        )
    return records


# ---------------------------------------------------------------------------
# Metric computations
# ---------------------------------------------------------------------------

def _team_key(team_id: int) -> str:
    """0 -> 'a', 1 -> 'b'. We use lowercase to keep JSON keys uniform."""
    return "a" if team_id == 0 else "b"


def _perspective_to_teams(perspective: str) -> tuple[int, int]:
    """'A' -> (0,1) ('us'=A, 'them'=B); 'B' -> (1,0)."""
    p = perspective.upper()
    if p == "A":
        return 0, 1
    if p == "B":
        return 1, 0
    raise ValueError(f"perspective must be 'A' or 'B', got {perspective!r}")


def _count_alive(team: list[dict[str, Any]]) -> int:
    return sum(1 for d in team if d.get("alive", False))


def _mean_pairwise_distance(team: list[dict[str, Any]]) -> float:
    """Mean Euclidean distance between every pair of alive drones.

    Formula: sum over unordered pairs of sqrt((dx)^2+(dy)^2), divided by
    the number of pairs. Returns 0.0 if fewer than 2 alive drones (no
    pairs exist; treating as zero disperses edge cases consistently).
    """
    alive = [(d["x"], d["y"]) for d in team if d["alive"]]
    n = len(alive)
    if n < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = alive[i][0] - alive[j][0]
            dy = alive[i][1] - alive[j][1]
            total += math.sqrt(dx * dx + dy * dy)
            pairs += 1
    return total / pairs


def _cooldown_utilization(team_snapshots: list[list[dict[str, Any]]]) -> float:
    """Fraction of (alive-drone * tick) cells with cooldown == 0.

    Dead drones do not count toward the denominator.
    """
    num_ready = 0
    denom = 0
    for team in team_snapshots:
        for d in team:
            if d["alive"]:
                denom += 1
                if d["cooldown"] == 0:
                    num_ready += 1
    if denom == 0:
        return 0.0
    return num_ready / denom


def _message_bus_stats(
    action_snapshots: list[list[dict[str, Any]]],
) -> tuple[bool, float]:
    """Return (any_nonzero, entropy).

    ``any_nonzero`` — True iff any alive drone emitted any non-zero
    message component anywhere in the match.

    ``entropy`` — Shannon entropy (bits) of the empirical distribution
    of quantized message floats. Quantization: 8 uniform buckets across
    the observed range [min, max] of each component. Joint distribution
    across the 4 components is computed per-tick per-drone as a tuple.
    Entropy is base-2. Returns 0.0 if all messages are zero.
    """
    # Gather all alive-drone messages, flattened.
    samples: list[tuple[float, float, float, float]] = []
    for actions in action_snapshots:
        for a in actions:
            if a["alive"]:
                msg = a["msg"]
                samples.append((msg[0], msg[1], msg[2], msg[3]))
    if not samples:
        return False, 0.0
    any_nonzero = any(any(v != 0.0 for v in s) for s in samples)
    if not any_nonzero:
        return False, 0.0
    # Quantize each axis independently into 8 bins.
    n_bins = 8
    axes = list(zip(*samples))  # 4 tuples, one per component
    bucketed: list[list[int]] = []
    for ax in axes:
        lo, hi = min(ax), max(ax)
        if hi <= lo:
            bucketed.append([0] * len(ax))
            continue
        span = hi - lo
        bucketed.append([
            min(n_bins - 1, int((v - lo) / span * n_bins))
            for v in ax
        ])
    # Build joint-tuple frequency distribution.
    joint_counts: dict[tuple[int, int, int, int], int] = {}
    total = len(samples)
    for i in range(total):
        key = (bucketed[0][i], bucketed[1][i], bucketed[2][i], bucketed[3][i])
        joint_counts[key] = joint_counts.get(key, 0) + 1
    entropy = 0.0
    for c in joint_counts.values():
        p = c / total
        entropy -= p * math.log2(p)
    return True, entropy


# ---------------------------------------------------------------------------
# Top-level metric assembly
# ---------------------------------------------------------------------------

def compute_metrics(
    records: list[dict[str, Any]],
    perspective: str,
    disable_range: float = DEFAULT_DISABLE_RANGE,
) -> dict[str, Any]:
    """Compute the full AAR metric dict from a v2 trace."""
    us, them = _perspective_to_teams(perspective)
    us_key, them_key = _team_key(us), _team_key(them)

    last = records[-1]
    outcome = last.get("outcome", "UNKNOWN")
    ticks = last["tick"]

    us_team_final = last[f"team_{us_key}"]
    them_team_final = last[f"team_{them_key}"]
    alive_final_us = _count_alive(us_team_final)
    alive_final_them = _count_alive(them_team_final)

    # Attack-event aggregates. Tick 0 has no events; ticks 1..T each carry
    # one attacks[] array. Attempts by dead-but-just-killed attackers are
    # still counted — the M15a engine captures resolution-time snapshots.
    shots_fired_us = 0
    shots_fired_them = 0
    shots_hit_us = 0
    shots_hit_them = 0
    engagement_dists_hit = []
    kiting_from_them = 0  # attempts by `them` at dist > 0.8*range
    for rec in records:
        for ev in rec.get("attacks", []):
            if ev["atk_team"] == us:
                shots_fired_us += 1
                if ev["hit"]:
                    shots_hit_us += 1
                    engagement_dists_hit.append(ev["dist"])
            else:
                shots_fired_them += 1
                if ev["hit"]:
                    shots_hit_them += 1
                if ev["dist"] > 0.8 * disable_range:
                    kiting_from_them += 1
    kiting_score_them = (
        kiting_from_them / shots_fired_them if shots_fired_them > 0 else 0.0
    )
    engagement_range_mean = (
        sum(engagement_dists_hit) / len(engagement_dists_hit)
        if engagement_dists_hit
        else 0.0
    )

    # Focus-fire redundancy: number of "extra" shots (beyond the first) at
    # the same (tick, target_team, target_id) triple. Higher means more
    # cooldowns were spent on an already-doomed target.
    focus_fire_extras = 0
    focus_fire_attempts = 0
    for rec in records:
        tick = rec["tick"]
        by_target: dict[tuple[int, int, int], int] = {}
        for ev in rec.get("attacks", []):
            if ev["atk_team"] != us:
                continue
            key = (tick, ev["tgt_team"], ev["tgt_id"])
            by_target[key] = by_target.get(key, 0) + 1
        for count in by_target.values():
            focus_fire_attempts += count
            focus_fire_extras += max(0, count - 1)
    focus_fire_redundancy = (
        focus_fire_extras / focus_fire_attempts
        if focus_fire_attempts > 0
        else 0.0
    )

    # Formation / dispersion: mean-of-means over ticks where team is alive.
    us_dists = []
    them_dists = []
    for rec in records:
        us_dists.append(_mean_pairwise_distance(rec[f"team_{us_key}"]))
        them_dists.append(_mean_pairwise_distance(rec[f"team_{them_key}"]))
    mean_pd_us = sum(us_dists) / len(us_dists) if us_dists else 0.0
    mean_pd_them = sum(them_dists) / len(them_dists) if them_dists else 0.0
    # Dispersion index: stddev / mean. 0 means perfectly steady formation
    # distance across the match; large values mean the team spread and
    # regrouped repeatedly.
    def _stddev(xs: list[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        var = sum((x - m) ** 2 for x in xs) / len(xs)
        return math.sqrt(var)
    disp_us = (_stddev(us_dists) / mean_pd_us) if mean_pd_us > 0 else 0.0
    disp_them = (_stddev(them_dists) / mean_pd_them) if mean_pd_them > 0 else 0.0

    # Cooldown utilization — fraction of alive-ticks with cooldown==0.
    cd_util_us = _cooldown_utilization(
        [rec[f"team_{us_key}"] for rec in records]
    )
    cd_util_them = _cooldown_utilization(
        [rec[f"team_{them_key}"] for rec in records]
    )

    # Message-bus signals. Limited to our own team because we only have
    # access to outgoing messages for our team in the perspective model.
    us_actions_snapshots = [rec[f"actions_{us_key}"] for rec in records]
    msg_used, msg_entropy = _message_bus_stats(us_actions_snapshots)

    structured = {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "perspective": perspective.upper(),
        "outcome": outcome,
        "ticks": ticks,
        "alive_final_us": alive_final_us,
        "alive_final_them": alive_final_them,
        "shots_fired_us": shots_fired_us,
        "shots_fired_them": shots_fired_them,
        "shots_hit_us": shots_hit_us,
        "shots_hit_them": shots_hit_them,
        "focus_fire_redundancy": round(focus_fire_redundancy, 4),
        "cooldown_utilization_us": round(cd_util_us, 4),
        "cooldown_utilization_them": round(cd_util_them, 4),
        "mean_pairwise_distance_us": round(mean_pd_us, 4),
        "mean_pairwise_distance_them": round(mean_pd_them, 4),
        "dispersion_index_us": round(disp_us, 4),
        "dispersion_index_them": round(disp_them, 4),
        "message_bus_used": msg_used,
        "message_bus_entropy": round(msg_entropy, 4),
        "kiting_score_them": round(kiting_score_them, 4),
        "engagement_range_mean": round(engagement_range_mean, 4),
    }
    return structured


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(m: dict[str, Any]) -> str:
    """Deterministic markdown renderer. Field order is fixed.

    The LLM sees this directly; keep it concise and single-scan.
    """
    hit_rate_us = (
        m["shots_hit_us"] / m["shots_fired_us"]
        if m["shots_fired_us"] > 0 else 0.0
    )
    hit_rate_them = (
        m["shots_hit_them"] / m["shots_fired_them"]
        if m["shots_fired_them"] > 0 else 0.0
    )
    lines = [
        f"# After-Action Report (perspective: Team {m['perspective']})",
        "",
        f"- **Outcome**: {m['outcome']} after {m['ticks']} ticks",
        f"- **Survivors**: us={m['alive_final_us']} | them={m['alive_final_them']}",
        "",
        "## Combat efficiency",
        f"- Shots fired: us={m['shots_fired_us']} | them={m['shots_fired_them']}",
        f"- Hit rate: us={hit_rate_us:.2%} | them={hit_rate_them:.2%}",
        f"- Focus-fire redundancy (our team): {m['focus_fire_redundancy']:.2%}  "
        "(extra shots at already-targeted enemies)",
        f"- Cooldown utilization: us={m['cooldown_utilization_us']:.2%} | "
        f"them={m['cooldown_utilization_them']:.2%}  "
        "(fraction of alive-ticks at cooldown 0)",
        f"- Engagement range (hits, mean): {m['engagement_range_mean']:.2f} units",
        f"- Opponent kiting score: {m['kiting_score_them']:.2%}  "
        "(their shots from >80% of disable range)",
        "",
        "## Formation & coordination",
        f"- Mean pairwise distance: us={m['mean_pairwise_distance_us']:.2f} | "
        f"them={m['mean_pairwise_distance_them']:.2f}",
        f"- Dispersion index: us={m['dispersion_index_us']:.3f} | "
        f"them={m['dispersion_index_them']:.3f}  (std/mean over ticks)",
        f"- Message bus: "
        + (
            f"active, entropy={m['message_bus_entropy']:.2f} bits"
            if m["message_bus_used"]
            else "unused (all zeros)"
        ),
        "",
    ]
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token estimate for budgeting AAR into LLM prompts.

    Uses the 4-chars-per-token heuristic. Tests (M15b acceptance #3)
    bound the error at 15 % vs a tiktoken oracle for n_drones=10 traces.
    """
    return max(1, (len(text) + 3) // 4)


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------

def render_aar(
    trace_path: Path,
    *,
    perspective: str,
    fmt: str = "both",
    disable_range: float = DEFAULT_DISABLE_RANGE,
) -> AARReport:
    """Public API. Reads a v2 trace and returns an AARReport."""
    records = load_v2_trace(trace_path)
    structured = compute_metrics(records, perspective, disable_range)
    markdown = render_markdown(structured) if fmt in ("markdown", "both", "json") else ""
    if fmt == "json":
        markdown_out = ""
    else:
        markdown_out = markdown
    return AARReport(
        structured=structured,
        markdown=markdown_out,
        token_estimate=estimate_tokens(markdown),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate an After-Action Report from a schema-v2 trace."
    )
    p.add_argument("--trace", required=True, type=Path,
                   help="Path to schema-v2 trace (produced with --record-actions).")
    p.add_argument("--perspective", required=True, choices=["A", "B"],
                   help="Which team is 'us' in the report.")
    p.add_argument("--format", choices=["markdown", "json", "both"], default="both",
                   help="Output format (default: both).")
    p.add_argument("--json-out", type=Path, default=None,
                   help="Write structured JSON sidecar to PATH. "
                        "Defaults to <trace>.aar.json when --format=both.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        report = render_aar(args.trace, perspective=args.perspective, fmt=args.format)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.format in ("markdown", "both"):
        sys.stdout.write(report.markdown)
    if args.format in ("json", "both"):
        sidecar = args.json_out or args.trace.with_suffix(args.trace.suffix + ".aar.json")
        sidecar.write_text(json.dumps(report.structured, indent=2, sort_keys=True) + "\n")
        if args.format == "json":
            # Print sidecar path so callers can chain downstream tools.
            print(str(sidecar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
