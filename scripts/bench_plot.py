#!/usr/bin/env python3
"""M13 benchmark plotter — render the three canonical charts from bench_results.json.

Outputs (under ``<out_dir>/plots/``):
    wall_ms_vs_N.png     -- log-log wall-time per match vs N, all backends overlaid
    per_tick_us_vs_N.png -- median µs per tick vs N, all backends
    speedup_vs_N.png     -- GPU divided by the fastest CPU backend at each N

Design notes
------------
* matplotlib is an *optional* dependency. When it is missing, the public
  ``render_all`` entry point returns a status dict with ``matplotlib: False``
  and writes no files. The bench_gpu driver calls us best-effort, so failing
  silently is the right behaviour here.
* Only backends that actually have summary rows are plotted. Empty series
  are skipped rather than drawn as a gap.
* The source of truth is always ``bench_results.json``'s ``summary`` array,
  which is byte-stable (sort_keys) and already grouped (backend, n).

CLI
---
``python3 -m scripts.bench_plot --out data/bench`` is equivalent to calling
``render_all(Path("data/bench"))`` directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING_INPUT = 41

# Stable plotting order and colour-agnostic line styles. Keeping the legend
# in a fixed order also makes visual diffs between report versions easier.
BACKEND_ORDER = ("cpu1", "cpu_omp", "gpu")
BACKEND_LABEL = {
    "cpu1": "CPU (single-thread)",
    "cpu_omp": "CPU (OpenMP)",
    "gpu": "GPU (OpenACC)",
}
BACKEND_MARKER = {"cpu1": "o", "cpu_omp": "s", "gpu": "^"}


def _mpl():
    """Return (plt, np) or (None, None) if matplotlib / numpy missing."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        import numpy as np

        return plt, np
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_results(out_dir: Path) -> dict[str, Any]:
    """Load ``bench_results.json`` from ``out_dir``.

    Raises FileNotFoundError if the file is missing — the caller (bench_gpu
    driver) wraps us in try/except so this is handled without crashing the
    whole benchmark pipeline.
    """
    path = out_dir / "bench_results.json"
    if not path.is_file():
        raise FileNotFoundError(f"bench_results.json not found at {path}")
    return json.loads(path.read_text())


def _series_by_backend(summary: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group summary rows by backend, sorted by N ascending within each group."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in summary:
        out.setdefault(row["backend"], []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["n"])
    return out


# ---------------------------------------------------------------------------
# Individual plots
# ---------------------------------------------------------------------------


def plot_wall_ms_vs_n(summary: list[dict[str, Any]], out_png: Path) -> bool:
    """Wall time per match (median over repeats), log-log axes."""
    plt, _ = _mpl()
    if plt is None:
        return False
    series = _series_by_backend(summary)
    if not series:
        return False

    fig, ax = plt.subplots(figsize=(7.5, 5))
    plotted = False
    for backend in BACKEND_ORDER:
        rows = series.get(backend)
        if not rows:
            continue
        xs = [r["n"] for r in rows]
        ys = [r["wall_ms_median"] for r in rows]
        # IQR band (p25 / p75) drawn as a light error bar where available.
        y_lo = [r["wall_ms_p25"] for r in rows]
        y_hi = [r["wall_ms_p75"] for r in rows]
        err_lo = [y - lo for y, lo in zip(ys, y_lo)]
        err_hi = [hi - y for y, hi in zip(ys, y_hi)]
        ax.errorbar(
            xs,
            ys,
            yerr=[err_lo, err_hi],
            marker=BACKEND_MARKER.get(backend, "o"),
            label=BACKEND_LABEL.get(backend, backend),
            capsize=3,
            linewidth=1.4,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("N (drones per team)")
    ax.set_ylabel("Wall time per match (ms, median)")
    ax.set_title("SwarmEvolve M13 — wall time vs swarm size")
    ax.grid(which="both", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def plot_per_tick_us_vs_n(summary: list[dict[str, Any]], out_png: Path) -> bool:
    """Per-tick microseconds, log-log axes."""
    plt, _ = _mpl()
    if plt is None:
        return False
    series = _series_by_backend(summary)
    if not series:
        return False

    fig, ax = plt.subplots(figsize=(7.5, 5))
    plotted = False
    for backend in BACKEND_ORDER:
        rows = series.get(backend)
        if not rows:
            continue
        xs = [r["n"] for r in rows]
        ys = [r["per_tick_us_median"] for r in rows]
        ax.plot(
            xs,
            ys,
            marker=BACKEND_MARKER.get(backend, "o"),
            label=BACKEND_LABEL.get(backend, backend),
            linewidth=1.4,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("N (drones per team)")
    ax.set_ylabel("Tick latency (µs, median)")
    ax.set_title("SwarmEvolve M13 — per-tick cost vs swarm size")
    ax.grid(which="both", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def plot_speedup_vs_n(summary: list[dict[str, Any]], out_png: Path) -> bool:
    """Speedup of GPU relative to the fastest CPU backend at each N.

    Denominator at each N is ``min(wall_ms_median_cpu1, wall_ms_median_cpu_omp)``
    over whichever CPU backends are present. If no CPU data is available the
    plot is skipped; if no GPU data is available we draw the CPU-vs-CPU ratio
    as a fallback so the reader still gets *some* scaling picture.
    """
    plt, _ = _mpl()
    if plt is None:
        return False
    series = _series_by_backend(summary)
    if not series:
        return False

    # Build n -> {backend: wall_ms_median}
    table: dict[int, dict[str, float]] = {}
    for backend, rows in series.items():
        for r in rows:
            table.setdefault(r["n"], {})[backend] = r["wall_ms_median"]

    ns = sorted(table.keys())
    if not ns:
        return False

    fig, ax = plt.subplots(figsize=(7.5, 5))
    plotted = False

    # Primary: GPU speedup over best CPU.
    xs: list[int] = []
    ys: list[float] = []
    for n in ns:
        cell = table[n]
        cpu_values = [cell[b] for b in ("cpu1", "cpu_omp") if b in cell]
        if "gpu" not in cell or not cpu_values:
            continue
        best_cpu = min(cpu_values)
        if cell["gpu"] <= 0:
            continue
        xs.append(n)
        ys.append(best_cpu / cell["gpu"])
    if xs:
        ax.plot(xs, ys, marker="^", linewidth=1.6, label="GPU speedup vs best CPU")
        plotted = True

    # Fallback secondary line: OpenMP speedup over single-thread CPU. Useful
    # even without a GPU in the run.
    xs2: list[int] = []
    ys2: list[float] = []
    for n in ns:
        cell = table[n]
        if "cpu1" in cell and "cpu_omp" in cell and cell["cpu_omp"] > 0:
            xs2.append(n)
            ys2.append(cell["cpu1"] / cell["cpu_omp"])
    if xs2:
        ax.plot(xs2, ys2, marker="s", linewidth=1.2, label="OpenMP speedup vs CPU1")
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.axhline(1.0, color="grey", linewidth=0.8, linestyle="--", label="1× (parity)")
    ax.set_xscale("log")
    ax.set_xlabel("N (drones per team)")
    ax.set_ylabel("Speedup (×, higher is better)")
    ax.set_title("SwarmEvolve M13 — speedup vs swarm size")
    ax.grid(which="both", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Public entry point (called by scripts/bench_gpu.py)
# ---------------------------------------------------------------------------


def render_all(out_dir: Path) -> dict[str, Any]:
    """Render every plot into ``out_dir/plots/``.

    Returns a status dict enumerating which plots were produced. Does NOT
    raise if matplotlib is missing or the summary is empty — callers treat
    plotting as best-effort.
    """
    status: dict[str, Any] = {
        "matplotlib": False,
        "plots": {},
        "out_dir": str(out_dir),
    }
    plt, _ = _mpl()
    if plt is None:
        status["reason"] = "matplotlib not installed"
        return status
    status["matplotlib"] = True

    try:
        data = load_results(out_dir)
    except FileNotFoundError as exc:
        status["reason"] = str(exc)
        return status

    summary = data.get("summary", [])
    if not summary:
        status["reason"] = "summary is empty"
        return status

    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    status["plots"]["wall_ms_vs_N.png"] = plot_wall_ms_vs_n(
        summary, plots_dir / "wall_ms_vs_N.png"
    )
    status["plots"]["per_tick_us_vs_N.png"] = plot_per_tick_us_vs_n(
        summary, plots_dir / "per_tick_us_vs_N.png"
    )
    status["plots"]["speedup_vs_N.png"] = plot_speedup_vs_n(
        summary, plots_dir / "speedup_vs_N.png"
    )
    return status


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Render M13 benchmark plots from bench_results.json.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("data/bench"),
        help="Directory containing bench_results.json (plots written to <out>/plots/).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    status = render_all(args.out)
    if not status.get("matplotlib"):
        print(f"bench_plot.py: {status.get('reason', 'no plots rendered')}", file=sys.stderr)
        return EXIT_MISSING_INPUT
    if not status.get("plots"):
        print(f"bench_plot.py: {status.get('reason', 'no plots rendered')}", file=sys.stderr)
        return EXIT_MISSING_INPUT
    for name, ok in status["plots"].items():
        print(f"bench_plot.py: {name} -> {'ok' if ok else 'skipped'}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
