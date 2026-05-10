"""M13 benchmark plumbing tests.

Covers
------
* ``--benchmark`` mode produces one parseable JSON line per ``--repeats``
  and writes no files (hot path is side-effect free).
* ``--benchmark`` + ``--record`` is rejected with a non-zero exit.
* CPU1 and CPU-OMP engines produce byte-identical traces for identical
  seeds at small N (OpenMP determinism regression guard).
* ``scripts/bench_plot.render_all`` on a synthetic ``bench_results.json``
  returns a status dict and never raises when matplotlib is missing.
* The report skeleton between ``BENCH_DATA_START`` / ``BENCH_DATA_END``
  can be rewritten in place by ``scripts/bench_gpu._regenerate_report``.

GPU tests are intentionally skipped on macOS (no nvc++). They will run
on the Spark-Claude session using the same file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._build_helper import BASELINES, CXX, render_baseline

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++17 compiler available")

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = REPO_ROOT / "src" / "engine.cpp"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _build_bench_binary(
    tmp_path: Path,
    *,
    extra_flags: list[str] | None = None,
    max_drones_override: int | None = 200,
    label: str = "bench",
) -> Path:
    """Compile a swarmevolve binary using frozen baselines, honouring flags.

    Mirrors ``_build_helper.build_matchup`` but allows passing the flags
    needed for M13 bench-mode exercises (``-DMAX_DRONES_OVERRIDE=…``,
    ``-fopenmp``, etc.).
    """
    scratch = tmp_path / f"build-{label}"
    a_dir = scratch / "src" / "a"
    b_dir = scratch / "src" / "b"
    render_baseline(BASELINES / "pursuit_v1.cpp", "TeamA", a_dir, "ai.cpp")
    render_baseline(BASELINES / "cluster_v1.cpp", "TeamB", b_dir, "ai.cpp")

    binary = scratch / "swarmevolve"
    cmd: list[str] = [
        CXX or "c++",
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wshadow",
        "-Wpedantic",
        "-Werror",
        "-Wno-unknown-pragmas",
        # Tolerate compiler-specific warning flags below (clang rejects
        # -Wno-stringop-overflow / -Wno-maybe-uninitialized as
        # unknown-warning-option under -Werror).
        "-Wno-unknown-warning-option",
        # GCC 13+ emits a false-positive -Wstringop-overflow at -O2 when
        # MAX_DRONES_OVERRIDE is set and memset length is runtime-computed.
        "-Wno-stringop-overflow",
        # GCC 13+ also emits a false-positive -Wmaybe-uninitialized at -O2
        # on src/engine.cpp:512 (the stack-allocated ``attack_events`` is
        # passed with event-count 0 to write_trace_line_v2 and never read
        # across the call boundary).
        "-Wno-maybe-uninitialized",
        f"-I{REPO_ROOT / 'src'}",
    ]
    if max_drones_override is not None:
        cmd.append(f"-DMAX_DRONES_OVERRIDE={max_drones_override}")
    if extra_flags:
        cmd.extend(extra_flags)
    cmd += [
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o",
        str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return binary


# ---------------------------------------------------------------------------
# Engine-side tests
# ---------------------------------------------------------------------------


def test_bench_binary_smoke(tmp_path: Path) -> None:
    """--benchmark emits one parseable JSON line per repeat, exits 0."""
    binary = _build_bench_binary(tmp_path, label="smoke")
    proc = subprocess.run(
        [
            str(binary),
            "--benchmark",
            "--drones-a",
            "10",
            "--drones-b",
            "10",
            "--max-ticks",
            "25",
            "--repeats",
            "3",
            "--seed",
            "42",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"bench smoke failed rc={proc.returncode} stderr={proc.stderr!r}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 3
    for i, line in enumerate(lines):
        obj = json.loads(line)
        for field in (
            "backend",
            "n_a",
            "n_b",
            "ticks_requested",
            "ticks_executed",
            "wall_ms",
            "per_tick_us",
            "arena_scale",
            "repeat",
            "seed",
            "outcome_code",
            "outcome_tag",
        ):
            assert field in obj, f"missing field {field} in {obj}"
        assert obj["repeat"] == i
        assert obj["n_a"] == 10
        assert obj["n_b"] == 10
        assert obj["ticks_requested"] == 25
        assert 0 < obj["ticks_executed"] <= 25
        assert obj["wall_ms"] >= 0
        assert obj["per_tick_us"] >= 0


def test_bench_no_side_effects(tmp_path: Path) -> None:
    """--benchmark must not create any files in its cwd."""
    binary = _build_bench_binary(tmp_path, label="noside")
    cwd = tmp_path / "run"
    cwd.mkdir()
    before = set(p.name for p in cwd.iterdir())
    proc = subprocess.run(
        [
            str(binary),
            "--benchmark",
            "--drones-a",
            "8",
            "--drones-b",
            "8",
            "--max-ticks",
            "20",
            "--repeats",
            "2",
            "--seed",
            "7",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr
    after = set(p.name for p in cwd.iterdir())
    assert before == after, f"benchmark leaked files: {after - before}"


def test_bench_rejects_record(tmp_path: Path) -> None:
    """--benchmark + --record is a usage error; no trace file is produced."""
    binary = _build_bench_binary(tmp_path, label="reject")
    trace = tmp_path / "illegal.jsonl"
    proc = subprocess.run(
        [
            str(binary),
            "--benchmark",
            "--record",
            str(trace),
            "--drones-a",
            "5",
            "--drones-b",
            "5",
            "--max-ticks",
            "10",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not trace.exists(), "trace file leaked from rejected CLI"


def _gcc_with_openmp() -> str | None:
    """Return a compiler path that supports -fopenmp (host OpenMP), or None.

    Homebrew LLVM ships libomp via a separate keg-only formula and at the
    time of writing requires extra flags; we skip the OMP test on Apple
    clang. g++ on Linux is the typical happy path. Setting
    ``SWARMEVOLVE_OMP_CXX`` in the environment forces a specific compiler
    (useful on CI).

    On macOS the Homebrew gcc chain is commonly broken against the SDK
    (``_Alignof`` parse errors in ``mach/_structs.h``). We probe-compile
    a trivial TU to skip gracefully instead of reporting a hard failure.
    """
    override = os.environ.get("SWARMEVOLVE_OMP_CXX")
    candidates: list[str] = []
    if override and shutil.which(override):
        candidates.append(override)
    # Prefer gcc/g++ because Apple clang's OpenMP story is fragile.
    for cand in ("g++-14", "g++-13", "g++-12", "g++"):
        if shutil.which(cand) and cand not in candidates:
            candidates.append(cand)

    # Probe each candidate by compiling a tiny OpenMP TU. Use a cache so
    # we only pay the 1-2s probe cost once per test session.
    for cc in candidates:
        if _probe_openmp(cc):
            return cc
    return None


_OMP_PROBE_CACHE: dict[str, bool] = {}


def _probe_openmp(cc: str) -> bool:
    cached = _OMP_PROBE_CACHE.get(cc)
    if cached is not None:
        return cached
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "probe.cpp"
        # Pull in <cstring>, <cstdlib>, <cstdio> so the probe exercises the
        # same libc / SDK headers that src/engine.cpp eventually touches.
        # On macOS the Homebrew gcc chain can fail here with _Alignof parse
        # errors in mach/_structs.h even though trivial <omp.h>-only probes
        # succeed; detect that mismatch up front and skip gracefully.
        src.write_text(
            "#include <omp.h>\n"
            "#include <cstring>\n#include <cstdlib>\n#include <cstdio>\n"
            "int main(){\n"
            "int s=0;\n#pragma omp parallel for reduction(+:s)\n"
            "for(int i=0;i<8;++i) s+=i; return s>0?0:1; }\n"
        )
        out = Path(td) / "probe"
        try:
            proc = subprocess.run(
                [cc, "-std=c++17", "-fopenmp", str(src), "-o", str(out)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            _OMP_PROBE_CACHE[cc] = False
            return False
        ok = proc.returncode == 0 and out.is_file()
    _OMP_PROBE_CACHE[cc] = ok
    return ok


@pytest.mark.skipif(_gcc_with_openmp() is None, reason="no g++ with OpenMP available")
def test_bench_backends_agree_at_small_n(tmp_path: Path) -> None:
    """CPU1 and CPU-OMP must produce byte-identical traces at small N.

    OpenMP is applied only to the query phase, where each iteration writes
    to a disjoint per-drone output slot. Any determinism regression in that
    pragma would surface here.
    """
    gpp = _gcc_with_openmp()
    assert gpp is not None  # for mypy
    # Use g++ (not CXX, which may be Homebrew clang) for both builds so the
    # only delta is the -fopenmp flag.
    bin_cpu1 = _build_bench_binary_with_cc(tmp_path, cc=gpp, label="cpu1", extra_flags=[])
    bin_omp = _build_bench_binary_with_cc(
        tmp_path, cc=gpp, label="cpu-omp", extra_flags=["-fopenmp"]
    )

    trace_cpu1 = tmp_path / "t_cpu1.jsonl"
    trace_omp = tmp_path / "t_omp.jsonl"
    for binary, trace in ((bin_cpu1, trace_cpu1), (bin_omp, trace_omp)):
        proc = subprocess.run(
            [
                str(binary),
                "--seed",
                "42",
                "--drones-a",
                "10",
                "--drones-b",
                "10",
                "--max-ticks",
                "40",
                "--record",
                str(trace),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode in (0, 1, 2), (
            f"match engine crashed: {proc.returncode} {proc.stderr!r}"
        )

    h_cpu1 = hashlib.sha256(trace_cpu1.read_bytes()).hexdigest()
    h_omp = hashlib.sha256(trace_omp.read_bytes()).hexdigest()
    assert h_cpu1 == h_omp, f"OpenMP regressed determinism: cpu1={h_cpu1} cpu_omp={h_omp}"


def _build_bench_binary_with_cc(
    tmp_path: Path, *, cc: str, label: str, extra_flags: list[str]
) -> Path:
    scratch = tmp_path / f"build-{label}"
    a_dir = scratch / "src" / "a"
    b_dir = scratch / "src" / "b"
    render_baseline(BASELINES / "pursuit_v1.cpp", "TeamA", a_dir, "ai.cpp")
    render_baseline(BASELINES / "cluster_v1.cpp", "TeamB", b_dir, "ai.cpp")
    binary = scratch / "swarmevolve"
    cmd = [
        cc,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wno-unknown-pragmas",
        f"-I{REPO_ROOT / 'src'}",
        *extra_flags,
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o",
        str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return binary


# ---------------------------------------------------------------------------
# Plotter tests (pure-python; no compiler needed, but gated behind CXX
# skip above for consistency — the pytestmark only applies to this module
# so a host without a compiler skips these too. That's fine: we always
# run these on the same host that measures.)
# ---------------------------------------------------------------------------


def _fake_summary() -> list[dict[str, object]]:
    return [
        {
            "backend": "cpu1",
            "n": 1000,
            "repeats": 5,
            "wall_ms_median": 10.0,
            "wall_ms_p25": 9.5,
            "wall_ms_p75": 10.5,
            "per_tick_us_median": 50.0,
            "ticks_executed_median": 200,
        },
        {
            "backend": "cpu_omp",
            "n": 1000,
            "repeats": 5,
            "wall_ms_median": 3.0,
            "wall_ms_p25": 2.8,
            "wall_ms_p75": 3.2,
            "per_tick_us_median": 15.0,
            "ticks_executed_median": 200,
        },
        {
            "backend": "gpu",
            "n": 1000,
            "repeats": 5,
            "wall_ms_median": 50.0,
            "wall_ms_p25": 48.0,
            "wall_ms_p75": 52.0,
            "per_tick_us_median": 250.0,
            "ticks_executed_median": 200,
        },
    ]


def test_bench_plot_does_not_crash_without_matplotlib(tmp_path: Path) -> None:
    """render_all must return a status dict regardless of matplotlib availability."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import bench_plot  # local import keeps module loads cheap

    data = {
        "schema_version": 1,
        "rows": [],
        "summary": _fake_summary(),
        "failures": [],
        "provenance": {},
    }
    (tmp_path / "bench_results.json").write_text(json.dumps(data))
    status = bench_plot.render_all(tmp_path)
    assert isinstance(status, dict)
    assert "matplotlib" in status
    if status["matplotlib"]:
        # When matplotlib is present, at least one plot should succeed.
        assert status["plots"].get("wall_ms_vs_N.png") is True
        # Speedup plot requires GPU+CPU rows; our fake has both.
        assert status["plots"].get("speedup_vs_N.png") is True


def test_bench_plot_missing_json_is_soft_error(tmp_path: Path) -> None:
    """A missing bench_results.json must not crash render_all."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import bench_plot

    status = bench_plot.render_all(tmp_path)
    assert isinstance(status, dict)
    assert status.get("plots") in (None, {}) or not any(status["plots"].values())


# ---------------------------------------------------------------------------
# Report regeneration test
# ---------------------------------------------------------------------------


def test_regenerate_report_rewrites_marker_region(tmp_path: Path) -> None:
    """_regenerate_report replaces the region between BENCH_DATA markers only."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import bench_gpu

    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    (bench_dir / "bench_results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rows": [],
                "summary": _fake_summary(),
                "failures": [],
                "provenance": {},
            }
        )
    )

    report = tmp_path / "perf_report.md"
    original = (
        "# header preserved\n\n"
        "<!-- BENCH_DATA_START -->\n"
        "OLD CONTENT\n"
        "<!-- BENCH_DATA_END -->\n\n"
        "# footer preserved\n"
    )
    report.write_text(original)

    bench_gpu._regenerate_report(bench_dir, report)

    text = report.read_text()
    assert "# header preserved" in text
    assert "# footer preserved" in text
    assert "OLD CONTENT" not in text
    assert "cpu1" in text and "gpu" in text
    # Markers retained exactly once each.
    assert text.count("<!-- BENCH_DATA_START -->") == 1
    assert text.count("<!-- BENCH_DATA_END -->") == 1


def test_perf_report_has_data_markers_and_conclusion() -> None:
    """The committed perf_report.md must have data markers and a landed conclusion.

    The BENCH_DATA markers allow the report regenerator to fill in the
    data table without touching the surrounding prose. The conclusion
    must be present (the CONCLUSION_PENDING placeholder must have been
    replaced by the Spark session's honest finding).
    """
    report = REPO_ROOT / "docs" / "perf_report.md"
    if not report.is_file():
        pytest.skip("perf_report.md not committed yet")
    text = report.read_text()
    assert "<!-- BENCH_DATA_START -->" in text
    assert "<!-- BENCH_DATA_END -->" in text
    assert "<!-- CONCLUSION_PENDING -->" not in text, (
        "perf_report.md still contains <!-- CONCLUSION_PENDING -->; "
        "the Spark session must replace it with the honest conclusion"
    )
    assert "Conclusion" in text, "perf_report.md must contain a conclusion"
