// Loop body with continue, break, and early return. The guard's `break`
// must coexist with user-level `break` / `continue` / `return`.
int f(int n) {
    int sum = 0;
    for (int i = 0; i < n; ++i) {
        if (i == 3)
            continue;
        if (i == 7)
            return sum;
        if (i >= 10)
            break;
        sum += i;
    }
    return sum;
}

int main() {
    // i in {0,1,2,4,5,6} → sum = 18 at i=7 → early return
    return (f(20) == 18) ? 0 : 1;
}
