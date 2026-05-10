"""Happy-path tests for :mod:`scripts.sandbox` (M8).

Skips cleanly when no container runtime is available (CI on Linux runs
these; macOS without Colima / Docker Desktop skips). Also skips when
the sandbox image hasn't been built yet (``make docker-build`` is
one-time and not worth re-running for every pytest invocation).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sandbox

BASELINES = REPO_ROOT / "src" / "baselines"
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "golden" / "seed42_pursuit_vs_cluster.jsonl"
IMAGE = os.environ.get("SANDBOX_IMAGE", sandbox.DEFAULT_IMAGE)


def _runtime_or_skip() -> str:
    try:
        return sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")


def _image_or_skip(runtime: str) -> None:
    if not sandbox.image_present(IMAGE, runtime=runtime):
        pytest.skip(
            f"sandbox image '{IMAGE}' not found; run `make docker-build` first",
        )


@pytest.fixture(scope="module")
def colima_safe_tmp() -> Path:
    """Colima/virtiofs only shares ``$HOME``; host ``/tmp`` is VM-local
    and appears as root-owned to a rootless container. Put temp dirs
    under ``$HOME/.pytest-sandbox`` so the bind mount is writable by the
    non-root sandbox user.
    """
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    # mkdtemp under our home-rooted base; pytest's default tmp_path is
    # under /private/var/folders which Colima doesn't share.
    d = Path(tempfile.mkdtemp(prefix="m8_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _render_baselines(dst_dir: Path) -> tuple[Path, Path]:
    """Render the frozen placeholder baselines into ``TeamA``/``TeamB``
    forms and write them to ``dst_dir``. Returns (a.cpp, b.cpp)."""
    src_dir = dst_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    a_src = (BASELINES / "pursuit_v1.cpp").read_text().replace("TEAM_NS_PLACEHOLDER", "TeamA")
    b_src = (BASELINES / "cluster_v1.cpp").read_text().replace("TEAM_NS_PLACEHOLDER", "TeamB")

    a = src_dir / "a.cpp"
    b = src_dir / "b.cpp"
    a.write_text(a_src)
    b.write_text(b_src)
    return a, b


# -----------------------------------------------------------------------
# flag-set unit tests (no container launch)
# -----------------------------------------------------------------------


def test_sandbox_flags_match_spec():
    """IMPLEMENTATION_PLAN §M8 mandates an exact flag set; drift is a
    silent security regression, so pin it by equality. Regex-free
    assertion so the flags are visible in the diff."""
    flags = sandbox.SANDBOX_FLAGS
    assert "--rm" in flags
    assert "--network=none" in flags
    assert "--cap-drop=ALL" in flags
    assert "--read-only" in flags
    assert "--memory=512m" in flags
    assert "--pids-limit=64" in flags
    assert "--cpus=2" in flags
    # security-opt and user come as paired args
    assert "no-new-privileges" in flags
    assert "65534:65534" in flags
    # tmpfs line carries size + exec (needed for the engine binary)
    tmpfs_specs = [flags[i + 1] for i, f in enumerate(flags) if f == "--tmpfs"]
    assert len(tmpfs_specs) == 1 and "/tmp:" in tmpfs_specs[0]
    assert "size=" in tmpfs_specs[0]


def test_build_command_has_expected_shape(tmp_path):
    a = tmp_path / "a.cpp"
    b = tmp_path / "b.cpp"
    a.write_text("//\n")
    b.write_text("//\n")
    out = tmp_path / "out"
    cmd = sandbox.build_command(
        team_a_src=a,
        team_b_src=b,
        out_dir=out,
        image="test:latest",
        engine_args=["--seed", "7"],
        runtime="docker",
        timeout=10.0,
    )
    assert cmd[0:2] == ["docker", "run"]
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert cmd[-1] == "7"
    assert cmd[-2] == "--seed"
    # image name is immediately before engine args
    assert cmd[-3] == "test:latest"
    # the staging mount maps to /work/src:ro
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert any(m.endswith(":/work/src:ro") for m in mounts)
    assert any(m.endswith(":/work/out") for m in mounts)


def test_build_command_rejects_missing_team(tmp_path):
    out = tmp_path / "out"
    (tmp_path / "b.cpp").write_text("//")
    with pytest.raises(sandbox.SandboxError):
        sandbox.build_command(
            team_a_src=tmp_path / "does_not_exist.cpp",
            team_b_src=tmp_path / "b.cpp",
            out_dir=out,
            image="test",
            engine_args=None,
            runtime="docker",
            timeout=10,
        )


# -----------------------------------------------------------------------
# integration: container must be present
# -----------------------------------------------------------------------


def test_baseline_match_runs_and_matches_golden(colima_safe_tmp):
    """pursuit_v1 vs cluster_v1 @ seed=42 must produce the byte-identical
    golden trace — proves the sandbox compile+run pipeline is
    deterministic and equivalent to the host-side harness."""
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    a, b = _render_baselines(colima_safe_tmp / "inputs")
    out_dir = colima_safe_tmp / "out_golden"

    result = sandbox.run_match_in_sandbox(
        a,
        b,
        out_dir,
        image=IMAGE,
        timeout=60.0,
        runtime=rt,
        engine_args=["--seed", "42", "--max-ticks", "1000"],
    )

    assert result.ok, f"sandbox match failed: rc={result.returncode} status={result.status}"
    assert result.status["status"] == "ok"
    assert result.status["engine_rc"] == 2  # DRAW

    trace = (out_dir / "trace.jsonl").read_bytes()
    golden = GOLDEN.read_bytes()
    assert trace == golden, "sandbox trace diverged from golden"


def test_missing_input_yields_structured_failure(colima_safe_tmp):
    """The wrapper must catch a missing source file *before* starting
    the container and raise a clean SandboxError."""
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    out_dir = colima_safe_tmp / "out_missing"
    out_dir.mkdir(parents=True, exist_ok=True)
    good = colima_safe_tmp / "good.cpp"
    good.write_text("//")

    with pytest.raises(sandbox.SandboxError):
        sandbox.run_match_in_sandbox(
            colima_safe_tmp / "does_not_exist.cpp",
            good,
            out_dir,
            image=IMAGE,
            runtime=rt,
            timeout=30.0,
        )


def test_compile_failure_reports_structured_status(colima_safe_tmp):
    """A garbage AI source should trip -Werror and be reported as
    status=compile_failed (not a silent crash)."""
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # Render a valid opponent + a broken candidate.
    _, b = _render_baselines(colima_safe_tmp / "inputs_bad")
    bad_a = colima_safe_tmp / "inputs_bad" / "src" / "a.cpp"
    bad_a.write_text(
        '#include "../ai_abi.h"\n'
        '#include "../types.h"\n'
        "namespace TeamA { // missing closing brace on purpose\n"
        "#pragma acc routine seq\n"
        "void drone_ai(int, const GameParams*, const AllyState*,\n"
        "              const EnemyState*, const float[][MSG_SIZE],\n"
        "              float*, Action* out) { out->target_id = -1;\n"
        "} // still inside namespace\n"
    )

    out_dir = colima_safe_tmp / "out_compile_fail"
    result = sandbox.run_match_in_sandbox(
        bad_a,
        b,
        out_dir,
        image=IMAGE,
        runtime=rt,
        timeout=60.0,
        engine_args=["--seed", "0", "--max-ticks", "10"],
    )
    assert not result.ok
    assert result.status["status"] == "compile_failed", (
        f"expected compile_failed got {result.status}"
    )
    # Compile log should exist for triage.
    assert (out_dir / "compile.log").is_file()
    assert (out_dir / "compile.log").read_text().strip(), "compile.log is empty"


def test_cli_reports_image_missing(colima_safe_tmp, monkeypatch):
    """Invoking scripts/sandbox.py with a non-existent image should exit
    with a structured SandboxError message (21), not a traceback."""
    _ = _runtime_or_skip()
    a, b = _render_baselines(colima_safe_tmp / "inputs_cli")
    out_dir = colima_safe_tmp / "out_cli"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "sandbox.py"),
            "--team-a",
            str(a),
            "--team-b",
            str(b),
            "--out-dir",
            str(out_dir),
            "--image",
            "definitely-not-an-image:xyz",
            "--timeout",
            "10",
            "--",
            "--seed",
            "0",
            "--max-ticks",
            "10",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 21, proc.stderr
    assert "sandbox error" in (proc.stdout + proc.stderr).lower()
