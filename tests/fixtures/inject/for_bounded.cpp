// A bounded for loop. Guard is defensive no-op but must compile cleanly.
int main() {
    int sum = 0;
    for (int i = 0; i < 10; ++i) {
        sum += i;
    }
    return (sum == 45) ? 0 : 1;
}
