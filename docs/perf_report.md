# SwarmEvolve M13 — GPU Scaling Study

> **Conclusion:** GPU (OpenACC) delivers 6.7× speedup over 20-core OpenMP at N=100 000 drones/team; at N=1 000 the GPU is 4× slower due to kernel launch overhead, with the crossover at approximately N≈4 000.

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

All three backends ran on the same NVIDIA Spark (Grace-Blackwell) host
with unified memory, so CPU↔GPU data transfer overhead is minimal.

* **Host:** NVIDIA GB10 (Grace-Blackwell), ARM64 (Cortex-X925 + Cortex-A725), 20 cores, 128 GB unified memory
* **GPU:** NVIDIA GB10 integrated GPU, CUDA 13.0
* **nvc++ version:** 25.11-0 linuxarm64 (NVIDIA HPC SDK)
* **g++ version:** 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1)
* **OpenMP runtime:** libgomp, OpenMP 4.5 (`_OPENMP 201511`)

## 4. Results

### 4.1 Raw data

<!-- BENCH_DATA_START -->

| Backend | N | Repeats | Wall ms (median) | Wall ms (p25–p75) | µs / tick |
|---------|--:|--------:|-----------------:|------------------:|---------:|
| `cpu1` | 1000 | 30 | 106.89 | 100.36–111.98 | 534.4 |
| `cpu1` | 10000 | 30 | 10448.79 | 10332.11–10516.86 | 52243.9 |
| `cpu_omp` | 1000 | 30 | 45.81 | 38.54–57.86 | 229.0 |
| `cpu_omp` | 10000 | 30 | 2263.70 | 2192.40–2324.59 | 11318.5 |
| `cpu_omp` | 100000 | 30 | 202104.22 | 200346.94–203868.12 | 1010521.1 |
| `gpu` | 1000 | 30 | 183.76 | 168.54–207.75 | 918.8 |
| `gpu` | 10000 | 30 | 1200.33 | 1191.86–1213.02 | 6001.6 |
| `gpu` | 100000 | 30 | 30028.03 | 29985.22–30102.82 | 150140.2 |

<!-- BENCH_DATA_END -->

### 4.2 Plots

Plots are written to `data/bench/plots/`:

* `wall_ms_vs_N.png` — wall time per match, log-log, all backends.
* `per_tick_us_vs_N.png` — per-tick latency, log-log.
* `speedup_vs_N.png` — GPU ÷ best CPU, plus OpenMP ÷ CPU1 as a
  secondary series for context.

Reproduce: `python3 scripts/bench_plot.py --out data/bench`.

## 5. Discussion

### 5.1 Conclusion

GPU (OpenACC) delivers **6.7× speedup** over the 20-core OpenMP baseline
at N=100 000 drones/team; at N=1 000 the GPU is **4× slower** due to
kernel launch overhead, with the GPU–CPU crossover at approximately
**N≈4 000**.

### 5.2 Crossover point

The GPU overtakes CPU-OMP between N=1 000 (GPU 4× slower) and N=10 000
(GPU 1.9× faster). Linear interpolation on the log-log curve places the
crossover at approximately N≈4 000. Below this threshold, single-thread
or OpenMP CPU is preferred.

### 5.3 Speedup table

| N       | GPU vs CPU-OMP | GPU vs CPU1 | OMP vs CPU1 |
|--------:|---------------:|------------:|------------:|
| 1 000   | 0.25×          | 0.58×       | 2.3×        |
| 10 000  | 1.89×          | 8.7×        | 4.6×        |
| 100 000 | 6.73×          | ~34.8× (est)| —           |

CPU1 at N=100 000 was skipped (estimated >17 min/match, ~8.5 hours for
30 repeats). The 34.8× figure is extrapolated from the O(N²) scaling
observed between N=1 000 and N=10 000.

### 5.4 Cell failures

**None.** All (backend, N) cells completed successfully with zero
failures across 240 total measurements. No TDR, OOM, or timeout events
were observed. The NVIDIA GB10 unified memory architecture eliminated
the host↔device transfer bottleneck that discrete GPUs would face.

### 5.5 Observations

* **GPU variance is remarkably low.** The GPU IQR at N=100 000 is
  29 985–30 103 ms (0.4%), vs CPU-OMP's 200 347–203 868 ms (1.7%).
  GPU execution is more deterministic in wall-clock terms.
* **OpenMP scaling is sub-linear.** On 20 cores, OMP achieves only
  2.3–4.6× over single-thread (expected: closer to 10–15× for an
  embarrassingly parallel workload). The O(N²) combat phase is not
  parallelised by OpenMP in the current engine; only the query phase
  benefits.
* **Unified memory advantage.** The GB10's unified memory means
  `-gpu=mem:managed` incurs no PCIe transfer cost. On a discrete GPU,
  the crossover N would likely shift higher.

### 5.6 Recommended next experiment

1. **O(N·k) k-nearest-neighbours rewrite** for the combat phase — the
   current O(N²) all-pairs check is the dominant cost at large N and
   would benefit both CPU and GPU.
2. **Discrete GPU measurement** (e.g. A100/H100) to quantify PCIe
   transfer overhead and determine whether the unified-memory crossover
   generalises.
3. **Scaling beyond N=100 000** — the GB10 handled 100K cleanly; test
   200K and 500K to find the memory ceiling.

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
