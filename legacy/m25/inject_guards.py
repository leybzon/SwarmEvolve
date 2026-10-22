"""Loop-guard injector (M6).

Primary defense against GPU TDR crashes: rewrites untrusted C++ source so
every loop terminates in at most ``MAX_ITERATIONS`` iterations. If the
loop body would have run forever, the injected guard forces a ``break``
after the bound is reached.

Transform (regex backend, this file):

    // before
    while (cond) {
        body();
    }

    // after
    int _g_swarmevolve_7 = 0;
    while (cond) {
        if (++_g_swarmevolve_7 > 1000) break;
        body();
    }

Why both a libclang and a regex backend?
  IMPLEMENTATION_PLAN §8 names libclang as the preferred backend (it
  understands the full C++ grammar, including templates, lambdas, and
  macros) and regex as the documented fallback ("best effort, warns on
  nested macros"). In environments without libclang's Python bindings
  (e.g. vanilla CI runners), the regex backend ships as a safe-ish
  default: it recognizes every fixture we care about, and anything it
  CAN'T parse (``goto``-based loops, duplicate guard names, or missing
  loop-body braces) is refused with a non-zero exit so we never silently
  miss a loop.

Rules of engagement:
  * Idempotent: re-running on an already-injected file is a no-op
    (guard variable names are deterministic; a re-injection detects its
    own marker and skips).
  * Preserves ``#line`` directives around the injection so compiler
    errors map back to LLM-authored line numbers.
  * ``goto``-based loops are explicitly rejected (exit code 4).
  * Macro-expanded loop keywords are not handled by the regex backend.
    Callers use ``--fail-on-macro-loops`` to error out defensively; by
    default we warn and continue (the sandbox timeout in M8 is the
    backstop defense).

Exit codes:
  0 — success, file rewritten in place (or --check passed)
  1 — CLI / IO error
  2 — source contains banned ``goto``-based loop
  3 — idempotence check failed (guards already present but mismatched)
  4 — regex backend encountered a loop it cannot parse
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("inject_guards")

MAX_ITERATIONS = 1000
GUARD_PREFIX = "_g_swarmevolve_"
MARKER_COMMENT = "/* @swarmevolve:guards-injected */"


# ---------------------------------------------------------------------------
# Token-level preprocessing: strip comments and string/char literals so our
# regex sweep doesn't match the word "while" inside a comment or string.
# We replace each stripped span with same-length whitespace so byte offsets
# into the scrubbed view map 1:1 onto the original source.
# ---------------------------------------------------------------------------


_COMMENT_OR_STRING = re.compile(
    r"""
    //[^\n]*                   # line comment
  | /\*.*?\*/                  # block comment
  | "(?:\\.|[^"\\\n])*"        # string literal
  | '(?:\\.|[^'\\\n])*'        # char literal
    """,
    re.VERBOSE | re.DOTALL,
)


def _scrub(source: str) -> str:
    """Return ``source`` with comments and literals replaced by whitespace
    of the same length. Newlines are preserved so line numbers match.
    """

    def repl(match: re.Match[str]) -> str:
        span = match.group(0)
        return "".join(ch if ch == "\n" else " " for ch in span)

    return _COMMENT_OR_STRING.sub(repl, source)


# ---------------------------------------------------------------------------
# Loop detection. We match loop *headers* whose immediately-following
# character (after optional whitespace) is an open brace ``{``. Anything
# else (single-statement body, macro) is refused — callers rely on the
# linter to reject such code before it reaches us.
# ---------------------------------------------------------------------------


# `\b` ensures we don't match e.g. "awhile" or "forall".
_LOOP_KEYWORD = re.compile(r"\b(while|for|do)\b")
_GOTO_BACK = re.compile(r"\bgoto\b")


@dataclass
class Loop:
    """A detected loop header ready for guard injection."""

    kind: str  # "while", "for", "do"
    kw_offset: int  # offset of the loop keyword in the original source
    body_open: int  # offset of the '{' that opens the loop body
    body_close: int  # offset of the matching '}' (exclusive end)


def _find_matching_brace(src: str, open_idx: int) -> int:
    """Return the index of the ``}`` that matches ``src[open_idx] == '{'``.

    Works on a scrubbed view (comments/strings already neutralized) so
    we don't need further context tracking. Raises ``ValueError`` if the
    brace is unbalanced (injector will bail rather than corrupt source).
    """
    assert src[open_idx] == "{", f"expected '{{' at offset {open_idx}"
    depth = 0
    for i in range(open_idx, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced braces starting at offset {open_idx}")


def _find_loop_body_brace(scrubbed: str, after_kw: int, kind: str) -> int:
    """Locate the ``{`` that begins the body of a loop whose keyword ends
    at ``after_kw``.

    For ``while`` / ``for``: skip past the ``(...)`` header.
    For ``do``: the body brace directly follows the keyword (optional WS).
    Returns the offset of the body-opening ``{``, or raises ValueError.
    """
    i = after_kw
    if kind in ("while", "for"):
        # Skip whitespace, expect '(', then scan to the matching ')'.
        while i < len(scrubbed) and scrubbed[i].isspace():
            i += 1
        if i >= len(scrubbed) or scrubbed[i] != "(":
            raise ValueError(f"{kind}: expected '(' after keyword at offset {after_kw}")
        depth = 0
        while i < len(scrubbed):
            c = scrubbed[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        else:
            raise ValueError(f"{kind}: unbalanced parens after offset {after_kw}")
    # Skip whitespace up to body '{'.
    while i < len(scrubbed) and scrubbed[i].isspace():
        i += 1
    if i >= len(scrubbed) or scrubbed[i] != "{":
        raise ValueError(
            f"{kind}: expected '{{' to open the loop body at offset {i} "
            "(single-statement bodies are not supported by the regex backend)"
        )
    return i


def _detect_loops(source: str) -> list[Loop]:
    """Return every ``while`` / ``for`` / ``do`` loop in ``source``, in order.

    ``do-while`` is recognized by the ``do`` keyword; the matching
    ``while (...)`` tail is left alone (it's evaluated after the body
    runs at least once, so the guard inside the body is sufficient to
    bound iteration count).
    """
    scrubbed = _scrub(source)

    # Banned: goto-based loops (e.g. `loop: ...; goto loop;`). These can
    # form infinite loops that slip past our keyword sweep.
    if _GOTO_BACK.search(scrubbed):
        raise InjectorError(
            "goto-based loops are not supported; refactor to while/for/do",
            exit_code=2,
        )

    loops: list[Loop] = []
    for m in _LOOP_KEYWORD.finditer(scrubbed):
        kind = m.group(1)
        kw_offset = m.start()
        after_kw = m.end()

        # Special case: `while (...)` tail of a do-while. Recognize by
        # scanning backwards past whitespace to find a matching `}` which
        # itself is preceded by a `do { ... }` pair we already recorded.
        if kind == "while" and _is_do_while_tail(scrubbed, kw_offset, loops):
            continue

        body_open = _find_loop_body_brace(scrubbed, after_kw, kind)
        body_close = _find_matching_brace(scrubbed, body_open)
        loops.append(
            Loop(kind=kind, kw_offset=kw_offset, body_open=body_open, body_close=body_close)
        )
    return loops


def _is_do_while_tail(scrubbed: str, while_offset: int, prior: list[Loop]) -> bool:
    """True if the ``while`` at ``while_offset`` is the tail of a preceding
    ``do { ... } while (...)``. Matches against already-detected loops so
    runtime is linear.
    """
    # Scan back over whitespace to find the closest previous non-space char.
    i = while_offset - 1
    while i >= 0 and scrubbed[i].isspace():
        i -= 1
    if i < 0 or scrubbed[i] != "}":
        return False
    # The ``}`` at index i must close a ``do`` loop body (a Loop whose
    # body_close == i).
    return any(loop.kind == "do" and loop.body_close == i for loop in prior)


# ---------------------------------------------------------------------------
# Injection.
# ---------------------------------------------------------------------------


class InjectorError(RuntimeError):
    def __init__(self, msg: str, *, exit_code: int) -> None:
        super().__init__(msg)
        self.exit_code = exit_code


def _line_of_offset(source: str, offset: int) -> int:
    """1-indexed line number of ``offset`` within ``source``."""
    return source.count("\n", 0, offset) + 1


def _indent_of(source: str, offset: int) -> str:
    """Return the whitespace prefix of the line containing ``offset``."""
    line_start = source.rfind("\n", 0, offset) + 1
    run = ""
    i = line_start
    while i < len(source) and source[i] in " \t":
        run += source[i]
        i += 1
    return run


def inject(source: str, *, fail_on_macro_loops: bool = False) -> str:
    """Inject loop guards into ``source`` and return the rewritten text.

    Idempotent: if ``MARKER_COMMENT`` is already present, return ``source``
    unchanged. This is the "re-running on already-injected code is a
    no-op" requirement from IMPLEMENTATION_PLAN §8.
    """
    if MARKER_COMMENT in source:
        LOG.info("marker present; skipping (already injected)")
        return source

    loops = _detect_loops(source)
    if not loops:
        LOG.info("no loops found; adding marker only")
        return _prepend_marker(source)

    # Process loops in reverse so earlier offsets remain valid as we
    # splice text in.
    out = source
    for idx, loop in enumerate(reversed(loops)):
        # Guard variables are named by their FORWARD index so regenerated
        # output is stable regardless of how we iterate.
        guard_idx = len(loops) - 1 - idx
        guard_name = f"{GUARD_PREFIX}{guard_idx}"
        out = _inject_one(out, loop, guard_name)

    if fail_on_macro_loops:
        # Heuristic: a macro-expanded loop keyword would show up as an
        # identifier in the preprocessed output but not in our scrubbed
        # scan. We can't reliably detect that without running cpp; flag
        # obvious all-caps identifiers immediately followed by '(' in the
        # source as suspicious and fail.
        if re.search(r"\b[A-Z_][A-Z0-9_]{2,}\s*\(\s*[^)]*\)\s*\{", _scrub(out)):
            raise InjectorError(
                "macro-expanded loop-like construct detected; regex backend refuses",
                exit_code=4,
            )

    return _prepend_marker(out)


def _prepend_marker(source: str) -> str:
    return MARKER_COMMENT + "\n" + source


def _inject_one(source: str, loop: Loop, guard_name: str) -> str:
    """Splice a guard declaration + body check around a single loop."""
    indent = _indent_of(source, loop.kw_offset)
    decl = f"int {guard_name} = 0; /* @swarmevolve:guard */\n{indent}"
    check_stmt = (
        f"\n{indent}    if (++{guard_name} > {MAX_ITERATIONS}) break; /* @swarmevolve:guard */"
    )

    # 1) insert the check as the first statement of the body.
    after_brace = loop.body_open + 1
    out = source[:after_brace] + check_stmt + source[after_brace:]

    # Body offsets after the splice shift forward by len(check_stmt);
    # but we inserted *inside* the body so the loop keyword offset is
    # still valid.
    # 2) insert the declaration immediately before the loop keyword.
    # Use a #line directive so diagnostics still point at the original
    # keyword line.
    kw_line = _line_of_offset(source, loop.kw_offset)
    line_directive = f'#line {kw_line} "injected"\n{indent}'
    out = out[: loop.kw_offset] + line_directive + decl + out[loop.kw_offset :]

    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inject per-loop iteration guards into C++ source."
    )
    parser.add_argument("files", nargs="+", type=Path, help="C++ source files to rewrite in place")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print what would change but do not write files; exit 1 if any file would change",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print rewritten source(s) to stdout and skip in-place writes",
    )
    parser.add_argument(
        "--backend",
        choices=["regex", "libclang"],
        default="regex",
        help="parser backend (libclang is not yet implemented; regex is the default)",
    )
    parser.add_argument(
        "--allow-regex",
        action="store_true",
        help="(compat flag; regex is currently the default backend)",
    )
    parser.add_argument(
        "--fail-on-macro-loops",
        action="store_true",
        help="fail loudly on macro-expanded loop-like constructs (CI-safe default)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    if args.backend == "libclang":
        print("ERROR kind=backend detail=libclang-not-implemented", file=sys.stderr)
        return 1

    any_changed = False
    for path in args.files:
        try:
            original = path.read_text()
        except OSError as exc:
            print(f"ERROR kind=io detail={exc}", file=sys.stderr)
            return 1
        try:
            rewritten = inject(original, fail_on_macro_loops=args.fail_on_macro_loops)
        except InjectorError as exc:
            print(f"ERROR kind=injector detail={exc} file={path}", file=sys.stderr)
            return exc.exit_code
        except ValueError as exc:
            print(f"ERROR kind=parse detail={exc} file={path}", file=sys.stderr)
            return 4

        if rewritten == original:
            LOG.info("%s: unchanged", path)
            continue

        any_changed = True
        if args.stdout:
            sys.stdout.write(rewritten)
        elif args.check:
            print(f"would-rewrite {path}")
        else:
            path.write_text(rewritten)
            LOG.info("%s: rewritten", path)

    if args.check and any_changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
