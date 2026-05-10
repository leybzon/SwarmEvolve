"""Tests for scripts/lint_ai_tokens.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Make scripts/ importable without a package install.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lint_ai_tokens import scan_file


def _write(tmp_path: Path, name: str, contents: str) -> Path:
    p = tmp_path / name
    p.write_text(contents)
    return p


def test_clean_file_has_no_violations(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "ok.cpp",
        """
        #include <cmath>
        namespace TeamA {
            void drone_ai(int my_id) {
                float v[4] = {0, 1, 2, 3};
                for (int i = 0; i < 4; ++i) v[i] = std::sqrt(v[i]);
            }
        }
        """,
    )
    assert scan_file(f) == []


def test_rejects_new(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.cpp", "void f() { int* p = new int(5); }\n")
    violations = scan_file(f)
    assert len(violations) == 1
    assert "new" in violations[0][1]


def test_rejects_malloc(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.cpp", "void f() { void* p = malloc(16); }\n")
    violations = scan_file(f)
    assert any("malloc" in v[2] for v in violations)


def test_rejects_std_vector(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.cpp", "#include <vector>\nstd::vector<int> v;\n")
    violations = scan_file(f)
    assert any("std::vector" in v[2] for v in violations)


def test_rejects_iostream(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.cpp", "#include <iostream>\nint main(){}\n")
    violations = scan_file(f)
    assert any("stream I/O" in v[2] for v in violations)


def test_ignores_banned_tokens_inside_comments(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "ok.cpp",
        """
        // new int(5) is banned but this is a comment
        /* std::vector also banned but commented out */
        void f() {}
        """,
    )
    assert scan_file(f) == []


def test_variable_named_new_ok(tmp_path: Path) -> None:
    # `new` as part of an identifier must not trip the regex.
    f = _write(tmp_path, "ok.cpp", "int my_new_value = 42;\nvoid f() {}\n")
    assert scan_file(f) == []


def test_inline_asm_rejected(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.cpp", 'void f() { __asm__("nop"); }\n')
    violations = scan_file(f)
    assert any("assembly" in v[2] for v in violations)


def test_multiple_violations_reported(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "bad.cpp",
        """
        #include <iostream>
        #include <vector>
        void f() {
            int* p = new int(1);
            std::vector<int> v;
        }
        """,
    )
    violations = scan_file(f)
    assert len(violations) >= 3
