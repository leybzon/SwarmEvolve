// WITHOUT the guard this program would spin forever. The injector must
// ensure it terminates within MAX_ITERATIONS (1000).
int main() {
    int i = 0;
    while (true) {
        ++i;
        // no break, no condition change
    }
    return i;
}
