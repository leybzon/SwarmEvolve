// SPDX-License-Identifier: MIT
//
// ABI golden test for src/types.h.
//
// This test is intentionally dependency-free (no GoogleTest) so that the
// Makefile can build and run it as part of `make test-cpp` without external
// toolchain setup. It verifies:
//   1. Every POD type is trivially copyable and standard layout
//      (also checked at compile time via static_asserts in types.h).
//   2. The byte size and field offsets match the checked-in golden file
//      tests/fixtures/abi_golden.txt. A mismatch indicates an unintended
//      layout change that would break the engine <-> AI ABI.
//
// If you intentionally change the ABI:
//   1. Update SPECIFICATION.md §1.
//   2. Regenerate the golden file by running this test with
//      SWARMEVOLVE_ABI_UPDATE=1 in the environment (writes the golden).
//   3. Commit both changes together with an ABI-break commit message.

#include "../src/types.h"

#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>

namespace {

struct AbiEntry {
    const char* name;
    std::size_t size;
    std::size_t offset;  // SIZE_MAX for struct totals
};

#define SIZE_ENTRY(T) AbiEntry{#T, sizeof(T), static_cast<std::size_t>(-1)}
#define OFF_ENTRY(T, field) \
    AbiEntry { #T "." #field, sizeof(((T*)nullptr)->field), offsetof(T, field) }

int collect_entries(AbiEntry* out, int cap) {
    int n = 0;
    auto push = [&](AbiEntry e) {
        if (n < cap) out[n++] = e;
    };

    push(SIZE_ENTRY(Vector2D));
    push(OFF_ENTRY(Vector2D, x));
    push(OFF_ENTRY(Vector2D, y));

    push(SIZE_ENTRY(GameParams));
    push(OFF_ENTRY(GameParams, arena_width));
    push(OFF_ENTRY(GameParams, arena_height));
    push(OFF_ENTRY(GameParams, max_velocity));
    push(OFF_ENTRY(GameParams, disable_range));
    push(OFF_ENTRY(GameParams, max_cooldown));
    push(OFF_ENTRY(GameParams, num_drones_a));
    push(OFF_ENTRY(GameParams, num_drones_b));
    push(OFF_ENTRY(GameParams, max_ticks));
    push(OFF_ENTRY(GameParams, current_tick));

    push(SIZE_ENTRY(AllyState));
    push(OFF_ENTRY(AllyState, id));
    push(OFF_ENTRY(AllyState, pos));
    push(OFF_ENTRY(AllyState, cooldown));
    push(OFF_ENTRY(AllyState, alive));

    push(SIZE_ENTRY(EnemyState));
    push(OFF_ENTRY(EnemyState, id));
    push(OFF_ENTRY(EnemyState, pos));
    push(OFF_ENTRY(EnemyState, alive));

    push(SIZE_ENTRY(Action));
    push(OFF_ENTRY(Action, velocity));
    push(OFF_ENTRY(Action, target_id));
    push(OFF_ENTRY(Action, message_out));

    return n;
}

std::string format_entries(const AbiEntry* e, int n) {
    std::ostringstream os;
    os << "# ABI golden for src/types.h\n";
    os << "# Format: NAME SIZE [OFFSET]\n";
    for (int i = 0; i < n; ++i) {
        os << e[i].name << " " << e[i].size;
        if (e[i].offset != static_cast<std::size_t>(-1)) {
            os << " " << e[i].offset;
        }
        os << "\n";
    }
    return os.str();
}

bool memcpy_roundtrip() {
    GameParams a{};
    a.arena_width   = 1000.0f;
    a.arena_height  = 1000.0f;
    a.max_velocity  = 5.0f;
    a.disable_range = 50.0f;
    a.max_cooldown  = 10;
    a.num_drones_a  = 10;
    a.num_drones_b  = 10;
    a.max_ticks     = 1000;
    a.current_tick  = 42;

    GameParams b{};
    std::memcpy(&b, &a, sizeof(GameParams));

    return b.arena_width == a.arena_width && b.current_tick == a.current_tick &&
           b.num_drones_a == a.num_drones_a;
}

const char* golden_path() {
    // Tests are invoked from the repo root via the Makefile.
    return "tests/fixtures/abi_golden.txt";
}

int update_mode(const std::string& actual) {
    std::ofstream out(golden_path(), std::ios::binary);
    if (!out) {
        std::fprintf(stderr, "cannot open %s for writing\n", golden_path());
        return 1;
    }
    out << actual;
    std::fprintf(stderr, "[ABI] golden written to %s\n", golden_path());
    return 0;
}

}  // namespace

int main() {
    if (!memcpy_roundtrip()) {
        std::fprintf(stderr, "GameParams memcpy round-trip failed\n");
        return 1;
    }

    AbiEntry entries[64];
    const int n = collect_entries(entries, 64);
    const std::string actual = format_entries(entries, n);

    if (const char* env = std::getenv("SWARMEVOLVE_ABI_UPDATE"); env && env[0] == '1') {
        return update_mode(actual);
    }

    std::ifstream in(golden_path());
    if (!in) {
        std::fprintf(stderr,
                     "golden not found at %s\n"
                     "run with SWARMEVOLVE_ABI_UPDATE=1 to create it\n",
                     golden_path());
        return 2;
    }
    std::ostringstream buf;
    buf << in.rdbuf();
    const std::string expected = buf.str();

    if (actual != expected) {
        std::fprintf(stderr,
                     "ABI mismatch. If this change is intentional, regenerate with\n"
                     "  SWARMEVOLVE_ABI_UPDATE=1 ./build/test_types\n"
                     "and commit tests/fixtures/abi_golden.txt with the types.h change.\n\n"
                     "--- expected ---\n%s\n--- actual ---\n%s\n",
                     expected.c_str(), actual.c_str());
        return 3;
    }

    std::printf("OK (%d entries)\n", n);
    return 0;
}
