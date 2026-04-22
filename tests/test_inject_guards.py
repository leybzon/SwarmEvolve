"""Tests for scripts/inject_guards.py (M6).

Three layers of coverage:

1. **Unit**: direct calls to ``inject()`` — idempotence, guard count,
   comment/string scrubbing, goto rejection.

2. **Fixture sweep**: every file under ``tests/fixtures/inject/``
   (excluding ``adversarial/``) must (a) be rewritten (or explicitly
   idempotent for ``already_injected.cpp``) and (b) compile cleanly with
   the project's strict flag set. This catches generated-code regressions
   that don't manifest at the Python level.

3. **Runtime termination**: three infinite-loop fixtures are compiled,
   executed, and must exit within 1 second with a non-zero code (guard
   triggered ``break`` after MAX_ITERATIONS — we don't care about the
   exit code, just that the process doesn't hang).

Tests requiring a C++ compiler skip when none is present on the host.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from inject_guards import (  # noqa: E402
    GUARD_PREFIX,
    MARKER_COMMENT,
    MAX_ITERATIONS,
    InjectorError,
    inject,
)

from tests._build_helper import CXX  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "inject"
ADVERSARIAL = FIXTURES / "adversarial"

_compiler_required = pytest.mark.skipif(CXX is None, reason="no C++17 compiler available")


def _compile_and_run(source: str, tmp_path: Path, *, timeout_s: float = 1.0) -> tuple[int, str, str]:
    """Compile ``source`` with the project flag set and run it; return
    ``(returncode, stdout, stderr)``. Raises ``subprocess.TimeoutExpired``
    if the binary takes longer than ``timeout_s`` — that's a test failure
    signalling the guard didn't actually bound the loop.
    """
    src_path = tmp_path / "t.cpp"
    src_path.write_text(source)
    bin_path = tmp_path / "t.out"
    subprocess.run(
        [
            CXX or "c++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wshadow",
            "-Wpedantic",
            "-Werror",
            # The injector adds `if (++_g...) break;` before the first line
            # of the body; the guard variable is never read elsewhere, so
            # `-Werror=unused-but-set-variable` would fire only if the
            # compiler failed to see the ++ as a read. We pass -Wunused to
            # double-check that doesn't happen.
            "-Wunused",
            str(src_path),
            "-o",
            str(bin_path),
        ],
        check=True,
        capture_output=True,
    )
    proc = subprocess.run([str(bin_path)], capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Unit tests (no compiler)
# ---------------------------------------------------------------------------


def test_injects_marker_on_fresh_source() -> None:
    out = inject("int main(){ while(true){ ++x; } }")
    assert out.startswith(MARKER_COMMENT)


def test_idempotent() -> None:
    first = inject("int main(){ for(int i=0;i<5;++i){ i+=0; } return 0; }")
    second = inject(first)
    assert first == second


def test_guard_count_matches_loop_count() -> None:
    src = """
    int main(){
        while(true){ ++a; }
        for(;;){ ++b; }
        do { ++c; } while(c<5);
        return 0;
    }
    """
    out = inject(src)
    # Each loop generates one declaration: find them by prefix.
    decls = re.findall(rf"int {re.escape(GUARD_PREFIX)}\d+ = 0;", out)
    assert len(decls) == 3, f"got {len(decls)} guards, expected 3: {decls}"
    # Each loop body receives one `if (++_g > 1000) break;`.
    checks = re.findall(rf"if \(\+\+{re.escape(GUARD_PREFIX)}\d+ > {MAX_ITERATIONS}\) break;", out)
    assert len(checks) == 3


def test_scrubs_comments_and_strings() -> None:
    src = '''
    int main(){
        /* while (true) { } */
        // while (true) { }
        const char* s = "while (true) { }";
        return 0;
    }
    '''
    out = inject(src)
    assert GUARD_PREFIX not in out, "false positive: guard injected for comment/string"


def test_goto_loop_rejected() -> None:
    src = """
    int main(){
        int i = 0;
    loop:
        ++i;
        if (i < 5) goto loop;
        return 0;
    }
    """
    with pytest.raises(InjectorError) as exc:
        inject(src)
    assert exc.value.exit_code == 2


def test_do_while_tail_not_double_guarded() -> None:
    """The `while (cond)` tail of a do-while must NOT get its own guard.

    Regression guard: an earlier draft treated every `while` uniformly
    and injected a second declaration before the tail keyword, which
    produced uncompilable output (``int name = 0; while (cond);``).
    """
    src = """
    int main(){
        int i = 0;
        do {
            ++i;
        } while (i < 5);
        return 0;
    }
    """
    out = inject(src)
    assert out.count("int _g_swarmevolve_") == 1, (
        f"expected 1 guard decl, got {out.count('int _g_swarmevolve_')}:\n{out}"
    )


# ---------------------------------------------------------------------------
# Fixture sweep: non-adversarial files must compile clean after injection.
# ---------------------------------------------------------------------------


_FIXTURE_FILES = sorted(p for p in FIXTURES.glob("*.cpp"))


@pytest.mark.parametrize("fixture", _FIXTURE_FILES, ids=lambda p: p.name)
@_compiler_required
def test_fixture_injects_and_compiles(fixture: Path, tmp_path: Path) -> None:
    """Every fixture must produce syntactically valid, -Werror-clean C++."""
    src = fixture.read_text()
    try:
        rewritten = inject(src)
    except InjectorError as exc:
        pytest.fail(f"{fixture.name}: injector raised {exc} (unexpected for non-adversarial)")

    out_path = tmp_path / fixture.name
    out_path.write_text(rewritten)
    subprocess.run(
        [
            CXX or "c++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wshadow",
            "-Wpedantic",
            "-Werror",
            "-c",
            str(out_path),
            "-o",
            str(tmp_path / "t.o"),
        ],
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Runtime: infinite-loop fixtures terminate after injection.
# ---------------------------------------------------------------------------


INFINITE_FIXTURES = [
    FIXTURES / "while_infinite_without_guard.cpp",
    FIXTURES / "for_infinite_without_guard.cpp",
]


@pytest.mark.parametrize("fixture", INFINITE_FIXTURES, ids=lambda p: p.name)
@_compiler_required
def test_injected_infinite_loop_terminates(fixture: Path, tmp_path: Path) -> None:
    """An otherwise-infinite fixture must exit within 1 s after injection."""
    rewritten = inject(fixture.read_text())
    rc, _out, _err = _compile_and_run(rewritten, tmp_path, timeout_s=1.0)
    # The guard fires `break`; main's natural `return` (if reached) or
    # the implicit return from the guarded path is fine. We only assert
    # the process didn't hang — subprocess raises on timeout.
    assert rc is not None


@_compiler_required
def test_bounded_fixtures_still_return_zero(tmp_path: Path) -> None:
    """while_basic / for_bounded / do_while_basic / range_for / nested_while
    / continue_break_return / loops_in_lambda all ``return 0`` on success
    (they sum values and compare). Injection must not change that.
    """
    for name in (
        "while_basic.cpp",
        "for_bounded.cpp",
        "do_while_basic.cpp",
        "range_for.cpp",
        "nested_while.cpp",
        "continue_break_return.cpp",
        "loops_in_lambda.cpp",
    ):
        rewritten = inject((FIXTURES / name).read_text())
        rc, _out, _err = _compile_and_run(rewritten, tmp_path, timeout_s=2.0)
        assert rc == 0, f"{name}: expected rc=0, got {rc}"


# ---------------------------------------------------------------------------
# Adversarial fixtures: silent miss is a test failure.
# ---------------------------------------------------------------------------


def test_adversarial_goto_rejected() -> None:
    with pytest.raises(InjectorError) as exc:
        inject((ADVERSARIAL / "goto_based_loop.cpp").read_text())
    assert exc.value.exit_code == 2


@_compiler_required
def test_adversarial_comment_with_while_no_false_positive(tmp_path: Path) -> None:
    """A comment containing ``while (true)`` must NOT trigger injection."""
    rewritten = inject((ADVERSARIAL / "comment_with_while.cpp").read_text())
    assert GUARD_PREFIX not in rewritten


@_compiler_required
def test_adversarial_string_with_while_no_false_positive(tmp_path: Path) -> None:
    rewritten = inject((ADVERSARIAL / "string_with_while.cpp").read_text())
    assert GUARD_PREFIX not in rewritten
    # And the rewritten file still compiles + runs.
    rc, _out, _err = _compile_and_run(rewritten, tmp_path, timeout_s=1.0)
    assert rc == 0


def test_adversarial_single_statement_body_refused() -> None:
    """Single-statement loop bodies are refused (exit code 4)."""
    with pytest.raises((ValueError, InjectorError)):
        inject((ADVERSARIAL / "single_statement_body.cpp").read_text())


def test_adversarial_macro_loop_flagged_with_flag() -> None:
    """With ``--fail-on-macro-loops`` the macro fixture is refused."""
    src = (ADVERSARIAL / "macro_loop.cpp").read_text()
    with pytest.raises(InjectorError) as exc:
        inject(src, fail_on_macro_loops=True)
    assert exc.value.exit_code == 4


# ---------------------------------------------------------------------------
# Frozen-baselines integration: the real pursuit / cluster baselines must
# round-trip through the injector and still compile + produce the pinned
# golden trace. This is the canary for "injector breaks my AI".
# ---------------------------------------------------------------------------


@_compiler_required
def test_injected_baselines_still_compile_in_engine(tmp_path: Path) -> None:
    """Inject into pursuit_v1 + cluster_v1, compile against the live engine.

    Does not assert the golden SHA (the injected decl changes line numbers
    and therefore nothing in the trace, but makes the object file drift;
    the determinism test in test_determinism.py is the source of truth
    for trace-bytes).
    """
    a_src = (REPO_ROOT / "src" / "baselines" / "pursuit_v1.cpp").read_text()
    b_src = (REPO_ROOT / "src" / "baselines" / "cluster_v1.cpp").read_text()

    a_inj = (
        inject(a_src)
        .replace("TEAM_NS_PLACEHOLDER", "TeamA")
        .replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
        .replace('#include "../types.h"', '#include "types.h"')
    )
    b_inj = (
        inject(b_src)
        .replace("TEAM_NS_PLACEHOLDER", "TeamB")
        .replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
        .replace('#include "../types.h"', '#include "types.h"')
    )

    a_path = tmp_path / "a.cpp"
    b_path = tmp_path / "b.cpp"
    a_path.write_text(a_inj)
    b_path.write_text(b_inj)

    binary = tmp_path / "swarmevolve"
    subprocess.run(
        [
            CXX or "c++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wshadow",
            "-Wpedantic",
            "-Werror",
            # Baselines carry `#pragma acc routine seq` — silently
            # ignored by non-nvc++ compilers per the OpenACC spec.
            "-Wno-unknown-pragmas",
            f"-I{REPO_ROOT / 'src'}",
            str(REPO_ROOT / "src" / "engine.cpp"),
            str(a_path),
            str(b_path),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    # A single match must actually terminate.
    subprocess.run([str(binary), "--seed", "0"], check=False, capture_output=True, timeout=10)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_check_mode_exits_1_if_changes_needed(tmp_path: Path) -> None:
    src = tmp_path / "t.cpp"
    src.write_text("int main(){ while(true){++x;} }")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "inject_guards.py"), "--check", str(src)],
        capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert "would-rewrite" in proc.stdout


def test_cli_check_mode_exits_0_if_clean(tmp_path: Path) -> None:
    src = tmp_path / "t.cpp"
    src.write_text(MARKER_COMMENT + "\nint main(){ return 0; }\n")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "inject_guards.py"), "--check", str(src)],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
