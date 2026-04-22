// The word while inside a comment must NOT be guarded.
// while (true) { }
/* while (true) { this is also a comment } */
int main() {
    return 0;
}
