// Macro-expanded loop. The regex backend does NOT see the `for` inside
// the macro body, so if the macro hides an unbounded loop the injector
// might silently miss it. With --fail-on-macro-loops the caller gets a
// structured error; without it we warn and continue (the sandbox
// timeout in M8 is the backstop).
#define FOREVER(BODY) { for (;;) { BODY } }

int main() {
    int i = 0;
    FOREVER(++i; if (i > 3) break;)
    return i;
}
