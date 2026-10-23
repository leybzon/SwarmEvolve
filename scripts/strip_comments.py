#!/usr/bin/env python3
"""Deterministic C++ comment stripper for frozen bots.

Removes ``//`` and ``/* */`` comments from a C++ source and leaves every
string and character literal (including raw strings) byte-for-byte intact.
Used to freeze generated bots (bots/generation/PROTOCOL.md) and to show the
baselines to bot authors without their strategy notes.

Rules, applied in one left-to-right pass:

* ``// ...`` is removed up to the end of the line (a backslash before the
  newline continues the comment, as in the compiler's line splicing). A
  comment of the form ``// SPDX-License-Identifier: <id>`` is kept.
* ``/* ... */`` becomes one space, so tokens on either side stay apart.
* String literals (with ``u8``/``u``/``U``/``L`` prefixes and raw strings
  ``R"delim(...)delim"``) and character literals are copied unchanged. A
  ``'`` inside a number (``1'000``) is a digit separator, not a literal.
* A line that held a comment loses its trailing spaces and tabs (unless its
  end lies inside a multi-line raw string), and is dropped if nothing else
  is left on it (unless the line before it ends with a backslash, which
  would splice the next line in).

Lines without comments are copied unchanged. The output is a function of the
input alone, so stripping twice gives the same text as stripping once.

    python scripts/strip_comments.py bot.cpp            # to stdout
    python scripts/strip_comments.py bot.cpp -o out.cpp
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SPDX = re.compile(r"// SPDX-License-Identifier: \S+")
_RAW_PREFIXES = ("R", "u8R", "uR", "UR", "LR")
_IDENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


class StripError(ValueError):
    """The source has an unterminated comment or literal."""


class _Out:
    """Output text plus, per output line, whether it held a comment and whether
    its end lies inside a literal (a raw string spanning lines)."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.comment_lines: set[int] = set()
        self.inside_literal: set[int] = set()
        self.line = 0

    def code(self, text: str) -> None:
        self.parts.append(text)
        self.line += text.count("\n")

    def literal(self, text: str) -> None:
        first = self.line
        self.code(text)
        # Lines first..last-1 end inside the literal; their trailing
        # whitespace is literal content.
        self.inside_literal.update(range(first, self.line))

    def comment(self, replacement: str) -> None:
        self.comment_lines.add(self.line)
        self.parts.append(replacement)

    def render(self) -> str:
        lines = "".join(self.parts).split("\n")
        kept: list[str] = []
        for i, line in enumerate(lines):
            if i in self.comment_lines and i not in self.inside_literal:
                line = line.rstrip(" \t")
                splices_next = bool(kept) and kept[-1].endswith("\\")
                if not line and not splices_next:
                    continue
            kept.append(line)
        return "\n".join(kept)


def _ident_before(src: str, i: int) -> str:
    """The run of identifier characters ending just before ``src[i]``."""
    j = i
    while j > 0 and src[j - 1] in _IDENT_CHARS:
        j -= 1
    return src[j:i]


def _in_number(src: str, i: int) -> bool:
    """True if the ``'`` at ``src[i]`` is a digit separator inside a pp-number."""
    j = i
    while j > 0 and (src[j - 1] in _IDENT_CHARS or src[j - 1] in ".'"):
        j -= 1
    run = src[j:i]
    if not run:
        return False
    return run[0].isdigit() or (run[0] == "." and len(run) > 1 and run[1].isdigit())


def _end_of_quoted(src: str, i: int, quote: str) -> int:
    """Index just past the literal opening at ``src[i]`` (backslash escapes)."""
    j = i + 1
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            break
        j += 1
    raise StripError(f"unterminated {quote}-literal starting at offset {i}")


def _end_of_raw(src: str, i: int) -> int:
    """Index just past the raw string whose opening quote is ``src[i]``."""
    paren = src.find("(", i + 1)
    if paren == -1 or paren - i - 1 > 16:
        raise StripError(f"malformed raw string at offset {i}")
    delim = src[i + 1 : paren]
    close = src.find(")" + delim + '"', paren + 1)
    if close == -1:
        raise StripError(f"unterminated raw string at offset {i}")
    return close + len(delim) + 2


def _end_of_line_comment(src: str, i: int) -> int:
    """Index of the newline that ends the ``//`` comment at ``src[i]`` (or len)."""
    j = i
    while True:
        nl = src.find("\n", j)
        if nl == -1:
            return len(src)
        if src[j:nl].rstrip("\r").endswith("\\"):
            j = nl + 1  # line splice: the comment continues
            continue
        return nl


def strip_comments(source: str) -> str:
    """Return ``source`` without comments; see the module docstring."""
    out = _Out()
    i, n = 0, len(source)
    code_start = 0

    def flush(upto: int) -> None:
        if upto > code_start:
            out.code(source[code_start:upto])

    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = _end_of_line_comment(source, i)
            text = source[i:end]
            if _SPDX.fullmatch(text.rstrip()):
                i = end
                continue  # kept: stays part of the code run
            flush(i)
            out.comment("")
            i = code_start = end
        elif c == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            if end == -1:
                raise StripError(f"unterminated block comment at offset {i}")
            flush(i)
            out.comment(" ")
            i = code_start = end + 2
        elif c == '"':
            raw = _ident_before(source, i) in _RAW_PREFIXES
            end = _end_of_raw(source, i) if raw else _end_of_quoted(source, i, '"')
            flush(i)
            out.literal(source[i:end])
            i = code_start = end
        elif c == "'" and not _in_number(source, i):
            end = _end_of_quoted(source, i, "'")
            flush(i)
            out.literal(source[i:end])
            i = code_start = end
        else:
            i += 1
    flush(n)
    return out.render()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)
    try:
        text = strip_comments(args.source.read_text(encoding="utf-8"))
    except StripError as exc:
        print(f"strip_comments: {args.source}: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
