#!/usr/bin/env python3
"""Eval-container checks: determinism gate and evaluation timing (v2 study).

Baselines only (no LLM code); no paid API calls.

``determinism``
    Plays 200 baseline matches in the eval container (pursuit_v1 vs
    cluster_v1, seeds ``--seed-base`` .. +99, each seed with both side
    assignments) twice, and asserts the per-match rows are byte-identical
    apart from ``wall_ms``. For information it also replays them with a
    different container split and on the host backend. Writes
    ``determinism.json`` and renders ``DETERMINISM.md`` from it; exits 1
    if the gate fails.

``timing``
    Times a 400-match evaluation (200 seeds x both side assignments, run
    as two ``fitness.evaluate_fitness`` calls) on 4 cores, 8 cores and all
    cores for several container sizes, including container start and
    compile. Writes ``eval_timing.json`` and renders ``EVAL_TIMING.md``.

    python scripts/eval_platform_checks.py determinism --out-dir reports/setup
    python scripts/eval_platform_checks.py timing --out-dir reports/setup
    python scripts/eval_platform_checks.py timing --out-dir reports/setup --render-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
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
import sandbox  # noqa: E402

BASELINES = REPO_ROOT / "src" / "baselines"
CANDIDATE, CHAMPION = "pursuit_v1", "cluster_v1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git_dirty() -> bool:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return bool(out.strip())


def _write_status(path: Path, **fields: Any) -> None:
    fields["updated_utc"] = _now()
    path.write_text(json.dumps(fields, sort_keys=True) + "\n")


def evaluate_both_sides(
    seed_base: int, n_seeds: int, scratch: Path, **kwargs: Any
) -> tuple[list[fitness.FitnessResult], float]:
    """Candidate vs champion on the same seeds, each seed with both side
    assignments: two evaluations, candidate under test in each."""
    t0 = time.monotonic()
    results = [
        fitness.evaluate_fitness(
            BASELINES / f"{CANDIDATE}.cpp",
            BASELINES / f"{CHAMPION}.cpp",
            n_matches=n_seeds,
            seed_base=seed_base,
            under_test="A",
            scratch_root=scratch,
            **kwargs,
        ),
        fitness.evaluate_fitness(
            BASELINES / f"{CHAMPION}.cpp",
            BASELINES / f"{CANDIDATE}.cpp",
            n_matches=n_seeds,
            seed_base=seed_base,
            under_test="B",
            scratch_root=scratch,
            **kwargs,
        ),
    ]
    return results, time.monotonic() - t0


def canonical_rows(results: list[fitness.FitnessResult]) -> bytes:
    """Per-match rows of both side assignments without ``wall_ms``, as the
    bytes the gate compares."""
    lines = []
    for side, r in zip(("candidate_as_A", "candidate_as_B"), results, strict=True):
        for m in r.per_match:
            row = {k: v for k, v in m.items() if k != "wall_ms"}
            row["assignment"] = side
            lines.append(json.dumps(row, sort_keys=True))
    return ("\n".join(lines) + "\n").encode()


def _run_summary(results: list[fitness.FitnessResult], wall: float) -> dict[str, Any]:
    data = canonical_rows(results)
    return {
        "rows_sha256": hashlib.sha256(data).hexdigest(),
        "n_matches": sum(r.n_matches for r in results),
        "wall_seconds": round(wall, 3),
        "per_assignment": [
            {k: v for k, v in r.to_dict().items() if k not in ("per_match", "platform")}
            for r in results
        ],
        "per_match": [r.per_match for r in results],
    }


# ---------------------------------------------------------------------------
# Determinism gate
# ---------------------------------------------------------------------------


def run_determinism(args: argparse.Namespace) -> dict[str, Any]:
    status = args.out_dir / "determinism_status.json"
    scratch = Path(tempfile.mkdtemp(prefix="detgate_", dir=Path.home() / ".pytest-sandbox"))
    workers = os.cpu_count() or 1
    out: dict[str, Any] = {
        "started_utc": _now(),
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "image": args.image,
        "pairing": {"candidate": CANDIDATE, "champion": CHAMPION},
        "seeds": [args.seed_base, args.seed_base + args.n_seeds - 1],
        "runs": {},
    }
    configs = {
        "run1": dict(backend="sandbox", workers=workers),
        "run2": dict(backend="sandbox", workers=workers),
        "other_split": dict(backend="sandbox", workers=4, container_cpus=1),
        "host": dict(backend="host", workers=workers),
    }
    platform: dict[str, Any] | None = None
    for i, (name, cfg) in enumerate(configs.items()):
        _write_status(status, state="running", step=name, done=i, total=len(configs))
        kwargs = dict(cfg)
        if kwargs["backend"] == "sandbox":
            kwargs["image"] = args.image
        results, wall = evaluate_both_sides(args.seed_base, args.n_seeds, scratch, **kwargs)
        out["runs"][name] = {"config": cfg, **_run_summary(results, wall)}
        if name == "run1":
            platform = results[0].platform
        out["runs"][name]["platform_label"] = results[0].platform["label"]
    runs = out["runs"]
    out["platform"] = platform
    out["gate_pass"] = runs["run1"]["rows_sha256"] == runs["run2"]["rows_sha256"]
    out["other_split_matches"] = runs["run1"]["rows_sha256"] == runs["other_split"]["rows_sha256"]
    out["host_matches"] = runs["run1"]["rows_sha256"] == runs["host"]["rows_sha256"]
    out["finished_utc"] = _now()
    _write_status(status, state="done", gate_pass=out["gate_pass"])
    return out


def render_determinism(res: dict[str, Any], source: str) -> str:
    runs = res["runs"]
    fp = res["platform"]
    yes = {True: "yes", False: "**no**"}
    lines = [
        "# Determinism gate (eval container)",
        "",
        f"Generated by `scripts/eval_platform_checks.py` from `{source}`. Platform label: "
        f"**{fp['label']}**. Commit `{res['git_sha'][:12]}`"
        + (" (with uncommitted changes)" if res["git_dirty"] else "")
        + f", started {res['started_utc']}.",
        "",
        f"{runs['run1']['n_matches']} matches: {res['pairing']['candidate']} (candidate) vs "
        f"{res['pairing']['champion']} (champion), seeds {res['seeds'][0]}-{res['seeds'][1]}, "
        "each seed with both side assignments. Compared: every per-match row (seed, outcome, "
        "score, ticks, a_alive, b_alive, return_code) except `wall_ms`, as canonical JSON lines.",
        "",
        "| Run | Backend | Workers | CPUs per container | Wall (s) | SHA-256 of rows |",
        "|---|---|---|---|---|---|",
    ]
    for name, run in runs.items():
        cfg = run["config"]
        cpus = cfg.get("container_cpus", "default") if cfg["backend"] == "sandbox" else "-"
        lines.append(
            f"| {name} | {cfg['backend']} | {cfg['workers']} | {cpus} | {run['wall_seconds']} "
            f"| `{run['rows_sha256'][:16]}` |"
        )
    lines += [
        "",
        f"- Gate (run1 and run2 byte-identical): **{'pass' if res['gate_pass'] else 'FAIL'}**",
        f"- A different container split gives the same rows: {yes[res['other_split_matches']]}",
        f"- The host backend gives the same rows: {yes[res['host_matches']]}",
        "",
        "## Platform",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Image | `{fp['image']}` |",
        f"| Image ID | `{fp['image_id']}` |",
        f"| Compiler | {fp['compiler_version']} |",
        f"| Flags | `{' '.join(fp['flags'])}` |",
        f"| CPU | {fp['cpu_model']} |",
        f"| Kernel | {fp['kernel']} |",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def run_timing(args: argparse.Namespace) -> dict[str, Any]:
    status = args.out_dir / "eval_timing_status.json"
    scratch = Path(tempfile.mkdtemp(prefix="timing_", dir=Path.home() / ".pytest-sandbox"))
    all_cores = os.cpu_count() or 1
    configs: list[dict[str, Any]] = []
    for cores in sorted({4, 8, all_cores}):
        for cpus in (1, 2, 4, 8, 16):
            if cpus <= cores and cpus <= sandbox.MAX_JOBS_PER_CONTAINER:
                configs.append({"backend": "sandbox", "cores": cores, "container_cpus": cpus})
        configs.append({"backend": "host", "cores": cores})
    out: dict[str, Any] = {
        "started_utc": _now(),
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "image": args.image,
        "pairing": {"candidate": CANDIDATE, "champion": CHAMPION},
        "seeds": [args.seed_base, args.seed_base + args.n_seeds - 1],
        "matches_per_evaluation": 2 * args.n_seeds,
        "repeats": args.repeats,
        "all_cores": all_cores,
        "configs": configs,
        "samples": [],
    }

    # Warm-up (page cache, image layers); not recorded.
    evaluate_both_sides(
        args.seed_base, args.n_seeds, scratch, backend="sandbox", image=args.image, workers=4
    )
    total = args.repeats * len(configs)
    platform: dict[str, Any] | None = None
    # Interleave configurations within each repeat so slow drift on the
    # host spreads evenly across them.
    for rep in range(args.repeats):
        for ci, cfg in enumerate(configs):
            _write_status(status, state="running", done=rep * len(configs) + ci, total=total)
            kwargs: dict[str, Any] = {"backend": cfg["backend"], "workers": cfg["cores"]}
            if cfg["backend"] == "sandbox":
                kwargs.update(image=args.image, container_cpus=cfg["container_cpus"])
            results, wall = evaluate_both_sides(args.seed_base, args.n_seeds, scratch, **kwargs)
            if cfg["backend"] == "sandbox" and platform is None:
                platform = results[0].platform
            out["samples"].append(
                {
                    "config": ci,
                    "repeat": rep,
                    "wall_seconds": round(wall, 4),
                    "invalid": sum(r.invalid for r in results),
                    "rows_sha256": hashlib.sha256(canonical_rows(results)).hexdigest(),
                }
            )

    # Phase breakdown of one container (status.json timings plus host wall).
    staged = scratch / "breakdown"
    staged.mkdir()
    for team, name in (("TeamA", CANDIDATE), ("TeamB", CHAMPION)):
        fitness._render(BASELINES / f"{name}.cpp", team, staged / f"{team}.cpp")
    image_id = sandbox.resolve_image_id(args.image)
    breakdown = []
    for rep in range(args.repeats):
        b = sandbox.run_batch_in_sandbox(
            staged / "TeamA.cpp",
            staged / "TeamB.cpp",
            list(range(args.seed_base, args.seed_base + args.n_seeds)),
            staged / f"run{rep}",
            image_id=image_id,
            cpus=4,
        )
        breakdown.append(
            {
                "host_wall_seconds": round(b.wall_seconds, 4),
                "in_container_seconds": b.status["wall_seconds"],
                "compile_seconds": b.status["compile_seconds"],
                "play_seconds": b.status["play_seconds"],
                "matches": len(b.rows),
            }
        )
    out["breakdown_one_container"] = {"container_cpus": 4, "runs": breakdown}
    out["platform"] = platform
    out["finished_utc"] = _now()
    _write_status(status, state="done")
    return out


def render_timing(res: dict[str, Any], source: str) -> str:
    fp = res["platform"]
    n = res["matches_per_evaluation"]
    lines = [
        "# Evaluation timing (eval container)",
        "",
        f"Generated by `scripts/eval_platform_checks.py` from `{source}`. Platform label: "
        f"**{fp['label']}**. Commit `{res['git_sha'][:12]}`"
        + (" (with uncommitted changes)" if res["git_dirty"] else "")
        + f", started {res['started_utc']}.",
        "",
        f"One evaluation = {n} matches: {res['pairing']['candidate']} vs "
        f"{res['pairing']['champion']}, seeds {res['seeds'][0]}-{res['seeds'][1]}, each seed "
        "with both side assignments (two `fitness.evaluate_fitness` calls, one after the other). "
        "Wall time covers both calls end to end: staging, container start, guard injection, "
        "compile (once per container), play and result parsing. `cores` is the total CPU quota "
        "(`workers`); each container gets `--cpus=container_cpus` and plays that many matches "
        f"at a time. {res['repeats']} repeats per configuration, interleaved, after one "
        "unrecorded warm-up evaluation.",
        "",
        "| Cores | Backend | CPUs per container | Containers per call | Median wall (s) "
        "| Min | Max | Matches/s (median) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ci, cfg in enumerate(res["configs"]):
        walls = [s["wall_seconds"] for s in res["samples"] if s["config"] == ci]
        med = statistics.median(walls)
        if cfg["backend"] == "sandbox":
            cpus = cfg["container_cpus"]
            containers = str(-(-cfg["cores"] // cpus))
        else:
            cpus, containers = "-", "-"
        lines.append(
            f"| {cfg['cores']} | {cfg['backend']} | {cpus} | {containers} | {med:.2f} "
            f"| {min(walls):.2f} | {max(walls):.2f} | {n / med:.0f} |"
        )
    shas = {s["rows_sha256"] for s in res["samples"]}
    invalid = sum(s["invalid"] for s in res["samples"])
    bd = res["breakdown_one_container"]
    lines += [
        "",
        f"Every timed evaluation produced the same per-match rows: "
        f"{'yes' if len(shas) == 1 else '**no** (' + str(len(shas)) + ' distinct)'}. "
        f"Invalid matches across all samples: {invalid}.",
        "",
        f"## One container, {bd['container_cpus']} CPUs, {res['seeds'][1] - res['seeds'][0] + 1} "
        "matches (one side assignment)",
        "",
        "| Run | Host wall (s) | In container (s) | Compile (s) | Play (s) |",
        "|---|---|---|---|---|",
    ]
    for i, run in enumerate(bd["runs"]):
        lines.append(
            f"| {i} | {run['host_wall_seconds']:.2f} | {run['in_container_seconds']:.2f} "
            f"| {run['compile_seconds']:.2f} | {run['play_seconds']:.2f} |"
        )
    lines += [
        "",
        "Host wall minus in-container time is container start and teardown. The baselines "
        "are cheap programs, so fixed costs dominate; heavier LLM-written programs shift "
        "the balance towards play time, which scales with cores.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("check", choices=["determinism", "timing"])
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "reports" / "setup")
    p.add_argument("--image", default=sandbox.DEFAULT_IMAGE)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--n-seeds", type=int, default=None)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--render-only", action="store_true")
    args = p.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".pytest-sandbox").mkdir(exist_ok=True)

    if args.check == "determinism":
        args.seed_base = 300000 if args.seed_base is None else args.seed_base
        args.n_seeds = args.n_seeds or 100
        name, run, render = "determinism", run_determinism, render_determinism
        md = "DETERMINISM.md"
    else:
        args.seed_base = 310000 if args.seed_base is None else args.seed_base
        args.n_seeds = args.n_seeds or 200
        name, run, render = "eval_timing", run_timing, render_timing
        md = "EVAL_TIMING.md"

    json_path = args.out_dir / f"{name}.json"
    if not args.render_only:
        res = run(args)
        json_path.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    res = json.loads(json_path.read_text())
    (args.out_dir / md).write_text(render(res, json_path.name))
    print(f"wrote {json_path} and {args.out_dir / md}")
    if args.check == "determinism" and not res["gate_pass"]:
        print("determinism gate FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
