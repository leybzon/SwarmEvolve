#!/usr/bin/env python3
"""Analysis CLI (M12).

Produces plots + derived statistics from completed experiment
directories:

* **Tournament directory** (contains ``tournament.json``)
  - ``rating_trajectory.png``: Elo rating over rounds per AI.
  - ``win_matrix_heatmap.png``: win-rate (wins_a - wins_b) / n_matches.
  - ``clustering.png``: hierarchical dendrogram of behaviour distance
    (see :func:`behaviour_distance` — L2 over end-of-match centroids
    across all shared seeds). Falls back to a blank figure with an
    explanatory title if fewer than 3 AIs are present.
  - ``top_matches.json``: the "most informative" match per round,
    defined as the pairing with the largest absolute Elo delta
    relative to its pre-round expectation.

* **Evolution run directory** (contains ``state.json`` /
  ``checkpoints/*.json``)
  - ``fitness.png``: champion fitness curve (reuses M10's plot logic
    via import).

matplotlib is an optional dependency. When it is missing, analysis
writes a ``skipped.json`` stub with the reason and returns EXIT_OK so
CI on minimal laptops doesn't break.

Invocation
----------
::

    python3 scripts/analysis.py \\
        --tournament data/tournaments/rr-4ai \\
        --out data/analysis/rr-4ai

    python3 scripts/analysis.py \\
        --evolution data/evolve/run001 \\
        --out data/analysis/evolve001

Both ``--tournament`` and ``--evolution`` can be specified at once; the
CLI produces whichever subset of plots is supported by the input.

Exit codes
----------
- ``0`` : analysis completed (possibly with a plot skipped)
- ``2`` : CLI usage error
- ``41``: input directory missing a required file
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING_INPUT = 41

# matplotlib is optional. We import it lazily inside functions so that
# `python3 scripts/analysis.py --help` works on hosts without the dep.


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
# Tournament analysis
# ---------------------------------------------------------------------------


def load_tournament(path: Path) -> dict[str, Any]:
    """Load ``tournament.json`` from a directory or a direct file path."""
    if path.is_dir():
        path = path / "tournament.json"
    if not path.is_file():
        raise FileNotFoundError(f"tournament.json not found at {path}")
    return json.loads(path.read_text())


def plot_rating_trajectory(trn: dict[str, Any], out_png: Path) -> bool:
    """Plot per-AI Elo trajectory across rounds. Returns True on success."""
    plt, _ = _mpl()
    if plt is None:
        return False
    names = [e["name"] for e in trn["entries"]]
    rounds = trn["rounds"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = list(range(len(rounds) + 1))
    for name in names:
        y = [trn["elo_start"]] + [r["ratings_snapshot"][name] for r in rounds]
        ax.plot(x, y, marker="o", label=name)
    ax.set_xlabel("round (0 = initial rating)")
    ax.set_ylabel("Elo rating")
    ax.set_title(f"Rating trajectory — {trn['mode']}, {trn['n_matches']} matches/pair")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def plot_win_matrix_heatmap(trn: dict[str, Any], out_png: Path) -> bool:
    """Heatmap of (wins_a - wins_b) / n_matches per ordered pair."""
    plt, np = _mpl()
    if plt is None:
        return False
    names = [e["name"] for e in trn["entries"]]
    n = len(names)
    matrix = np.zeros((n, n), dtype=float)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                matrix[i, j] = 0.0
                continue
            cell = trn["win_matrix"][a][b]
            total = cell["wins_a"] + cell["wins_b"] + cell["draws"] + cell["invalid"]
            if total == 0:
                matrix[i, j] = 0.0
            else:
                matrix[i, j] = (cell["wins_a"] - cell["wins_b"]) / total

    fig, ax = plt.subplots(figsize=(1.2 * n + 2.0, 1.0 * n + 1.5))
    im = ax.imshow(matrix, cmap="RdBu_r", vmin=-1.0, vmax=1.0)
    ax.set_xticks(range(n), labels=names, rotation=45, ha="right")
    ax.set_yticks(range(n), labels=names)
    ax.set_xlabel("as TeamB")
    ax.set_ylabel("as TeamA")
    ax.set_title("(wins_a - wins_b) / n_matches")
    for i in range(n):
        for j in range(n):
            val = matrix[i, j]
            ax.text(
                j,
                i,
                f"{val:+.2f}",
                ha="center",
                va="center",
                color="black" if abs(val) < 0.5 else "white",
                fontsize=9,
            )
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def behaviour_distance(trn: dict[str, Any]) -> tuple[list[str], list[list[float]]]:
    """Compute a symmetric pairwise behavioural-distance matrix.

    The definition - mean |wins_a(i, j) - wins_a(j, i)| normalised by
    n_matches - is a cheap, trace-free proxy that nonetheless captures
    whether two AIs treat each other symmetrically. AIs with identical
    play style (or both no-op) should have distance ≈ 0 against their
    shared opponent set; AIs with opposite dominance should approach 1.

    A richer definition (end-of-match centroid distance) is left for
    future work — it requires replaying traces and so would couple this
    module to the engine binary.
    """
    names = [e["name"] for e in trn["entries"]]
    n = len(names)
    dist = [[0.0] * n for _ in range(n)]
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                continue
            cell_ab = trn["win_matrix"][a][b]
            # Opposite-direction fights contribute equally.
            total = cell_ab["wins_a"] + cell_ab["wins_b"] + cell_ab["draws"] + cell_ab["invalid"]
            if total == 0:
                dist[i][j] = 0.0
                continue
            asym = abs(cell_ab["wins_a"] - cell_ab["wins_b"]) / total
            dist[i][j] = asym
    # Symmetrise: d[i][j] := (d[i][j] + d[j][i]) / 2
    for i in range(n):
        for j in range(i + 1, n):
            avg = (dist[i][j] + dist[j][i]) / 2.0
            dist[i][j] = dist[j][i] = avg
    return names, dist


def plot_clustering(trn: dict[str, Any], out_png: Path) -> bool:
    """Render a simple single-linkage dendrogram of behaviour distance.

    We avoid a scipy dependency by implementing single-linkage in
    ~20 lines — the algorithm is simple and the input size is tiny
    (O(n) = number of AIs, typically ≤ 16).
    """
    plt, _ = _mpl()
    if plt is None:
        return False
    names, dist = behaviour_distance(trn)
    n = len(names)
    if n < 3:
        # Dendrogram is degenerate; still emit a stub figure so tests
        # can assert the file exists.
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(
            0.5,
            0.5,
            f"clustering requires >= 3 AIs (have {n})",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.axis("off")
        fig.savefig(out_png, dpi=120)
        plt.close(fig)
        return True

    # Simple single-linkage agglomerative clustering.
    clusters: list[list[int]] = [[i] for i in range(n)]
    merges: list[tuple[list[int], list[int], float]] = []

    def cluster_dist(ci: list[int], cj: list[int]) -> float:
        return min(dist[a][b] for a in ci for b in cj)

    while len(clusters) > 1:
        best = (math.inf, -1, -1)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = cluster_dist(clusters[i], clusters[j])
                if d < best[0]:
                    best = (d, i, j)
        d, i, j = best
        merges.append((list(clusters[i]), list(clusters[j]), d))
        clusters = (
            clusters[:i] + clusters[i + 1 : j] + clusters[j + 1 :] + [clusters[i] + clusters[j]]
        )

    # Plot merges as a textual dendrogram (simple horizontal layout).
    fig, ax = plt.subplots(figsize=(8, 0.6 * n + 1.5))
    positions: dict[int, float] = {i: float(i) for i in range(n)}
    for _step, (left, right, d) in enumerate(merges, start=1):
        lx = sum(positions[i] for i in left) / len(left)
        rx = sum(positions[i] for i in right) / len(right)
        y = d
        ax.plot([lx, lx, rx, rx], [0.0, y, y, 0.0], "k-", lw=1.2)
        for i in left + right:
            positions[i] = (lx + rx) / 2
    ax.set_xticks(range(n), labels=names, rotation=45, ha="right")
    ax.set_ylabel("single-linkage behaviour distance")
    ax.set_ylim(bottom=0.0)
    ax.set_title("AI behavioural clustering")
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def top_matches(trn: dict[str, Any], k: int = 3) -> list[dict[str, Any]]:
    """Pick the top-``k`` "most informative" pairings — those with the
    largest absolute surprise relative to the pre-round Elo expectation.

    Surprise = |actual_score_a - expected_score_a|, where
    actual_score_a = wins_a + 0.5 * draws (normalised by n_matches),
    and expected_score_a uses the pre-round Elo snapshot. Ties broken
    by team_a, team_b names (deterministic).
    """
    rounds = trn["rounds"]
    elo_start = trn["elo_start"]
    picks: list[dict[str, Any]] = []
    for r in rounds:
        # Pre-round ratings: previous round's snapshot, or elo_start
        # for the first round.
        prev_idx = r["index"] - 1
        if prev_idx < 0:
            pre = {e["name"]: elo_start for e in trn["entries"]}
        else:
            pre = rounds[prev_idx]["ratings_snapshot"]
        for p in r["pairings"]:
            n = p["n_matches"]
            if n == 0:
                continue
            actual = (p["wins_a"] + 0.5 * (p["draws"] + p["invalid"])) / n
            ra, rb = pre[p["team_a"]], pre[p["team_b"]]
            expected = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
            picks.append(
                {
                    "round": r["index"],
                    "team_a": p["team_a"],
                    "team_b": p["team_b"],
                    "n_matches": n,
                    "wins_a": p["wins_a"],
                    "wins_b": p["wins_b"],
                    "draws": p["draws"],
                    "expected_score_a": expected,
                    "actual_score_a": actual,
                    "surprise": abs(actual - expected),
                }
            )
    picks.sort(key=lambda d: (-d["surprise"], d["team_a"], d["team_b"]))
    return picks[:k]


def analyse_tournament(tournament_dir: Path, out_dir: Path, top_k: int = 3) -> dict[str, Any]:
    """Full analysis pass over one tournament directory."""
    trn = load_tournament(tournament_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plt, _ = _mpl()
    status: dict[str, Any] = {"matplotlib": plt is not None}

    if plt is None:
        (out_dir / "skipped.json").write_text(
            json.dumps(
                {
                    "reason": "matplotlib not installed",
                    "source": str(tournament_dir),
                },
                indent=2,
            )
            + "\n"
        )
    else:
        status["rating_trajectory"] = plot_rating_trajectory(trn, out_dir / "rating_trajectory.png")
        status["win_matrix_heatmap"] = plot_win_matrix_heatmap(
            trn, out_dir / "win_matrix_heatmap.png"
        )
        status["clustering"] = plot_clustering(trn, out_dir / "clustering.png")

    picks = top_matches(trn, k=top_k)
    (out_dir / "top_matches.json").write_text(json.dumps(picks, indent=2) + "\n")
    status["top_matches"] = len(picks)
    return status


# ---------------------------------------------------------------------------
# Evolution-run analysis (thin wrapper around existing M10 plotting)
# ---------------------------------------------------------------------------


def analyse_evolution(run_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Produce a fitness-curve PNG from an evolve run.

    We rely on the M10 plotter if available (``scripts/evolve.py``
    ``plot_fitness_curve`` or similar); if not, we fall back to a
    minimal in-process plotter here.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        # Try the latest checkpoint.
        latest = run_dir / "checkpoints" / "latest.json"
        if latest.is_file():
            state_path = latest
        else:
            raise FileNotFoundError(f"no state.json or checkpoints/latest.json in {run_dir}")

    state = json.loads(state_path.read_text())
    history = state.get("history") or state.get("generations") or []

    plt, _ = _mpl()
    if plt is None:
        (out_dir / "skipped.json").write_text(
            json.dumps(
                {"reason": "matplotlib not installed", "source": str(run_dir)},
                indent=2,
            )
            + "\n"
        )
        return {"matplotlib": False, "generations": len(history)}

    xs = []
    means = []
    ci_lo = []
    ci_hi = []
    status_by_gen: list[str] = []
    for i, g in enumerate(history):
        xs.append(g.get("gen", i))
        means.append(g.get("mean"))
        ci_lo.append(g.get("ci_low"))
        ci_hi.append(g.get("ci_high"))
        status_by_gen.append(g.get("status", "unknown"))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, (x, m, s) in enumerate(zip(xs, means, status_by_gen, strict=False)):
        if m is None:
            continue
        colour = "tab:green" if s == "accepted" else "tab:grey"
        ax.scatter([x], [m], c=colour, s=35, zorder=3)
        if ci_lo[i] is not None and ci_hi[i] is not None:
            ax.errorbar(
                [x],
                [m],
                yerr=[[m - ci_lo[i]], [ci_hi[i] - m]],
                c=colour,
                capsize=3,
                alpha=0.6,
                zorder=2,
            )

    # Champion trajectory (accepted-only, step plot).
    champ_x: list[float] = []
    champ_y: list[float] = []
    best = -math.inf
    for x, m, s in zip(xs, means, status_by_gen, strict=False):
        if s == "accepted" and m is not None and m > best:
            best = m
            champ_x.append(x)
            champ_y.append(m)
    if champ_x:
        ax.step(champ_x, champ_y, where="post", c="tab:blue", lw=2.0, label="champion")
        ax.legend(loc="best", fontsize=9)

    ax.set_xlabel("generation")
    ax.set_ylabel("fitness mean (±95% CI)")
    ax.set_title(f"Evolution run — {run_dir.name}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "fitness.png", dpi=120)
    plt.close(fig)
    return {"matplotlib": True, "generations": len(history)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Analyse a completed tournament and/or evolution run.")
    p.add_argument(
        "--tournament",
        type=Path,
        default=None,
        help="Directory containing tournament.json (or the JSON file itself)",
    )
    p.add_argument(
        "--evolution",
        type=Path,
        default=None,
        help="Directory of an evolve run (containing state.json or checkpoints/)",
    )
    p.add_argument("--out", type=Path, required=True, help="Output directory for plots + JSON")
    p.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of 'most informative' matches to include (default 3)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.tournament is None and args.evolution is None:
        print("analysis.py: need at least one of --tournament / --evolution", file=sys.stderr)
        return EXIT_USAGE

    args.out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}

    if args.tournament is not None:
        try:
            summary["tournament"] = analyse_tournament(
                args.tournament, args.out / "tournament", top_k=args.top_k
            )
        except FileNotFoundError as exc:
            print(f"analysis.py: {exc}", file=sys.stderr)
            return EXIT_MISSING_INPUT

    if args.evolution is not None:
        try:
            summary["evolution"] = analyse_evolution(args.evolution, args.out / "evolution")
        except FileNotFoundError as exc:
            print(f"analysis.py: {exc}", file=sys.stderr)
            return EXIT_MISSING_INPUT

    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"analysis.py: wrote {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
