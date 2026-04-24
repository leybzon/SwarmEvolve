You are writing drone swarm combat AI code for **SwarmEvolve**, an
evolutionary C++ testbed. Your output becomes the body of
`src/<team>/ai.cpp` in a single-file matchup against a fixed opponent.

# Objective

Produce **one** complete C++17 translation unit implementing
`drone_ai` inside `namespace {NAMESPACE} { ... }`. Your AI must
**beat the reference opponent** over many seeded matches while staying
within the strict safety rules below.

# Opponent

You are playing Team {TEAM_LETTER}. The opponent is **{OPPONENT_NAME}**;
its source is shown below for reference. Assume it does not change.

```cpp
{OPPONENT_SOURCE}
```

# ABI you must implement exactly

```cpp
// src/types.h excerpt (authoritative — do not redefine these types).
{TYPES_HEADER}
```

```cpp
// src/ai_abi.h (entry point signature — do not change it).
{ABI_HEADER}
```

Your file must begin with:

```cpp
#include "../ai_abi.h"
#include "../types.h"
```

…and wrap your code in `namespace {NAMESPACE} { ... }`. Mark the entry
point with `#pragma acc routine seq` immediately above the function
definition.

# Hard rules (violations fail the pre-commit linter and the loop-guard
  injector — your AI will be rejected without running)

1. **No heap.** No `new`, `delete`, `malloc`, `calloc`, `realloc`,
   `free`.
2. **No STL containers or strings.** No `std::vector`, `std::string`,
   `std::map`, `std::unordered_map`, `std::list`, `std::deque`.
3. **No threading.** No `std::thread`, `std::mutex`, `std::atomic`,
   `<thread>`, `<mutex>`, `<atomic>`.
4. **No I/O or filesystem.** No `<iostream>`, `<fstream>`,
   `<filesystem>`, `fopen`, `popen`, `system`, `execve`.
5. **No inline assembly.** No `asm`, `__asm__`.
6. **All loops must have a static upper bound.** The guard injector
   inserts a break at 1000 iterations per loop — that is the only
   safety net, not a budget. Prefer `for (int i = 0; i < MAX_DRONES;
   ++i)` or similar compile-time bounds.
7. **No `goto` loops.** The injector rejects them.
8. **Single statement bodies are rejected.** Always brace your loops
   (`while (cond) { ... }`).
9. **Pure function of inputs.** No static state, no clocks, no
   `rand()`. Persistent state goes in `my_memory[MEM_SIZE]`.

# What the engine gives you each tick

* `my_id` — your drone's index in the `allies` array.
* `params` — arena/combat constants and `current_tick`.
* `allies` — full state (including cooldowns) for every teammate, size
  `params->num_drones_a` or `num_drones_b` depending on your team.
* `enemies` — limited visibility (position + alive only, **no**
  cooldowns).
* `incoming_messages[ally_index][MSG_SIZE]` — what each teammate
  broadcast last tick.
* `my_memory[MEM_SIZE]` — 16 floats of persistent scratch; zeroed at
  tick 0.

# What you must write into `out_action`

* `out_action->velocity` — `(dx, dy)` desired velocity. The engine
  clamps magnitude to `params->max_velocity`.
* `out_action->target_id` — enemy index to attack (or `-1` for no
  attack). Attack only succeeds when in `disable_range` and
  `allies[my_id].cooldown == 0`.
* `out_action->message_out[MSG_SIZE]` — broadcast payload for
  teammates next tick.

# Coordinate system

Origin top-left, +X right, **+Y down** (screen space). Arena bounds
are `[0, arena_width] x [0, arena_height]`.

# Style

* C++17. Compile cleanly under
  `-Wall -Wextra -Wshadow -Wpedantic -Werror`. The sandbox suppresses
  `-Wunused-*` (so leftover scratch variables do not hard-fail the
  build), but **do not rely on that** — if you declare a local, read
  it. Dead bookkeeping is a sign of an unfinished refactor, not a
  design.
* Shadowing, uninitialized reads, signed/unsigned compares, and
  pedantic violations **remain hard errors**. Write code that would
  pass a careful code review, not just the compiler.
* Keep it under ~250 lines. Comments welcome; cleverness optional.

# After-Action Report (last generation)

{AAR}

# Lessons from prior generations

{PRIOR_LESSONS}

# Response format

Return **one** fenced ```cpp``` block containing the entire file. No
prose outside the block. Do not include a `main` function. Do not
redefine types from `types.h`.
