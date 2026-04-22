// do-while — the trailing `while (cond)` must NOT receive its own guard
// because the body's injected check already bounds iterations.
int main() {
    int i = 0;
    do {
        ++i;
    } while (i < 5);
    return (i == 5) ? 0 : 1;
}
