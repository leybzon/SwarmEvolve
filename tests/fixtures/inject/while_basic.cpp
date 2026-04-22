// A basic bounded while loop. Guard injection must be a no-op semantically
// (loop already terminates) but the generated code must still compile.
int main() {
    int i = 0;
    int sum = 0;
    while (i < 5) {
        sum += i;
        ++i;
    }
    return (sum == 10) ? 0 : 1;
}
