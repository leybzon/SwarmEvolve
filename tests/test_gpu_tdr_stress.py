"""TDR (Timeout Detection and Recovery) stress test — M11 step 8.

Verifies that a **guard-bounded but intentionally inefficient** drone AI
runs to completion on the GPU without triggering a driver reset.

Per SPEC §5 / IMPLEMENTATION_PLAN §13: all runtime-bounded loops in AI
code must be guard-injected by the orchestrator; compile-time-bounded
loops (like ``for (int k = 0; k < 5000; ++k)`` in the fixture) are safe
by construction. This test exercises the "compile-time-bounded but heavy"
path — if the GB10 driver ever killed our kernel mid-match we would see
a non-zero exit code from the swarmevolve binary, a CUDA error printed
to stderr, or (worst case) a complete hang.

The test is skipped on hosts without ``nvc++``; laptop CI sees a single
skip line.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._build_helper import ENGINE_SRC, REPO_ROOT

NVCPP = shutil.which("nvc++")
pytestmark = pytest.mark.skipif(
    NVCPP is None,
    reason="nvc++ not available on this host; TDR stress test is Spark-only",
)


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tdr_stress_ai.cpp"


def _render(src: Path, namespace: str, dest: Path) -> None:
    """Substitute TEAM_NS_PLACEHOLDER → namespace and write to dest."""
    text = src.read_text()
    assert "TEAM_NS_PLACEHOLDER" in text, f"{src} is missing placeholder"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text.replace("TEAM_NS_PLACEHOLDER", namespace))


def _build_gpu_stress(tmp_path: Path) -> Path:
    a = tmp_path / "src" / "a" / "ai.cpp"
    b = tmp_path / "src" / "b" / "ai.cpp"
    _render(FIXTURE, "TeamA", a)
    _render(FIXTURE, "TeamB", b)
    binary = tmp_path / "swarmevolve_tdr"
    subprocess.run(
        [
            NVCPP,
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wshadow",
            "-Wpedantic",
            "-Werror",
            "-acc=gpu",
            "-gpu=mem:managed",
            f"-I{REPO_ROOT / 'src'}",
            str(ENGINE_SRC),
            str(a),
            str(b),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


def test_heavy_ai_does_not_trigger_tdr(tmp_path):
    """Run a 50-drone x 300-tick match with the heavy-work AI on both sides.

    The per-tick cost is dominated by the fixture's 5000-iter
    trig-accumulate loop, but each individual kernel launch still
    completes in well under the ~2 s TDR window. We cap max-ticks at 300
    (not 1000) because the heavy-work matchup has no combat → no early
    termination, and we want the test to finish in a couple of seconds
    of wall-clock time rather than ~6 s at 1000 ticks.
    """
    binary = _build_gpu_stress(tmp_path)
    proc = subprocess.run(
        [str(binary), "--seed", "42", "--max-ticks", "300", "--drones-a", "50", "--drones-b", "50"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Accept the three legitimate outcome exit codes (0, 1, 2).
    assert proc.returncode in (0, 1, 2), (
        f"binary crashed: rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    # CUDA driver errors typically print a line containing "CUDA error"
    # or "device driver" to stderr. Surface any such evidence.
    assert "CUDA error" not in proc.stderr, f"CUDA error in stderr: {proc.stderr}"
    assert "driver" not in proc.stderr.lower() or "TDR" not in proc.stderr, (
        f"driver-reset evidence in stderr: {proc.stderr}"
    )

    # Confirm the match actually ran to completion (not cut short by a
    # mid-match kernel kill). With stationary-ish velocities neither
    # team takes damage, so final outcome should be DRAW with both
    # teams still full after 300 ticks.
    last = proc.stdout.strip().splitlines()[-1]
    fields = dict(tok.split("=") for tok in last.split())
    assert fields["outcome"] == "DRAW", f"unexpected outcome: {last}"
    assert int(fields["ticks"]) == 300, f"match ended early at tick {fields['ticks']}"
    assert int(fields["a_alive"]) == 50
    assert int(fields["b_alive"]) == 50
