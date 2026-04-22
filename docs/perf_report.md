# SwarmEvolve M13 — GPU Scaling Study

> **Status:** scaffolding committed; measurements pending on NVIDIA Spark.
> <!-- CONCLUSION_PENDING -->

## 1. Purpose

Quantify whether GPU offload (OpenACC via `nvc++`) delivers a meaningful
acceleration over honest CPU baselines for the SwarmEvolve tick loop, as
the team size scales from the M1-M12 nominal of 50 drones/team up to
100 000 drones/team.

This report is **deliverable-gated, not performance-gated**: it is
required to land (with an honest conclusion in either direction), but no
specific speedup threshold is promised. If GPU does not beat the CPU
backends, the conclusion sentence will say so and list the next steps.

See [IMPLEMENTATION_PLAN.md §15](../IMPLEMENTATION_PLAN.md) for the full
milestone specification.

## 2. Methodology

### 2.1 Measurement scope (hot path)

The engine exposes a dedicated `--benchmark` mode that:

* runs `--repeats` full matches back-to-back,
* emits **one JSON object per match** on stdout (no other output),
* **writes no files** (no trace, no log, no image),
* times only the per-match tick loop with `std::chrono::steady_clock`.

Each measurement is a single match of `--max-ticks` (default 200) ticks
between two teams of size `N`, using the frozen M3 baseline AIs
(pursuit_v1 vs cluster_v1 for now; swappable via build flags in later
iterations). The wall time reported is the tick-loop wall time only —
process startup, arg parsing, and the post-loop stdout write are
excluded.

### 2.2 Arena scaling (constant density)

To keep the measurement fair across N we scale the arena so that drone
*density* is constant relative to the M1 reference of N=50:

    arena_scale(N) = sqrt(N / 50)

This keeps the expected number of pair interactions per tick (and thus
the O(N²) cost curve's slope) meaningful as N grows. Without density
preservation, the combat-range ratio collapses at large N and the
comparison favours whichever backend has the lowest per-item constant.

### 2.3 Backends under test

Three backends are compiled from the same `src/engine.cpp` with
different toolchains and flags:

| Tag        | Compiler       | Key flag                     | Parallelism           |
|------------|----------------|------------------------------|-----------------------|
| `cpu1`     | clang++ / g++  | (none)                       | single-thread         |
| `cpu_omp`  | g++            | `-fopenmp`                   | host threads (query)  |
| `gpu`      | nvc++          | `-acc=gpu -gpu=mem:managed`  | OpenACC offload       |

All three use `-O2` and `-DMAX_DRONES_OVERRIDE=100000` for the study
runs. The runtime backend label is emitted by the engine itself (via
compile-time macros `_OPENMP` / `_OPENACC`), so a mis-routed binary is
caught by the driver's backend-tag consistency check.

### 2.4 Statistics

For each (backend, N) cell we record `--repeats` samples (default 30).
The report summarises each cell as **median** wall-ms plus **p25 /
p75** interquartile range; we use median rather than mean so a single
context-switch outlier does not dominate. Speedup is computed on
medians (ratio of medians, not median of ratios).

### 2.5 Reproducibility

All inputs are in version control:

* Engine: `src/engine.cpp` (deterministic per-platform, byte-exact
  trace per SPECIFICATION §7.6).
* Baselines: `src/baselines/{pursuit_v1,cluster_v1}.cpp` (frozen
  since M3).
* Driver: `scripts/bench_gpu.py` — stdlib only.
* Plotter: `scripts/bench_plot.py` — matplotlib (optional, skipped
  gracefully if absent).

Full re-run: `make bench-all` (requires all three toolchains present).
Per-backend: `make bench-cpu1`, `make bench-cpu-omp`, `make bench-gpu`.

## 3. Hardware & toolchain

Both the CPU-baseline host and the GPU host will be filled in by the
Spark-Claude session once the measurements run. For scaffolding, the
placeholders below preserve the structure so the final diff is clean.

* **CPU baseline host (cpu1, cpu_omp):** _TBD — e.g. NVIDIA Spark host, ARM64 CPU, N cores_
* **GPU host (gpu):** _TBD — NVIDIA Spark, GPU model, CUDA version_
* **nvc++ version:** _TBD_
* **g++ version:** _TBD_
* **OpenMP runtime:** _TBD_

## 4. Results

### 4.1 Raw data

<!-- BENCH_DATA_START -->
_No data yet — run `make bench-all` on Spark to populate this section._
<!-- BENCH_DATA_END -->

### 4.2 Plots

Plots are written to `data/bench/plots/`:

* `wall_ms_vs_N.png` — wall time per match, log-log, all backends.
* `per_tick_us_vs_N.png` — per-tick latency, log-log.
* `speedup_vs_N.png` — GPU ÷ best CPU, plus OpenMP ÷ CPU1 as a
  secondary series for context.

Reproduce: `python3 scripts/bench_plot.py --out data/bench`.

## 5. Discussion

_To be filled in by the Spark-Claude session after the sweep runs. At a
minimum this section must:_

1. _State the single honest conclusion sentence (also written to the
   top of the report — search for `<!-- CONCLUSION_PENDING -->`)._
2. _Identify the first N at which the GPU overtakes the best CPU
   backend, if any._
3. _Call out any cells that failed to complete (TDR, OOM, etc.) and why._
4. _Name the next experiment worth running given the result._

## 6. Known risks

* **GPU TDR at N=100 000.** The driver treats per-N failures as
  `kind: nonzero_exit` and continues; the 100 000-drone cell may be
  blank in the table if the watchdog fires. That outcome is itself a
  finding.
* **Matplotlib absence** is not an error — the JSON is the source of
  truth; plots are derivative and regenerate idempotently.
* **OpenMP non-determinism** is guarded by `tests/test_bench.py::
  test_bench_backends_agree_at_small_n`, which will light red before
  the run lands a bad pragma.
