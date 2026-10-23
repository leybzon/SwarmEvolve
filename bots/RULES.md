# Bot rules

A bot is one C++17 source file that controls one team's drones. The harness
compiles it into the engine and calls `drone_ai` once per alive drone per
tick. `scripts/validate_bot.py` checks every rule it can; a bot that breaks
one is invalid.

1. **One file.** A single `.cpp` file. Start from `bots/template.cpp`.
2. **License.** The bot is released under the MIT license. The first line
   is `// SPDX-License-Identifier: MIT`.
3. **Includes.** Only `"../ai_abi.h"`, `"../types.h"`, `<cmath>`, `<cfloat>`,
   `<climits>`, `<cstdint>` and `<cstddef>`.
4. **Namespace.** All code is inside `namespace TEAM_NS_PLACEHOLDER { ... }`.
   The harness replaces the placeholder with `TeamA` or `TeamB`; the file
   never names `TeamA` or `TeamB` itself.
5. **Entry point.** Exactly one definition of `drone_ai` with the signature in
   `src/ai_abi.h`, immediately preceded by `#pragma acc routine seq`. Helper
   functions and constants may sit in the same namespace or in an anonymous
   namespace inside it.
6. **Either side.** The bot may play Team A or Team B and cannot tell which.
   Both teams always have the same number of drones, so
   `params->num_drones_a` is the size of each team. Team size, arena size and
   the other `GameParams` fields are read at run time; do not assume fixed
   values.
7. **Output.** Every field of `*out_action` is set on every call.
8. **Plain data only.** Plain structs, fixed-size arrays and scalars. No
   dynamic allocation: no `new`, `delete`, `malloc`, `calloc`, `realloc`,
   `free`, no standard-library containers, no `std::string`.
9. **State.** The only state that survives from one call to the next is
   `my_memory` (`MEM_SIZE` floats per drone) and the messages delivered next
   tick. No mutable global or `static` variables, no `thread_local`; `const`
   and `constexpr` values are fine.
10. **Deterministic.** No randomness (`rand`, `srand`, `<random>`), no clocks or
    time, no I/O, no environment, no threads or atomics, no system calls, no
    inline assembly, no `goto`.
11. **Loops.** Every loop body is in braces. Loop guards are injected before
    compiling: each loop is cut off after 1000 iterations per call.
12. **Limits.** Local arrays stay under about 1 KB; recursion, if any, stays
    under 10 levels.
13. **Compiles cleanly** with g++ 11 and
    `-std=c++17 -O2 -Wall -Wextra -Wshadow -Wpedantic -Werror -ffp-contract=off`
    (unknown pragmas and unused variables, parameters and functions are not
    errors).
