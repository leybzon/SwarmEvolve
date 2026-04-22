// Single-statement loop body (no braces). Regex backend refuses because
// it has no safe place to splice the guard check. Authors must brace
// their loops; the lint layer enforces this on AI-generated code.
int main() {
    int i = 0;
    while (i < 5)
        ++i;
    return i;
}
