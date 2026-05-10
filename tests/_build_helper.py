"""Shared build helpers for integration tests (M3 baselines, M4 determinism).

Kept under a leading-underscore module name so pytest does not collect it as
a test file. Code duplication between ``test_baselines.py`` and
``test_determinism.py`` was the original motivation.

The pipeline is:

1. Locate a C++17 compiler (``$CXX`` → Homebrew LLVM → system ``g++`` /
   ``clang++``). On hosts with no compiler, callers skip their tests via
   ``pytestmark = pytest.mark.skipif(CXX is None, ...)``.
2. Render a frozen baseline from ``src/baselines/*.cpp`` — replacing the
   ``TEAM_NS_PLACEHOLDER`` token with ``TeamA``/``TeamB`` and rewriting
   ``#include "../foo.h"`` → ``#include "foo.h"`` so the ``-Isrc`` flag
   resolves them from the live headers.
3. Compile the live engine (``src/engine.cpp``) plus the two rendered AI
   translation units into a single per-matchup binary under ``tmp_path``.

This intentionally mirrors the Makefile flags (``-std=c++17 -O2 -Wall
-Wextra -Wshadow -Wpedantic -Werror``) so regressions caught by the
production build also fire in tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = REPO_ROOT / "src" / "engine.cpp"
BASELINES = REPO_ROOT / "src" / "baselines"
PLACEHOLDER = "TEAM_NS_PLACEHOLDER"


def find_compiler() -> str | None:
    """Return a path to a working C++17 compiler, or None."""
    env = os.environ.get("CXX")
    if env and shutil.which(env):
        return env
    for cand in (
        "/opt/homebrew/opt/llvm/bin/clang++",
        "/usr/local/opt/llvm/bin/clang++",
        "g++",
        "clang++",
    ):
        if shutil.which(cand) or Path(cand).is_file():
            return cand
    return None


CXX = find_compiler()


def render_baseline(src_path: Path, namespace: str, dest_dir: Path, dest_name: str) -> Path:
    """Copy a frozen baseline into ``dest_dir/dest_name`` with namespace substitution.

    Also rewrites ``#include "../foo.h"`` → ``#include "foo.h"`` so the
    ``-I<repo>/src`` flag (rather than the relative path from
    ``src/baselines/``) resolves the headers.
    """
    text = src_path.read_text()
    if PLACEHOLDER not in text:
        raise AssertionError(f"{src_path} missing {PLACEHOLDER}")
    rendered = text.replace(PLACEHOLDER, namespace)
    rendered = rendered.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    rendered = rendered.replace('#include "../types.h"', '#include "types.h"')
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / dest_name
    out.write_text(rendered)
    return out


def build_matchup(tmp_path: Path, team_a_baseline: str, team_b_baseline: str) -> Path:
    """Render two baseline files into scratch src/{a,b}/ + compile an engine.

    Returns the path to the produced binary.
    """
    scratch = tmp_path / "build"
    a_dir = scratch / "src" / "a"
    b_dir = scratch / "src" / "b"
    render_baseline(BASELINES / team_a_baseline, "TeamA", a_dir, "ai.cpp")
    render_baseline(BASELINES / team_b_baseline, "TeamB", b_dir, "ai.cpp")

    binary = scratch / "swarmevolve"
    cmd = [
        CXX or "c++",
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wshadow",
        "-Wpedantic",
        "-Werror",
        # OpenACC pragmas must be silently ignored by non-nvc++ compilers
        # (Apple clang already does; g++ warns, and -Werror would fail).
        "-Wno-unknown-pragmas",
        f"-I{REPO_ROOT / 'src'}",
        str(ENGINE_SRC),
        str(a_dir / "ai.cpp"),
        str(b_dir / "ai.cpp"),
        "-o",
        str(binary),
    ]
    # GCC 13 (Ubuntu 24.04 default) emits two false positives at -O2 on
    # src/engine.cpp under -Werror that the same code compiles cleanly
    # under Apple clang and earlier g++ versions. Mirror the suppressions
    # applied in the production Makefile (LINUX_GCC_EXTRA) and in
    # ``scripts/fitness.py`` so test builds match production behaviour.
    #   * -Wmaybe-uninitialized: src/engine.cpp:512 passes the stack-allocated
    #     ``AttackEvent attack_events[2*MAX_DRONES]`` to write_trace_line_v2()
    #     at tick 0 with event-count 0; GCC can't see across the call.
    #   * -Wstringop-overflow: at -O2 the optimizer can't prove
    #     ``num_drones_*`` (int) is non-negative and conservatively flags
    #     memset paths.
    cxx_basename = Path(cmd[0]).name
    if "g++" in cxx_basename and "clang" not in cxx_basename:
        idx = cmd.index("-Werror") + 1
        cmd[idx:idx] = ["-Wno-maybe-uninitialized", "-Wno-stringop-overflow"]
    subprocess.run(cmd, check=True, capture_output=True)
    return binary


def run_match(binary: Path, seed: int, record: Path | None = None) -> tuple[str, int, int, int]:
    """Run one match; return (outcome, a_alive, b_alive, ticks)."""
    args: list[str] = ["--seed", str(seed)]
    if record is not None:
        args += ["--record", str(record)]
    proc = subprocess.run([str(binary), *args], capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2):
        raise AssertionError(
            f"engine crashed: rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    last_line = proc.stdout.strip().splitlines()[-1]
    fields = dict(tok.split("=") for tok in last_line.split())
    return (fields["outcome"], int(fields["a_alive"]), int(fields["b_alive"]), int(fields["ticks"]))
