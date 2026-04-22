// "while" inside a string literal must NOT be guarded.
#include <cstring>
int main() {
    const char* msg = "while (true) { do_something(); }";
    return (std::strlen(msg) > 0) ? 0 : 1;
}
