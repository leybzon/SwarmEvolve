#!/usr/bin/env python3
"""
Reject banned tokens in AI source files (src/a/*, src/b/*).

Enforces SPECIFICATION.md §2.2 "Forbidden Operations" at commit time so an LLM-
or human-authored AI module cannot accidentally pull in heap allocation, STL
containers, threading, I/O, or syscalls.

Run directly:
    python scripts/lint_ai_tokens.py src/a/team_a_ai.cpp [...]

Used as a pre-commit hook. Exit codes:
    0  - all files clean
    1  - at least one banned token found
    2  - usage error
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

# Token classes. Each entry is (regex, human-readable reason). Regexes match
# against stripped-of-comments source text. Word boundaries are enforced so
# that e.g. "std::vector" matches but "my_vector" does not.
BANNED: list[tuple[str, str]] = [
    (r"\bnew\s+[A-Za-z_]", "heap allocation via `new`"),
    (r"\bdelete\s*[\[(]?", "heap deallocation via `delete`"),
    (r"\bmalloc\s*\(", "heap allocation via malloc"),
    (r"\bcalloc\s*\(", "heap allocation via calloc"),
    (r"\brealloc\s*\(", "heap allocation via realloc"),
    (r"\bfree\s*\(", "heap deallocation via free"),
    (r"std::vector\b", "STL container std::vector"),
    (r"std::string\b", "STL container std::string"),
    (r"std::map\b", "STL container std::map"),
    (r"std::unordered_map\b", "STL container std::unordered_map"),
    (r"std::list\b", "STL container std::list"),
    (r"std::deque\b", "STL container std::deque"),
    (r"std::thread\b", "threading not allowed in AI code"),
    (r"std::mutex\b", "threading not allowed in AI code"),
    (r"std::atomic\b", "threading primitives not allowed in AI code"),
    (r"<thread>", "threading header not allowed"),
    (r"<mutex>", "threading header not allowed"),
    (r"<atomic>", "threading header not allowed"),
    (r"<fstream>", "file I/O not allowed in AI code"),
    (r"<iostream>", "stream I/O not allowed in AI code"),
    (r"<filesystem>", "filesystem access not allowed in AI code"),
    (r"\bfopen\s*\(", "file I/O not allowed in AI code"),
    (r"\bsystem\s*\(", "syscall not allowed in AI code"),
    (r"\bpopen\s*\(", "process control not allowed in AI code"),
    (r"\bexec[lv]p?e?\s*\(", "exec syscalls not allowed in AI code"),
    (r"\basm\b", "inline assembly not allowed in AI code"),
    (r"__asm__", "inline assembly not allowed in AI code"),
]

# Strip // and /* ... */ comments before scanning.
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(src: str) -> str:
    src = _BLOCK_COMMENT.sub("", src)
    src = _LINE_COMMENT.sub("", src)
    return src


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return a list of (line_number, matched_text, reason) for violations."""
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = _strip_comments(text)
    # Keep a mapping from offset in stripped text to original line number by
    # scanning the original text and computing line starts once.
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    violations: list[tuple[int, str, str]] = []
    for pattern, reason in BANNED:
        for m in re.finditer(pattern, stripped):
            # Map stripped offset back to original line by counting newlines in
            # the original up to an offset approximately equal to m.start().
            # Approximate mapping is acceptable here — the linter's output is
            # advisory and the precise offset is not security-critical.
            orig_offset = _approx_original_offset(text, stripped, m.start())
            line = _offset_to_line(line_starts, orig_offset)
            violations.append((line, m.group(0), reason))
    return violations


def _approx_original_offset(original: str, stripped: str, stripped_offset: int) -> int:
    """Best-effort mapping. Walk both strings in lockstep skipping comments."""
    oi = 0
    si = 0
    n_o = len(original)
    while si < stripped_offset and oi < n_o:
        # block comment
        if original.startswith("/*", oi):
            end = original.find("*/", oi + 2)
            oi = n_o if end == -1 else end + 2
            continue
        # line comment
        if original.startswith("//", oi):
            end = original.find("\n", oi)
            oi = n_o if end == -1 else end
            continue
        oi += 1
        si += 1
    return oi


def _offset_to_line(line_starts: list[int], offset: int) -> int:
    # Binary search would be fine; linear is trivially fast for our file sizes.
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1  # 1-indexed


def main(argv: Iterable[str]) -> int:
    files = [Path(p) for p in argv]
    if not files:
        print("usage: lint_ai_tokens.py FILE [FILE ...]", file=sys.stderr)
        return 2

    any_bad = False
    for path in files:
        if not path.is_file():
            print(f"skip (not a file): {path}", file=sys.stderr)
            continue
        violations = scan_file(path)
        if violations:
            any_bad = True
            for line, text, reason in violations:
                print(f"{path}:{line}: banned token `{text}` — {reason}")

    if any_bad:
        print(
            "\nAI source must not contain banned tokens. "
            "See SPECIFICATION.md §2.2 and scripts/lint_ai_tokens.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
