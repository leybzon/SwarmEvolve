# M13 Hand-off — Spark-Claude Session Runbook

This document is the full hand-off from the macOS scaffolding session to
the NVIDIA-Spark Claude Code session. Everything the Spark session needs
to finish M13 and close the milestone is here.

## 0. Prerequisites on the Spark host

1. Clone the repo and check out the branch with the M13 scaffolding.
2. Ensure the following toolchain is on `PATH`:
   * `nvc++` (NVIDIA HPC SDK 24.x or newer, with OpenACC)
   * `g++` (≥11, with `libgomp`)
   * `python3` (≥3.10 for stdlib-only `statistics.quantiles`)
3. Optional but recommended: `matplotlib` in the Python env used to run
   `bench-all`. Without it, the JSON is still authoritative; plots just
   do not get regenerated.

Quick sanity check:

```bash
nvc++ --version
g++ --version
python3 -c "import matplotlib; print(matplotlib.__version__)" || echo "(no mpl: ok, plots skipped)"
```

## 1. Build every bench binary

Single command, uses the default `BENCH_MAX_DRONES=100000`:

```bash
make bench-build-cpu1 bench-build-cpu-omp bench-build-gpu
```

Binaries appear under `build/bench/`:

* `build/bench/swarmevolve-cpu1`
* `build/bench/swarmevolve-cpu-omp`
* `build/bench/swarmevolve-gpu`

Each binary reports its own backend tag via a JSON line when invoked
with `--benchmark`. The driver cross-checks this to catch accidental
binary mix-ups.

### 1.1 Common build issues

* **nvc++ refuses `-Wpedantic`.** The bench Makefile target uses only
  the flags nvc++ accepts; the CPU-warning-strict flags are applied in
  a separate target (`build-linux-cpu-omp`) which is not part of the
  bench sweep.
* **`libgomp.so.1` not found at runtime.** Add
  `LD_LIBRARY_PATH=/usr/lib/gcc/…/11` to your env, or rebuild g++ with
  a static libgomp (`-static-libgcc -static-libstdc++ -Wl,-Bstatic -lgomp
  -Wl,-Bdynamic`). Check with `ldd build/bench/swarmevolve-cpu-omp`.
* **Compile time for N=100 000.** First build of nvc++ with managed
  memory can take several minutes; this is normal.

## 2. Smoke-test each binary before the sweep

Save 30 minutes of doomed sweep time by checking each binary runs one
repeat cleanly:

```bash
for b in cpu1 cpu-omp gpu; do
  echo "=== $b ==="
  ./build/bench/swarmevolve-$b \
      --benchmark --drones-a 100 --drones-b 100 \
      --max-ticks 50 --repeats 1 --seed 1 \
      --arena-scale 1.414
done
```

Expected output: one JSON object per invocation, with the `backend`
field matching (respectively) `cpu1`, `cpu_omp`, `gpu_openacc`.

**If the GPU run hangs >60 s on this small sweep, abort.** It means the
OpenACC build is not actually offloading (or managed memory is broken).
Common root cause: the `-gpu=managed` flag got dropped, so host-only
fallback is printing nothing. Rebuild with
`make clean bench-build-gpu`.

## 3. Run the sweep

Full sweep (every backend, default N={1K, 10K, 100K}, 30 repeats,
200 ticks):

```bash
make bench-all
```

This internally invokes:

```bash
python3 scripts/bench_gpu.py all \
    --bin-dir build/bench \
    --n-list 1000 10000 100000 \
    --repeats 30 --ticks 200 --seed-base 42 \
    --out data/bench \
    --report docs/perf_report.md
```

Expected wall-clock wall-time for the full sweep:

| Backend  | N=1K     | N=10K    | N=100K             |
|----------|---------:|---------:|-------------------:|
| cpu1     | <5 s     | ~5 min   | TDR-risk, ~1 h+    |
| cpu_omp  | <5 s     | ~1 min   | ~10–30 min         |
| gpu      | <10 s    | <30 s    | unknown — measure  |

If the `cpu1 @ 100K` cell threatens to burn a day, **skip it**:

```bash
python3 scripts/bench_gpu.py run \
    --backend cpu1 --binary build/bench/swarmevolve-cpu1 \
    --n-list 1000 10000 \
    --repeats 30 --ticks 200 --seed-base 42 \
    --out data/bench
```

Then run `cpu_omp` and `gpu` at the full N-list. The driver marks the
missing cell in the failure log rather than in the summary table — the
report can reference this gap explicitly.

## 4. Regenerate plots and the report

`make bench-all` attempts this automatically. If it was skipped (no
matplotlib, or you ran backends individually), regenerate manually:

```bash
python3 scripts/bench_plot.py --out data/bench
python3 -c "from scripts.bench_gpu import _regenerate_report; \
            from pathlib import Path; \
            _regenerate_report(Path('data/bench'), Path('docs/perf_report.md'))"
```

Check:

```bash
ls data/bench/plots/
# expect: wall_ms_vs_N.png  per_tick_us_vs_N.png  speedup_vs_N.png
grep -c BENCH_DATA_ docs/perf_report.md
# expect: 2
```

## 5. Land the honest conclusion

The file `docs/perf_report.md` contains a `<!-- CONCLUSION_PENDING -->`
marker near the top. **M13 exits the milestone only when this marker is
removed and replaced by an honest one-sentence conclusion.** Examples
of acceptable conclusions:

* _"GPU delivers 12.4× over the OpenMP baseline at N=100 000; at
  N=1 000 the GPU is 3.1× slower than CPU-OMP due to launch overhead,
  so the crossover N is approximately 4K."_
* _"GPU never beats CPU-OMP in the tested range; the O(N²) kernel is
  memory-bandwidth bound at N=100 000, and the next experiment worth
  running is an O(N·k) k-nearest-neighbours rewrite."_
* _"N=100 000 crashes with TDR on the test hardware regardless of
  backend; the crossover is not yet measurable. Next step is to
  install a longer watchdog timeout (`cudaDeviceSetLimit`) and retry."_

Update Discussion §5 with:

1. The single-sentence conclusion (also in the top banner).
2. The crossover N (if any).
3. Per-cell failure notes (TDR, OOM, etc.).
4. Recommended next experiment.

## 6. Additional artefacts to commit

After the sweep lands:

```
data/bench/bench_results.json       # canonical, byte-stable
data/bench/bench_results.csv        # human-readable pivot
data/bench/plots/*.png              # if matplotlib ran
docs/perf_report.md                 # updated with data + conclusion
```

Then run the test suite once to catch regressions:

```bash
pytest tests/test_bench.py -v
# All 8 tests should pass on Spark (no skips — both a compiler and a
# working g++-with-OpenMP are present).
```

Commit message template:

```
M13: land GPU scaling study (<one-line conclusion>)

- Measured CPU1, CPU-OMP, GPU at N ∈ {1K, 10K, 100K}
- Crossover: <…>
- Data: data/bench/bench_results.json (byte-stable)
- Plots: data/bench/plots/*.png
- Report: docs/perf_report.md
```

Then add a CHANGELOG entry under the Unreleased header:
`- M13: GPU scaling study — <conclusion>.`

## 7. Scope guardrails

These are **out of scope** for this session (per
IMPLEMENTATION_PLAN.md §15 deferred items):

* New AI modules, richer evolution, tournament replays — M14+.
* Video / visualiser upgrades — M14+.
* Rewriting the engine for k-nearest — M15+ candidate.
* Scientific write-up or external blog post — M17+ candidate.

If the measurement exposes a trivial one-line fix (e.g. a stray
`printf` in the hot path that was missed), land it here with a test.
Otherwise, file it as an issue/TODO and keep scope tight.

## 8. Escalation

* If the sweep reveals an engine bug (non-determinism, memory
  corruption), stop the sweep and fix the bug first — the `cpu1 vs
  cpu_omp` byte-identity test in `tests/test_bench.py` is the canonical
  guard.
* If `nvc++` cannot be installed on the target host, document the
  toolchain-absence explicitly in the report, run only CPU1/OMP, and
  close M13 with "GPU not measured on available hardware; this
  milestone recorded the CPU scaling baseline only." That is an
  acceptable exit per the deliverable-gated exit criterion.
