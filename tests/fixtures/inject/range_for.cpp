// Range-based for. Guarded just like the classic for/while because the
// `for` keyword is what the regex matches.
int main() {
    int arr[5] = {1, 2, 3, 4, 5};
    int sum = 0;
    for (int v : arr) {
        sum += v;
    }
    return (sum == 15) ? 0 : 1;
}
