#!/usr/bin/env python3
"""Final-report renderer (M12 exit criterion).

Given a completed experiment directory — which may contain any
combination of ``tournament.json``, an evolve-run ``state.json``, and
an ``analysis/`` directory produced by ``scripts/analysis.py`` — emit
a standalone Markdown report (``report.md``) that requires zero manual
edits.

The renderer is deliberately conservative:

* No YAML/Jinja dependency — the template is a handful of f-string
  blocks, so the only runtime dep is the stdlib.
* Missing inputs degrade gracefully; the corresponding section is
  omitted and a short note appended to ``report.md``.
* Image references use relative paths from ``report.md`` to the
  analysis PNGs, so the report is portable as long as the directory
  tree is kept together.

Directory layout it understands::

    <experiment_dir>/
        tournament.json                  # from scripts/tournament.py
        tournament.canonical.json        # ditto
        ratings.csv                      # ditto
        win_matrix.csv                   # ditto
        state.json                       # from scripts/evolve.py (optional)
        checkpoints/latest.json          # fallback for state.json
        events.jsonl                     # from any ExperimentLog user
        analysis/
            tournament/
                rating_trajectory.png
                win_matrix_heatmap.png
                clustering.png
                top_matches.json
            evolution/
                fitness.png

If the ``analysis/`` subtree is missing and ``--run-analysis`` is set,
``scripts.analysis`` is invoked automatically so the report has its
plots.

Exit codes
----------
- ``0`` : report.md written
- ``2`` : CLI usage error
- ``41``: experiment_dir missing or empty
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


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_tournament_section(experiment_dir: Path, analysis_subdir: Path | None) -> list[str]:
    trn_path = experiment_dir / "tournament.json"
    if not trn_path.is_file():
        return []

    trn = json.loads(trn_path.read_text())
    ranked = sorted(trn["final_ratings"].items(), key=lambda kv: (-kv[1], kv[0]))
    lines: list[str] = [
        "## Tournament results",
        "",
        f"- Mode: `{trn['mode']}`",
        f"- Matches per pairing: `{trn['n_matches']}`",
        f"- Elo start / K: `{trn['elo_start']}` / `{trn['elo_k']}`",
        f"- Participants: {len(trn['entries'])}",
        f"- Rounds played: {len(trn['rounds'])}",
        "",
        "### Final ratings",
        "",
        "| Rank | Name | Rating |",
        "|-----:|------|-------:|",
    ]
    for i, (name, rating) in enumerate(ranked, start=1):
        lines.append(f"| {i} | `{name}` | {rating:.2f} |")
    lines.append("")

    # Participants with SHA-256.
    lines += ["### Participants", "", "| Name | SHA-256 | Path |", "|------|---------|------|"]
    for e in trn["entries"]:
        lines.append(f"| `{e['name']}` | `{e['sha256'][:12]}…` | `{e['path']}` |")
    lines.append("")

    # Plots (if analysis directory is present).
    if analysis_subdir is not None and (analysis_subdir / "tournament").is_dir():
        plots_rel = analysis_subdir.relative_to(experiment_dir) / "tournament"
        for fname in ("rating_trajectory.png", "win_matrix_heatmap.png", "clustering.png"):
            target = analysis_subdir / "tournament" / fname
            if target.is_file():
                lines.append(f"![{fname}]({plots_rel / fname})")
                lines.append("")

    # Top matches.
    top_path = (
        (analysis_subdir / "tournament" / "top_matches.json")
        if analysis_subdir is not None
        else None
    )
    if top_path is not None and top_path.is_file():
        picks = json.loads(top_path.read_text())
        if picks:
            lines += [
                "### Most informative matches",
                "",
                "| Round | A | B | Expected | Actual | Surprise |",
                "|------:|---|---|---------:|-------:|---------:|",
            ]
            for p in picks:
                lines.append(
                    f"| {p['round']} | `{p['team_a']}` | `{p['team_b']}` | "
                    f"{p['expected_score_a']:.3f} | {p['actual_score_a']:.3f} | "
                    f"{p['surprise']:.3f} |"
                )
            lines.append("")

    return lines


def _render_evolution_section(experiment_dir: Path, analysis_subdir: Path | None) -> list[str]:
    state_path = experiment_dir / "state.json"
    if not state_path.is_file():
        latest = experiment_dir / "checkpoints" / "latest.json"
        if latest.is_file():
            state_path = latest
        else:
            return []
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    history = state.get("history") or state.get("generations") or []
    if not history:
        return []

    accepted = [g for g in history if g.get("status") == "accepted"]
    best = max(
        (g for g in accepted if g.get("mean") is not None),
        key=lambda g: g["mean"],
        default=None,
    )

    lines = [
        "## Evolution run",
        "",
        f"- Generations attempted: {len(history)}",
        f"- Accepted generations: {len(accepted)}",
    ]
    if best is not None:
        lines.append(
            f"- Champion fitness mean: {best['mean']:.4f} (generation {best.get('gen', '?')})"
        )
    lines.append("")

    if analysis_subdir is not None:
        fitness_png = analysis_subdir / "evolution" / "fitness.png"
        if fitness_png.is_file():
            rel = fitness_png.relative_to(experiment_dir)
            lines.append(f"![fitness.png]({rel})")
            lines.append("")
    return lines


def _render_environment_section(experiment_dir: Path) -> list[str]:
    events_path = experiment_dir / "events.jsonl"
    if not events_path.is_file():
        return []

    start_event: dict[str, Any] | None = None
    try:
        with events_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "experiment_start":
                    start_event = obj
                    break
    except (OSError, json.JSONDecodeError):
        return []
    if start_event is None:
        return []

    env = start_event.get("environment", {})
    lines = [
        "## Environment",
        "",
        f"- git SHA: `{env.get('git_sha', '?')}`",
        f"- git dirty: `{env.get('git_dirty', '?')}`",
        f"- Python: `{env.get('python_version', '?')}`",
        f"- Platform: `{env.get('platform', '?')}`",
        f"- Experiment type: `{start_event.get('experiment_type', '?')}`",
        "",
    ]
    return lines


# ---------------------------------------------------------------------------
# Top-level render
# ---------------------------------------------------------------------------


def render_report(
    experiment_dir: Path,
    *,
    analysis_subdir: Path | None = None,
    title: str | None = None,
) -> str:
    """Produce the full Markdown report as a single string."""
    if not experiment_dir.is_dir():
        raise FileNotFoundError(f"experiment_dir not found: {experiment_dir}")
    title = title or f"SwarmEvolve experiment report — {experiment_dir.name}"

    if analysis_subdir is None:
        candidate = experiment_dir / "analysis"
        analysis_subdir = candidate if candidate.is_dir() else None

    sections: list[str] = [f"# {title}", ""]
    env_lines = _render_environment_section(experiment_dir)
    trn_lines = _render_tournament_section(experiment_dir, analysis_subdir)
    evo_lines = _render_evolution_section(experiment_dir, analysis_subdir)

    sections += env_lines
    sections += trn_lines
    sections += evo_lines

    if not (trn_lines or evo_lines):
        sections += [
            "## No results",
            "",
            "This experiment directory does not contain a recognisable",
            "tournament.json or evolve-run state.json. Run the tournament",
            "or evolution pipelines first.",
            "",
        ]

    sections.append("---")
    sections.append("")
    sections.append("*Report generated by `scripts/render_report.py`.*")
    sections.append("")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Render a final Markdown report from a completed experiment directory.",
    )
    p.add_argument("experiment_dir", type=Path)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: <experiment_dir>/report.md)",
    )
    p.add_argument(
        "--analysis-dir",
        type=Path,
        default=None,
        help="Analysis directory to embed plots from (default: <experiment_dir>/analysis)",
    )
    p.add_argument(
        "--run-analysis",
        action="store_true",
        help="If an analysis dir is missing, run scripts/analysis.py first",
    )
    p.add_argument("--title", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.experiment_dir.is_dir():
        print(f"render_report.py: not a directory: {args.experiment_dir}", file=sys.stderr)
        return EXIT_MISSING_INPUT

    # If asked, auto-run analysis before rendering.
    analysis_dir = args.analysis_dir or (args.experiment_dir / "analysis")
    if args.run_analysis and not analysis_dir.is_dir():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import analysis as a_mod
        except ImportError as exc:
            print(f"render_report.py: cannot import analysis: {exc}", file=sys.stderr)
            return EXIT_MISSING_INPUT
        cli: list[str] = ["--out", str(analysis_dir)]
        if (args.experiment_dir / "tournament.json").is_file():
            cli += ["--tournament", str(args.experiment_dir)]
        if (args.experiment_dir / "state.json").is_file() or (
            args.experiment_dir / "checkpoints" / "latest.json"
        ).is_file():
            cli += ["--evolution", str(args.experiment_dir)]
        if cli != ["--out", str(analysis_dir)]:
            a_mod.main(cli)

    try:
        text = render_report(
            args.experiment_dir,
            analysis_subdir=analysis_dir if analysis_dir.is_dir() else None,
            title=args.title,
        )
    except FileNotFoundError as exc:
        print(f"render_report.py: {exc}", file=sys.stderr)
        return EXIT_MISSING_INPUT

    out = args.out or (args.experiment_dir / "report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"render_report.py: wrote {out} ({len(text)} chars)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
