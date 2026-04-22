// Loop inside a lambda. Regex backend guards it just like any other
// loop — the enclosing construct is transparent to our scan.
int main() {
    auto counter = [](int n) {
        int s = 0;
        for (int i = 0; i < n; ++i) {
            s += i;
        }
        return s;
    };
    return (counter(5) == 10) ? 0 : 1;
}
