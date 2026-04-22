// goto-based loops MUST be rejected — they escape our keyword-based scan.
int main() {
    int i = 0;
loop:
    ++i;
    if (i < 5) goto loop;
    return i;
}
