#!/usr/bin/env python3
"""M13 benchmark driver — sweep (backend, N, repeats) and collect raw timings.

Responsibilities
----------------
1. Invoke a backend-specific ``swarmevolve-*`` binary in ``--benchmark`` mode
   for each target drone count ``N``.
2. Parse one JSON line per repeat from stdout, validate the ``backend`` field,
   and accumulate them under a common schema.
3. Emit ``bench_results.json`` (canonical, byte-stable under ``sort_keys``)
   and ``bench_results.csv`` (pivoted, human-scannable).
4. Honest-failure principle: missing toolchain, missing binary, non-zero
   exit, or schema drift fails *that run* loudly but allows subsequent runs
   to complete — the final JSON records each failure verbatim so the report
   can explain what was not measured and why.

No matplotlib here — plotting is ``scripts/bench_plot.py``. This separation
means the benchmark driver can run on a headless Spark box without a display
dep.

Subcommands
-----------
run
    Sweep a single backend across the N-list; merges into existing
    bench_results.json.
all
    Sweep every available backend (cpu1, cpu_omp, gpu) using binaries from
    ``--bin-dir``. Regenerates the report at ``--report`` if plotting
    succeeds.

Exit codes
----------
0 : at least one successful (backend, N) combination recorded
2 : CLI usage error
41: the requested backend's binary is missing or not executable
42: the binary produced no parseable JSON lines (stdout empty / malformed)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BINARY_MISSING = 41
EXIT_NO_OUTPUT = 42

# Backends we know how to run. The string must match what the engine's
# bench_backend_tag() emits in its JSON output.
KNOWN_BACKENDS = ("cpu1", "cpu_omp", "gpu")

# Engine-side tag → driver tag. The engine emits "gpu_openacc" for the
# nvc++ build; we normalise to "gpu" for the driver's side of the API so
# callers do not have to care which GPU runtime was used.
BACKEND_TAG_NORMALISE = {
    "cpu1": "cpu1",
    "cpu_omp": "cpu_omp",
    "gpu_openacc": "gpu",
    "gpu": "gpu",
}


# ---------------------------------------------------------------------------
# Scaling law
# ---------------------------------------------------------------------------


def arena_scale_for_n(n: int, reference_n: int = 50) -> float:
    """Return the arena_scale that keeps drone *density* constant at ``n``.

    M13 uses constant density as the primary scaling axis: as the team size
    grows from the baseline of 50 (per team) to 100_000, the arena side
    grows as ``sqrt(n / reference_n)``. This means the combat-range ratio
    and the number of expected interactions per tick do not collapse at
    large N, which would make the comparison unfair to either backend.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    return (n / reference_n) ** 0.5


# ---------------------------------------------------------------------------
# Invocation and parsing
# ---------------------------------------------------------------------------


@dataclass
class BenchRow:
    """One repeat's worth of measurement. Mirrors the engine's JSON line."""

    backend: str
    n: int
    ticks_requested: int
    ticks_executed: int
    arena_scale: float
    wall_ms: float
    per_tick_us: float
    repeat: int
    seed: int
    outcome_code: int
    outcome_tag: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "n": self.n,
            "ticks_requested": self.ticks_requested,
            "ticks_executed": self.ticks_executed,
            "arena_scale": self.arena_scale,
            "wall_ms": self.wall_ms,
            "per_tick_us": self.per_tick_us,
            "repeat": self.repeat,
            "seed": self.seed,
            "outcome_code": self.outcome_code,
            "outcome_tag": self.outcome_tag,
        }


@dataclass
class BackendRunResult:
    backend: str
    binary_path: str
    n_list: list[int]
    repeats: int
    ticks: int
    rows: list[BenchRow] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    host_wall_s: float = 0.0


def invoke_binary_once(
    binary: Path,
    *,
    n: int,
    ticks: int,
    repeats: int,
    seed_base: int,
) -> tuple[list[BenchRow], str]:
    """Invoke the binary and parse one JSON line per repeat.

    Returns (rows, raw_stdout). Raises CalledProcessError on non-zero exit.
    """
    cmd: list[str] = [
        str(binary),
        "--benchmark",
        "--drones-a",
        str(n),
        "--drones-b",
        str(n),
        "--max-ticks",
        str(ticks),
        "--arena-scale",
        f"{arena_scale_for_n(n):.6f}",
        "--repeats",
        str(repeats),
        "--seed",
        str(seed_base),
    ]

    proc = subprocess.run(cmd, check=True, text=True, capture_output=True)
    rows: list[BenchRow] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        raw_backend = obj["backend"]
        normalised = BACKEND_TAG_NORMALISE.get(raw_backend, raw_backend)
        rows.append(
            BenchRow(
                backend=normalised,
                # The engine reports n_a and n_b separately; they are always
                # equal in M13 (50/50 split), but we record the authoritative
                # value from the engine, not the one we asked for.
                n=int(obj["n_a"]),
                ticks_requested=int(obj["ticks_requested"]),
                ticks_executed=int(obj["ticks_executed"]),
                arena_scale=float(obj["arena_scale"]),
                wall_ms=float(obj["wall_ms"]),
                per_tick_us=float(obj["per_tick_us"]),
                repeat=int(obj["repeat"]),
                seed=int(obj["seed"]),
                outcome_code=int(obj["outcome_code"]),
                outcome_tag=str(obj["outcome_tag"]),
            )
        )
    return rows, proc.stdout


def run_backend(
    backend: str,
    binary: Path,
    *,
    n_list: list[int],
    repeats: int,
    ticks: int,
    seed_base: int,
) -> BackendRunResult:
    """Run the full N sweep for a single backend.

    Per-N failures are captured in ``failures`` but do not abort the sweep.
    """
    if backend not in KNOWN_BACKENDS:
        raise ValueError(f"unknown backend {backend!r}")
    if not binary.is_file():
        raise FileNotFoundError(f"binary not found: {binary}")

    result = BackendRunResult(
        backend=backend,
        binary_path=str(binary),
        n_list=list(n_list),
        repeats=repeats,
        ticks=ticks,
    )
    wall_t0 = time.monotonic()
    for n in n_list:
        try:
            rows, _ = invoke_binary_once(
                binary, n=n, ticks=ticks, repeats=repeats, seed_base=seed_base
            )
        except subprocess.CalledProcessError as exc:
            result.failures.append(
                {
                    "n": n,
                    "kind": "nonzero_exit",
                    "returncode": exc.returncode,
                    "stderr": exc.stderr[-2000:] if exc.stderr else "",
                }
            )
            continue
        except (OSError, ValueError) as exc:
            result.failures.append({"n": n, "kind": "exception", "detail": str(exc)})
            continue

        if not rows:
            result.failures.append({"n": n, "kind": "no_output"})
            continue

        # Integrity check: every row must report the backend we asked for,
        # after normalisation. This catches accidental binary mix-ups (e.g.
        # the GPU bench target pointing at the CPU binary).
        bad = [r for r in rows if r.backend != backend]
        if bad:
            result.failures.append(
                {
                    "n": n,
                    "kind": "backend_tag_mismatch",
                    "expected": backend,
                    "got": bad[0].backend,
                }
            )
            continue

        result.rows.extend(rows)
    result.host_wall_s = time.monotonic() - wall_t0
    return result


# ---------------------------------------------------------------------------
# Summaries + serialisation
# ---------------------------------------------------------------------------


def summarise_rows(rows: list[BenchRow]) -> list[dict[str, Any]]:
    """One row per (backend, n) with median + IQR. Stable sort by (backend, n)."""
    groups: dict[tuple[str, int], list[BenchRow]] = {}
    for r in rows:
        groups.setdefault((r.backend, r.n), []).append(r)

    out: list[dict[str, Any]] = []
    for (backend, n), group in sorted(groups.items()):
        wall_values = [r.wall_ms for r in group]
        per_tick = [r.per_tick_us for r in group]
        out.append(
            {
                "backend": backend,
                "n": n,
                "repeats": len(group),
                "wall_ms_median": statistics.median(wall_values),
                "wall_ms_p25": _percentile(wall_values, 0.25),
                "wall_ms_p75": _percentile(wall_values, 0.75),
                "per_tick_us_median": statistics.median(per_tick),
                "ticks_executed_median": statistics.median(r.ticks_executed for r in group),
            }
        )
    return out


def _percentile(values: list[float], q: float) -> float:
    """Simple linear-interpolation percentile. stdlib has it only in 3.10+ quantiles."""
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = q * (len(xs) - 1)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def merge_into_results(
    out_dir: Path,
    new_rows: list[BenchRow],
    new_failures: list[dict[str, Any]],
    backend: str,
    *,
    host_tag: str | None = None,
    extra_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge the new backend's rows into out_dir/bench_results.json.

    Existing rows for the same ``backend`` are replaced so re-running a
    single backend does not leave stale numbers behind.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "bench_results.json"

    if path.is_file():
        existing = json.loads(path.read_text())
    else:
        existing = {
            "schema_version": 1,
            "rows": [],
            "failures": [],
            "provenance": {},
        }

    # Drop stale rows for this backend.
    existing["rows"] = [r for r in existing["rows"] if r.get("backend") != backend]
    existing["failures"] = [f for f in existing["failures"] if f.get("backend") != backend]

    existing["rows"].extend(r.to_dict() for r in new_rows)
    for f in new_failures:
        existing["failures"].append({"backend": backend, **f})

    prov = existing.setdefault("provenance", {})
    backend_prov = prov.setdefault(backend, {})
    if host_tag:
        backend_prov["host_tag"] = host_tag
    backend_prov["recorded_at_unix"] = int(time.time())
    if extra_provenance:
        backend_prov.update(extra_provenance)

    # Sort rows for byte-stability.
    existing["rows"].sort(key=lambda r: (r["backend"], r["n"], r["repeat"]))
    existing["summary"] = summarise_rows([_row_from_dict(r) for r in existing["rows"]])

    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    _write_csv(out_dir / "bench_results.csv", existing["summary"])
    return existing


def _row_from_dict(d: dict[str, Any]) -> BenchRow:
    return BenchRow(
        backend=d["backend"],
        n=int(d["n"]),
        ticks_requested=int(d["ticks_requested"]),
        ticks_executed=int(d["ticks_executed"]),
        arena_scale=float(d["arena_scale"]),
        wall_ms=float(d["wall_ms"]),
        per_tick_us=float(d["per_tick_us"]),
        repeat=int(d["repeat"]),
        seed=int(d["seed"]),
        outcome_code=int(d["outcome_code"]),
        outcome_tag=str(d["outcome_tag"]),
    )


def _write_csv(path: Path, summary: list[dict[str, Any]]) -> None:
    if not summary:
        path.write_text("backend,n,repeats,wall_ms_median,per_tick_us_median\n")
        return
    # Fixed column order; pick a stable-ish subset for humans.
    fieldnames = [
        "backend",
        "n",
        "repeats",
        "wall_ms_median",
        "wall_ms_p25",
        "wall_ms_p75",
        "per_tick_us_median",
        "ticks_executed_median",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in summary:
            w.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="M13 GPU scaling benchmark driver. Sweeps (backend, N) "
        "and emits canonical JSON + CSV.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--n-list",
        type=int,
        nargs="+",
        default=[1000, 10000, 100000],
        help="Drone counts to sweep (per team). Default: 1000 10000 100000.",
    )
    common.add_argument("--repeats", type=int, default=30)
    common.add_argument("--ticks", type=int, default=200)
    common.add_argument("--seed-base", type=int, default=42)
    common.add_argument("--out", type=Path, default=Path("data/bench"))

    run_p = sub.add_parser("run", parents=[common], help="Run one backend.")
    run_p.add_argument("--backend", choices=KNOWN_BACKENDS, required=True)
    run_p.add_argument("--binary", type=Path, required=True)

    all_p = sub.add_parser(
        "all",
        parents=[common],
        help="Run every backend whose binary is present under --bin-dir.",
    )
    all_p.add_argument("--bin-dir", type=Path, default=Path("build/bench"))
    all_p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/perf_report.md"),
        help="Regenerate this Markdown report after the sweep (best-effort).",
    )

    return p


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.binary.is_file():
        print(f"bench_gpu.py: binary not found: {args.binary}", file=sys.stderr)
        return EXIT_BINARY_MISSING
    result = run_backend(
        args.backend,
        args.binary,
        n_list=args.n_list,
        repeats=args.repeats,
        ticks=args.ticks,
        seed_base=args.seed_base,
    )
    if not result.rows and result.failures:
        print(
            f"bench_gpu.py: backend={args.backend} produced no rows; failures={result.failures}",
            file=sys.stderr,
        )
    merge_into_results(
        args.out,
        result.rows,
        result.failures,
        args.backend,
        extra_provenance={
            "binary_path": str(args.binary),
            "n_list": list(args.n_list),
            "repeats": args.repeats,
            "ticks": args.ticks,
            "seed_base": args.seed_base,
            "host_wall_s": round(result.host_wall_s, 3),
        },
    )
    print(
        f"bench_gpu.py: backend={args.backend} rows={len(result.rows)} "
        f"failures={len(result.failures)} wall={result.host_wall_s:.1f}s"
    )
    if not result.rows:
        return EXIT_NO_OUTPUT
    return EXIT_OK


def _cmd_all(args: argparse.Namespace) -> int:
    any_success = False
    for backend in KNOWN_BACKENDS:
        bin_path = args.bin_dir / f"swarmevolve-{backend.replace('_', '-')}"
        if not bin_path.is_file():
            print(
                f"bench_gpu.py: skipping backend={backend}: binary {bin_path} missing "
                f"(build it with `make bench-build-{backend.replace('_', '-')}`)"
            )
            continue
        result = run_backend(
            backend,
            bin_path,
            n_list=args.n_list,
            repeats=args.repeats,
            ticks=args.ticks,
            seed_base=args.seed_base,
        )
        merge_into_results(
            args.out,
            result.rows,
            result.failures,
            backend,
            extra_provenance={
                "binary_path": str(bin_path),
                "n_list": list(args.n_list),
                "repeats": args.repeats,
                "ticks": args.ticks,
                "seed_base": args.seed_base,
                "host_wall_s": round(result.host_wall_s, 3),
            },
        )
        print(
            f"bench_gpu.py: backend={backend} rows={len(result.rows)} "
            f"failures={len(result.failures)} wall={result.host_wall_s:.1f}s"
        )
        if result.rows:
            any_success = True

    # Best-effort plot + report regeneration. Failing to plot is not a
    # hard error — the JSON is the source of truth; plots are derived.
    try:
        from scripts import bench_plot  # type: ignore[import-not-found]

        bench_plot.render_all(args.out)
    except Exception as exc:
        print(f"bench_gpu.py: bench_plot failed ({type(exc).__name__}: {exc})")

    if any_success and args.report:
        try:
            from scripts import bench_plot as _bp  # noqa: F401

            # Deferred import: report renderer lives near the plotter.
            _regenerate_report(args.out, args.report)
        except Exception as exc:
            print(f"bench_gpu.py: report regeneration failed ({type(exc).__name__}: {exc})")

    return EXIT_OK if any_success else EXIT_NO_OUTPUT


def _regenerate_report(bench_dir: Path, report_path: Path) -> None:
    """Minimal report regenerator — fills in the raw-data section.

    The skeleton at ``docs/perf_report.md`` is hand-authored. This function
    only rewrites the region between ``<!-- BENCH_DATA_START -->`` and
    ``<!-- BENCH_DATA_END -->`` markers; everything outside is left intact.
    """
    results_path = bench_dir / "bench_results.json"
    if not results_path.is_file() or not report_path.is_file():
        return
    data = json.loads(results_path.read_text())
    summary = data.get("summary", [])

    lines = [
        "",
        "| Backend | N | Repeats | Wall ms (median) | Wall ms (p25–p75) | µs / tick |",
        "|---------|--:|--------:|-----------------:|------------------:|---------:|",
    ]
    for row in summary:
        lines.append(
            f"| `{row['backend']}` | {row['n']} | {row['repeats']} | "
            f"{row['wall_ms_median']:.2f} | "
            f"{row['wall_ms_p25']:.2f}–{row['wall_ms_p75']:.2f} | "
            f"{row['per_tick_us_median']:.1f} |"
        )
    lines.append("")

    text = report_path.read_text()
    start = "<!-- BENCH_DATA_START -->"
    end = "<!-- BENCH_DATA_END -->"
    if start in text and end in text:
        before, _, rest = text.partition(start)
        _, _, after = rest.partition(end)
        new_text = before + start + "\n" + "\n".join(lines) + "\n" + end + after
        report_path.write_text(new_text)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "all":
        return _cmd_all(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
