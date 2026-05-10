// Macro-expanded loop. The regex backend does NOT see the `for` inside
// the macro body, so if the macro hides an unbounded loop the injector
// might silently miss it. With --fail-on-macro-loops the caller gets a
// structured error; without it we warn and continue (the sandbox
// timeout in M8 is the backstop).
//
// NOTE: clang-format is disabled around the macro so the all-caps
// identifier ``FOREVER(BODY) {`` stays on one logical line. The macro
// detector in scripts/inject_guards.py looks for the regex
// ``\b[A-Z_]+\s*\([^)]*\)\s*\{`` and a multi-line backslash-continued
// definition would split that pattern and silently disarm the test.
// clang-format off
#define FOREVER(BODY) { for (;;) { BODY } }
// clang-format on

int main() {
    int i = 0;
    FOREVER(++i; if (i > 3) break;)
    return i;
}
