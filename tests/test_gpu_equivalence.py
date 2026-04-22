"""CPU↔GPU per-platform equivalence test (M11).

SPECIFICATION §7.6 allows per-platform determinism only — the clang/g++
CPU trace and the nvc++ GPU trace may differ within a small FP epsilon.
In practice (see ``docs/profiling/*.md``), on seed=42 with the frozen
baselines the traces are byte-identical on Spark (GB10, aarch64) across
both builds; this test enforces the weaker "FP-epsilon equivalence"
contract so a future compiler bump cannot silently regress into
trajectory drift.

The test is skipped unless ``nvc++`` is on ``PATH``. CI that lacks an
NVIDIA toolchain (macOS, most Linux laptops) therefore sees a single
skip line rather than a hard fail.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._build_helper import (
    BASELINES,
    ENGINE_SRC,
    REPO_ROOT,
    build_matchup,
    render_baseline,
)

NVCPP = shutil.which("nvc++")
pytestmark = pytest.mark.skipif(
    NVCPP is None,
    reason="nvc++ not available on this host; GPU equivalence test is Spark-only",
)


# Tolerances: derived from SPEC §7.6 (FMA/reduction-order slop).
# Positions in world units — 0.5 is well under one drone-width.
POS_ABS_TOL = 0.5
# Velocities are internal (not in trace) so no tolerance needed there.
# Integer fields (cooldown, alive, id, tick) must match exactly.


def _build_gpu_binary(tmp_path: Path) -> Path:
    """Compile engine + baselines with nvc++ -acc=gpu -gpu=mem:managed."""
    scratch = tmp_path / "build_gpu"
    a_dir = scratch / "src" / "a"
    b_dir = scratch / "src" / "b"
    render_baseline(BASELINES / "stationary_v1.cpp", "TeamA", a_dir, "ai.cpp")
    render_baseline(BASELINES / "cluster_v1.cpp", "TeamB", b_dir, "ai.cpp")

    binary = scratch / "swarmevolve_gpu"
    cmd = [
        NVCPP or "nvc++",
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wshadow",
        "-Wpedantic",
        "-Werror",
        # NB: no -Wno-unknown-pragmas; nvc++ rejects that flag and natively
        # understands `#pragma acc`.
        f"-I{REPO_ROOT / 'src'}",
        "-acc=gpu",
        "-gpu=mem:managed",
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o",
        str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return binary


def _record(binary: Path, seed: int, max_ticks: int, record: Path) -> tuple[str, int]:
    """Run ``binary`` producing a trace at ``record``. Returns (outcome, ticks)."""
    proc = subprocess.run(
        [str(binary), "--seed", str(seed), "--max-ticks", str(max_ticks), "--record", str(record)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1, 2), f"engine rc={proc.returncode} stderr={proc.stderr!r}"
    last = proc.stdout.strip().splitlines()[-1]
    fields = dict(tok.split("=") for tok in last.split())
    return fields["outcome"], int(fields["ticks"])


def _compare_traces(cpu: Path, gpu: Path, pos_tol: float = POS_ABS_TOL) -> list[str]:
    """Return list of divergence messages; empty list means FP-epsilon match."""
    errs: list[str] = []
    with cpu.open() as fc, gpu.open() as fg:
        for lineno, (lc, lg) in enumerate(zip(fc, fg, strict=False), start=1):
            jc = json.loads(lc)
            jg = json.loads(lg)
            if jc["tick"] != jg["tick"]:
                errs.append(f"line {lineno}: tick mismatch {jc['tick']} vs {jg['tick']}")
                return errs
            for team in ("team_a", "team_b"):
                a = jc[team]
                b = jg[team]
                if len(a) != len(b):
                    errs.append(f"line {lineno} {team}: team size mismatch")
                    return errs
                for i, (dc, dg) in enumerate(zip(a, b, strict=False)):
                    # Integer-exact fields.
                    for k in ("id", "cooldown", "alive"):
                        if dc[k] != dg[k]:
                            errs.append(f"line {lineno} {team}#{i} {k}: {dc[k]} vs {dg[k]}")
                    # FP-tolerant fields.
                    for k in ("x", "y"):
                        if not math.isclose(dc[k], dg[k], abs_tol=pos_tol):
                            errs.append(
                                f"line {lineno} {team}#{i} {k}: "
                                f"{dc[k]:.4f} vs {dg[k]:.4f} "
                                f"(|Δ|={abs(dc[k] - dg[k]):.4e} > {pos_tol})"
                            )
                    if errs and len(errs) > 20:
                        return errs
        # Ensure file lengths match.
        tail_c = fc.read()
        tail_g = fg.read()
        if tail_c or tail_g:
            errs.append("trace length mismatch")
    return errs


def test_cpu_gpu_traces_match_within_fp_epsilon(tmp_path):
    """stationary vs swarm_center, seed=42, 200 ticks — positions within POS_ABS_TOL."""
    cpu_bin = build_matchup(tmp_path, "stationary_v1.cpp", "cluster_v1.cpp")
    gpu_bin = _build_gpu_binary(tmp_path)

    cpu_trace = tmp_path / "cpu.jsonl"
    gpu_trace = tmp_path / "gpu.jsonl"

    cpu_outcome, cpu_ticks = _record(cpu_bin, seed=42, max_ticks=200, record=cpu_trace)
    gpu_outcome, gpu_ticks = _record(gpu_bin, seed=42, max_ticks=200, record=gpu_trace)

    assert cpu_outcome == gpu_outcome, (
        f"outcome divergence: CPU={cpu_outcome} GPU={gpu_outcome} "
        f"(CPU ticks={cpu_ticks} GPU ticks={gpu_ticks})"
    )
    assert cpu_ticks == gpu_ticks, f"tick-count divergence: {cpu_ticks} vs {gpu_ticks}"

    errs = _compare_traces(cpu_trace, gpu_trace)
    assert not errs, "CPU/GPU trace divergence:\n  " + "\n  ".join(errs[:10])


def test_cpu_gpu_multi_seed_outcome_parity(tmp_path):
    """Across seeds 0..4, outcome+tick-count must match (positions can drift)."""
    cpu_bin = build_matchup(tmp_path, "stationary_v1.cpp", "cluster_v1.cpp")
    gpu_bin = _build_gpu_binary(tmp_path)

    # Reuse one directory for trace churn to avoid filling the fs.
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    for seed in range(5):
        cpu_trace = trace_dir / f"cpu_{seed}.jsonl"
        gpu_trace = trace_dir / f"gpu_{seed}.jsonl"
        cpu_o, cpu_t = _record(cpu_bin, seed=seed, max_ticks=300, record=cpu_trace)
        gpu_o, gpu_t = _record(gpu_bin, seed=seed, max_ticks=300, record=gpu_trace)
        assert cpu_o == gpu_o, f"seed={seed}: outcome {cpu_o} vs {gpu_o}"
        assert cpu_t == gpu_t, f"seed={seed}: ticks {cpu_t} vs {gpu_t}"
