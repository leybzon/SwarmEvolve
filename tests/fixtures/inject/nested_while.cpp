// Nested loops. Each must get its own guard variable (distinct names).
int main() {
    int outer = 0;
    int inner = 0;
    while (outer < 3) {
        int j = 0;
        while (j < 4) {
            ++inner;
            ++j;
        }
        ++outer;
    }
    return (inner == 12 && outer == 3) ? 0 : 1;
}
