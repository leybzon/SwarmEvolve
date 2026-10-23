"""scripts/strip_comments.py: comments go, literals and behaviour stay."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fitness
from strip_comments import StripError, main, strip_comments

CASES = [
    ("int a; // x\nint b;\n", "int a;\nint b;\n"),
    ("// only\nint a;\n", "int a;\n"),
    ("int a = 1/*c*/+2;\n", "int a = 1 +2;\n"),
    ("  /* one\n two */  int z;\n", "     int z;\n"),
    ('const char* p = "// no /* no */";\n', 'const char* p = "// no /* no */";\n'),
    ("char c = '/'; int d = 1'000'000; // t\n", "char c = '/'; int d = 1'000'000;\n"),
    ("int h = 0x1'F; // t\n", "int h = 0x1'F;\n"),
    ("char e = '\\''; // t\n", "char e = '\\'';\n"),
    ("auto u = u8\"//k\"; auto l = L'/';\n", "auto u = u8\"//k\"; auto l = L'/';\n"),
    ('auto r = R"x(// in /* raw */)x"; // out\n', 'auto r = R"x(// in /* raw */)x";\n'),
    ('auto r = R"(a  \n// in  \n)"; // out\n', 'auto r = R"(a  \n// in  \n)";\n'),
    ('const char* s = "a\\"// b"; // c\n', 'const char* s = "a\\"// b";\n'),
    ("int a; // continued \\\nstill comment\nint b;\n", "int a;\nint b;\n"),
    ("#define A 1 \\\n// c\nint x;\n", "#define A 1 \\\n\nint x;\n"),
    (
        "// SPDX-License-Identifier: MIT\n// other\nint a;\n",
        "// SPDX-License-Identifier: MIT\nint a;\n",
    ),
    ("int a;    \nint b; // c\n", "int a;    \nint b;\n"),
    ("x = a/b; y = a / *p;\n", "x = a/b; y = a / *p;\n"),
]


@pytest.mark.parametrize(("source", "expected"), CASES)
def test_strips_comments_and_keeps_literals(source: str, expected: str) -> None:
    assert strip_comments(source) == expected


@pytest.mark.parametrize(("source", "_expected"), CASES)
def test_idempotent(source: str, _expected: str) -> None:
    once = strip_comments(source)
    assert strip_comments(once) == once


@pytest.mark.parametrize("bad", ["int a; /* open", 'const char* s = "open;\n', "char c = 'x;\n"])
def test_unterminated_input_is_an_error(bad: str) -> None:
    with pytest.raises(StripError):
        strip_comments(bad)


def test_cli_writes_output(tmp_path: Path) -> None:
    src = tmp_path / "a.cpp"
    src.write_text("int a; // x\n")
    assert main([str(src), "-o", str(tmp_path / "b.cpp")]) == 0
    assert (tmp_path / "b.cpp").read_text() == "int a;\n"


@pytest.mark.parametrize("name", ["pursuit_v1", "cluster_v1"])
def test_stripped_baseline_plays_identically(name: str, tmp_path: Path) -> None:
    """Trusted repo code, so the host backend is fine here."""
    original = REPO_ROOT / "src" / "baselines" / f"{name}.cpp"
    stripped = tmp_path / f"{name}.cpp"
    stripped.write_text(strip_comments(original.read_text()))
    assert "//" not in stripped.read_text().replace("// SPDX-License-Identifier: MIT", "")
    other_name = "cluster_v1" if name == "pursuit_v1" else "pursuit_v1"
    other = REPO_ROOT / "src" / "baselines" / f"{other_name}.cpp"

    def rows(path: Path) -> list[dict]:
        res = fitness.evaluate_fitness(path, other, n_matches=20, seed_base=7300000, backend="host")
        return [{k: v for k, v in r.items() if k != "wall_ms"} for r in res.per_match]

    assert rows(stripped) == rows(original)
