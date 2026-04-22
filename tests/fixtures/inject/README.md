# Loop-Guard Injector Fixtures

Input corpus for `tests/test_inject_guards.py`.

Files here are **input-only** C++ snippets. Tests assert properties of
the injector's output (guard present, compiles, terminates within 1 s)
rather than byte-exact output, so whitespace changes in `inject_guards.py`
don't invalidate the corpus.

## Layout

| Path                                      | Expect                           |
|-------------------------------------------|----------------------------------|
| `while_basic.cpp`                         | guards injected, compiles        |
| `while_infinite_without_guard.cpp`        | guards injected; runs < 1 s      |
| `for_bounded.cpp`                         | guards injected (defensive no-op)|
| `for_infinite_without_guard.cpp`          | guards injected; runs < 1 s      |
| `do_while_basic.cpp`                      | guards injected; do-while tail `while` not double-guarded |
| `range_for.cpp`                           | guards injected                  |
| `nested_while.cpp`                        | two guards, distinct names       |
| `continue_break_return.cpp`               | guards injected; control flow still works |
| `loops_in_lambda.cpp`                     | guards injected                  |
| `already_injected.cpp`                    | idempotent: re-inject is a no-op |

## `adversarial/`

Adversarial fixtures that must either be handled correctly OR produce a
non-zero exit — **silent miss is a test failure**.

| Path                              | Expected outcome                          |
|-----------------------------------|-------------------------------------------|
| `goto_based_loop.cpp`             | injector rejects with exit 2              |
| `comment_with_while.cpp`          | "while" inside comment must NOT be guarded|
| `string_with_while.cpp`           | "while" inside string literal must NOT be guarded|
| `macro_loop.cpp`                  | with `--fail-on-macro-loops`: exit 4      |
| `single_statement_body.cpp`       | injector rejects (exit 4)                 |
