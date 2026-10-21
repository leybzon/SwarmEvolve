"""Every engine compile path takes its flags from scripts/compile_flags.py.

ADR-0001 defines one compile-flag policy. Before it was centralised the
sandbox entrypoint compiled with stricter flags than fitness.py (no
-Wno-unused-*), so a candidate could pass on the host and fail in the
sandbox, and the GCC-only false-positive suppressions were missing from
the orchestrator path. These tests pin both the shared definition and its
use at every call site.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import compile_flags
import fitness
import orchestrator

from tests import _build_helper

BASELINES = REPO_ROOT / "src" / "baselines"
FLAGS_MODULE = REPO_ROOT / "scripts" / "compile_flags.py"

# Literal flags that only scripts/compile_flags.py may spell out.
_OWNED_FLAGS = (
    "-std=c++17",
    "-Wpedantic",
    "-Wno-unknown-pragmas",
    "-Wno-maybe-uninitialized",
    "-Wno-stringop-overflow",
    "-Wno-unused-variable",
)


def _contains_run(cmd: list[str], flags: list[str]) -> bool:
    """True if ``flags`` appears in ``cmd`` as one contiguous run."""
    n = len(flags)
    return any(cmd[i : i + n] == flags for i in range(len(cmd) - n + 1))


# ---------------------------------------------------------------------------
# The shared definition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("compiler", "family"),
    [
        ("/nonexistent/g++-13", "gcc"),
        ("/nonexistent/clang++", "clang"),
        ("/nonexistent/nvc++", "nvhpc"),
        ("/nonexistent/icpx", "unknown"),
    ],
)
def test_family_falls_back_to_executable_name(compiler, family):
    assert compiler_family_uncached(compiler) == family


def compiler_family_uncached(compiler: str) -> str:
    compile_flags.compiler_family.cache_clear()
    return compile_flags.compiler_family(compiler)


@pytest.mark.skipif(shutil.which("c++") is None, reason="no c++ alias")
def test_family_asks_the_compiler_itself():
    """``c++`` is an alias; the banner, not the name, decides."""
    banner = subprocess.run(["c++", "--version"], capture_output=True, text=True).stdout.lower()
    expected = "clang" if "clang" in banner else "gcc"
    assert compiler_family_uncached("c++") == expected


def test_gcc_gets_false_positive_suppressions_clang_does_not():
    gcc = compile_flags.engine_flags("/nonexistent/g++")
    clang = compile_flags.engine_flags("/nonexistent/clang++")
    for flag in compile_flags.GCC_FALSE_POSITIVES:
        assert flag in gcc
        assert flag not in clang
    for flags in (gcc, clang):
        for flag in (*compile_flags.WARNINGS, *compile_flags.UNUSED_RELAXED):
            assert flag in flags
        assert "-Wno-unknown-pragmas" in flags


def test_nvcpp_omits_unknown_pragmas_flag():
    flags = compile_flags.engine_flags("/nonexistent/nvc++")
    assert "-Wno-unknown-pragmas" not in flags
    assert "-Werror" in flags


def test_strict_flags_have_no_adr_relaxations():
    strict = compile_flags.strict_warning_flags("/nonexistent/g++")
    assert not set(compile_flags.UNUSED_RELAXED) & set(strict)


def test_cli_matches_python_api():
    for args, optimize in (([], True), (["--no-optimize"], False)):
        out = subprocess.run(
            [sys.executable, str(FLAGS_MODULE), "--compiler", "/nonexistent/g++", *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert out == compile_flags.engine_flags("/nonexistent/g++", optimize=optimize)


# ---------------------------------------------------------------------------
# Python compile paths
# ---------------------------------------------------------------------------


_REAL_RUN = subprocess.run


class _Capture:
    """Records engine compiles; passes other calls (``--version`` probes) through."""

    def __init__(self):
        self.cmds: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        cmd = [str(c) for c in cmd]
        if not any(c.endswith("engine.cpp") for c in cmd):
            return _REAL_RUN(cmd, *args, **kwargs)
        self.cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.mark.parametrize("compiler", ["/nonexistent/g++", "/nonexistent/clang++"])
def test_fitness_compile_uses_shared_flags(tmp_path, monkeypatch, compiler):
    cap = _Capture()
    monkeypatch.setattr(subprocess, "run", cap)
    fitness._compile(BASELINES / "pursuit_v1.cpp", BASELINES / "cluster_v1.cpp", tmp_path, compiler)
    assert _contains_run(cap.cmds[-1], compile_flags.engine_flags(compiler))


@pytest.mark.parametrize("compiler", ["/nonexistent/g++", "/nonexistent/clang++"])
def test_orchestrator_compile_uses_shared_flags(tmp_path, monkeypatch, compiler):
    cap = _Capture()
    monkeypatch.setattr(subprocess, "run", cap)
    orchestrator.compile_matchup(
        tmp_path, BASELINES / "pursuit_v1.cpp", BASELINES / "cluster_v1.cpp", compiler
    )
    assert _contains_run(cap.cmds[-1], compile_flags.engine_flags(compiler))


def test_build_helper_uses_shared_flags(tmp_path, monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(subprocess, "run", cap)
    _build_helper.build_matchup(tmp_path, "pursuit_v1.cpp", "cluster_v1.cpp")
    compiler = _build_helper.CXX or "c++"
    assert _contains_run(cap.cmds[-1], compile_flags.engine_flags(compiler))


# ---------------------------------------------------------------------------
# Makefile and sandbox entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
@pytest.mark.parametrize(
    ("target", "var", "compiler", "optimize"),
    [
        ("build-linux-cpu", "CXX_LINUX", "g++", True),
        ("build-linux-cpu-omp", "CXX_LINUX", "g++", True),
        ("build-macos", "CXX_MACOS", "clang++", True),
        ("bench-build-cpu1", "CXX_LINUX", "g++", True),
        ("build/test_types", "CXX_MACOS", "g++", False),
    ],
)
def test_makefile_targets_use_shared_flags(target, var, compiler, optimize):
    out = subprocess.run(
        ["make", "-n", "-B", target, f"{var}={compiler}", f"PYTHON={sys.executable}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    compile_lines = [line.split() for line in out.splitlines() if line.startswith(compiler + " ")]
    assert compile_lines, out
    expected = compile_flags.engine_flags(compiler, optimize=optimize)
    assert all(_contains_run(cmd, expected) for cmd in compile_lines), out


def test_entrypoint_compiles_with_shared_flags():
    text = (REPO_ROOT / "docker" / "entrypoint.sh").read_text()
    assert 'CXXFLAGS_SHARED=$(python3 "$ASSETS/compile_flags.py" --compiler g++)' in text
    assert re.search(r"^g\+\+ \$CXXFLAGS_SHARED \\$", text, re.MULTILINE)
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile.sandbox").read_text()
    assert "COPY scripts/compile_flags.py /opt/swarmevolve/compile_flags.py" in dockerfile


# ---------------------------------------------------------------------------
# Nothing else spells the flags out
# ---------------------------------------------------------------------------


def _code_files() -> list[Path]:
    files = [REPO_ROOT / "Makefile"]
    for sub in ("scripts", "tests", "docker"):
        for pattern in ("*.py", "*.sh"):
            files += sorted((REPO_ROOT / sub).rglob(pattern))
    return [f for f in files if f not in (FLAGS_MODULE, Path(__file__).resolve())]


def _python_flag_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{path.name}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in _OWNED_FLAGS
    ]


def _shell_flag_literals(path: Path) -> list[str]:
    hits = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        code = line.split("#", 1)[0]
        hits += [f"{path.name}:{lineno}: {f}" for f in _OWNED_FLAGS if f in code.split()]
    return hits


def test_no_compile_path_defines_its_own_flags():
    hits: list[str] = []
    for path in _code_files():
        if path.suffix == ".py":
            hits += _python_flag_literals(path)
        else:
            hits += _shell_flag_literals(path)
    assert hits == []


# ---------------------------------------------------------------------------
# Host / sandbox agreement (needs the sandbox image)
# ---------------------------------------------------------------------------

_UNUSED_LOCAL_AI = (
    '#include "../ai_abi.h"\n'
    '#include "../types.h"\n'
    "namespace TeamA {\n"
    "#pragma acc routine seq\n"
    "void drone_ai(int my_id, const GameParams* params, const AllyState* allies,\n"
    "              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],\n"
    "              float* my_memory, Action* out_action) {\n"
    "    int scratch = 0;  // unused: allowed by ADR-0001\n"
    "    out_action->velocity.x = 0.0f;\n"
    "    out_action->velocity.y = 0.0f;\n"
    "    out_action->target_id = -1;\n"
    "}\n"
    "}\n"
)


def test_unused_variable_candidate_compiles_in_sandbox(tmp_path):
    import sandbox

    from tests.test_sandbox_ok import IMAGE

    runtime = sandbox.detect_runtime() if sandbox.runtime_available() else None
    if runtime is None or not sandbox.image_present(IMAGE, runtime=runtime):
        pytest.skip(f"sandbox image '{IMAGE}' not available")
    base = Path.home() / ".pytest-sandbox"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tmp_path_in(base))
    try:
        src = work / "src"
        src.mkdir()
        (src / "a.cpp").write_text(_UNUSED_LOCAL_AI)
        (src / "b.cpp").write_text(
            (BASELINES / "cluster_v1.cpp").read_text().replace("TEAM_NS_PLACEHOLDER", "TeamB")
        )
        out = work / "out"
        out.mkdir()
        out.chmod(0o777)
        result = sandbox.run_match_in_sandbox(src / "a.cpp", src / "b.cpp", out, image=IMAGE)
        assert result.ok, (result.status, (out / "compile.log").read_text())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def tmp_path_in(base: Path) -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="flags_", dir=str(base))
