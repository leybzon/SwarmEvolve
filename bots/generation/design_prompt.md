# Write a SwarmEvolve bot

SwarmEvolve is a simulation in which two teams of drones fight in a square
arena. A bot is a C++ program that controls every drone of one team. Your
task is to write the strongest bot you can.

Your bot will play many matches against a range of opponents that you will
not see, sometimes as Team A and sometimes as Team B. It is judged only by
the results of those matches. Everything you need is below: the game rules,
the rules every bot must follow, the two headers the bot is compiled
against, a template, and two example programs.

Reply with the complete source of your bot as one C++ file in a single
```cpp code block. Anything outside the code block is ignored.

## Game rules

**Teams and arena.** Two teams with the same number of drones, N per team
(`params->num_drones_a`; usually 10, at most `MAX_DRONES`). The arena is
`[0, params->arena_width] x [0, params->arena_height]`, usually 1000 x 1000.
The origin is the top-left corner, x grows to the right and y grows
downwards.

**Start.** Every drone of both teams starts at an independent, uniformly
random position in the arena, drawn from the match's seed. Cooldowns,
memories and messages start at zero.

**Ticks.** A match runs in ticks. Each tick has four phases.

1. *Query.* `drone_ai` is called once for every alive drone of both teams.
   All calls in a tick see the same snapshot of the game state; nothing
   changes until every drone has answered. Each call fills in an `Action`.
2. *Movement.* Each alive drone's `velocity` is scaled down to length
   `params->max_velocity` (usually 5) if it is longer, added to its
   position, and the position is clamped to the arena. Velocity does not
   carry over between ticks. Dead drones do not move.
3. *Combat.* An alive drone attacks successfully when its `cooldown` is 0,
   its `target_id` is the index of an alive enemy, and that enemy is within
   `params->disable_range` (usually 50) of it after movement. A successful
   attack marks the target to die and sets the attacker's cooldown to
   `params->max_cooldown` (usually 10). All attacks of both teams are
   resolved before anyone dies, so two drones can destroy each other, and
   every drone that successfully attacks the same target pays the cooldown
   although the target dies only once. An attack with `target_id == -1`, an
   invalid index, a dead target, a target out of range or a nonzero
   cooldown does nothing and costs nothing.
4. *Cleanup.* Marked drones die. Every alive drone's cooldown above 0 drops
   by 1. Each alive drone's `message_out` is delivered to all its teammates
   on the next tick as `incoming_messages[sender_id]`; a dead drone's slot
   is all zeros, and so is every slot on the first tick.
   `params->current_tick` increases by 1.

**What a drone sees.** `allies` holds its own team, including itself at
`allies[my_id]`: id, position, cooldown and alive flag. `enemies` holds the
other team's id, position and alive flag; enemy cooldowns are not visible.
Both arrays have `MAX_DRONES` entries, of which the first N are used.
`my_memory` is `MEM_SIZE` floats belonging to this drone alone: zero at the
start, kept from tick to tick, and frozen when the drone dies.

**End of a match.** A match ends when a team has no drones left, or when
`params->max_ticks` ticks have been played. A team with drones left wins
against a team with none; if both teams lose their last drones in the same
tick, it is a draw. At the tick limit, the team with more drones alive wins,
and equal numbers are a draw.

**Scoring.** A win scores +1, a draw 0 and a loss -1. A bot that breaks the
bot rules, fails to compile, crashes, or makes a match run longer than 10
seconds loses that match.

**Determinism.** The engine is deterministic: the same seed and the same
two programs always give the same match.

## Bot rules (bots/RULES.md)

`bots/RULES.md`, verbatim.

```markdown
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
```

## src/types.h

`src/types.h`, verbatim.

```cpp
// SPDX-License-Identifier: MIT
//
// SwarmEvolve POD data structures.
//
// This header is the ONLY interface between the engine and AI modules. It
// must remain:
//   - pure C++17, no STL containers, no heap, no <iostream>
//   - compilable by clang++, g++, and nvc++
//   - Plain Old Data (trivially copyable, standard layout)
//
// See SPECIFICATION.md §1 for the authoritative field descriptions.

#ifndef SWARMEVOLVE_TYPES_H
#define SWARMEVOLVE_TYPES_H

#include <cstdint>
#include <type_traits>

namespace swarmevolve {

// ---------------------------------------------------------------------------
// Compile-time constants (SPECIFICATION.md §1.1)
// ---------------------------------------------------------------------------

/// Number of floats per inter-drone message payload.
inline constexpr int MSG_SIZE = 4;

/// Number of floats in per-drone persistent memory.
inline constexpr int MEM_SIZE = 16;

/// Maximum drones per team. Runtime team size (GameParams::num_drones_{a,b})
/// must satisfy `num_drones <= MAX_DRONES`.
///
/// Build-time override: pass ``-DMAX_DRONES_OVERRIDE=<N>`` to the compiler
/// to raise the cap (e.g. for the M13 GPU scaling study at N in
/// {1'000, 10'000, 100'000}). The World struct in src/engine.cpp sizes
/// its static scratch buffers from this constant, so large overrides are
/// best paired with a heap-allocated World (see main()).
#ifdef MAX_DRONES_OVERRIDE
inline constexpr int MAX_DRONES = MAX_DRONES_OVERRIDE;
#else
inline constexpr int MAX_DRONES = 50;
#endif

// ---------------------------------------------------------------------------
// Vector2D  (SPECIFICATION.md §1.2)
// Coordinate system: origin top-left, +X right, +Y DOWN (screen space).
// ---------------------------------------------------------------------------
struct Vector2D {
    float x;
    float y;
};

// ---------------------------------------------------------------------------
// GameParams  (SPECIFICATION.md §1.3)
// All fields are constant during a match except `current_tick`.
// Team A size and Team B size are stored separately to permit asymmetric
// ("handicap") matches; by default the orchestrator sets them equal.
// ---------------------------------------------------------------------------
struct GameParams {
    float arena_width;
    float arena_height;
    float max_velocity;
    float disable_range;
    int max_cooldown;
    int num_drones_a;
    int num_drones_b;
    int max_ticks;
    int current_tick;
};

// ---------------------------------------------------------------------------
// AllyState  (SPECIFICATION.md §1.4)
// Full visibility of teammate state including cooldown.
// ---------------------------------------------------------------------------
struct AllyState {
    int id;
    Vector2D pos;
    int cooldown;
    bool alive;
    // Explicit padding to a 4-byte multiple for predictable layout across
    // compilers. The static_assert below verifies size.
    std::uint8_t _pad[3];
};

// ---------------------------------------------------------------------------
// EnemyState  (SPECIFICATION.md §1.5)
// Limited visibility: no cooldown field. Information asymmetry is enforced
// at the type level — AI cannot access something that does not exist in the
// struct.
// ---------------------------------------------------------------------------
struct EnemyState {
    int id;
    Vector2D pos;
    bool alive;
    std::uint8_t _pad[3];
};

// ---------------------------------------------------------------------------
// Action  (SPECIFICATION.md §1.6)
// AI output per tick. target_id == -1 means "do not attack".
// ---------------------------------------------------------------------------
struct Action {
    Vector2D velocity;
    int target_id;
    float message_out[MSG_SIZE];
};

// ---------------------------------------------------------------------------
// Compile-time ABI guarantees.
// A change to any of these lines is a breaking ABI change and must be
// accompanied by an update to tests/fixtures/abi_golden.txt.
// ---------------------------------------------------------------------------

static_assert(std::is_trivially_copyable_v<Vector2D>);
static_assert(std::is_standard_layout_v<Vector2D>);
static_assert(sizeof(Vector2D) == 8, "Vector2D must be 8 bytes");

static_assert(std::is_trivially_copyable_v<GameParams>);
static_assert(std::is_standard_layout_v<GameParams>);

static_assert(std::is_trivially_copyable_v<AllyState>);
static_assert(std::is_standard_layout_v<AllyState>);
static_assert(sizeof(AllyState) <= 32, "AllyState should fit in half a cacheline");

static_assert(std::is_trivially_copyable_v<EnemyState>);
static_assert(std::is_standard_layout_v<EnemyState>);

static_assert(std::is_trivially_copyable_v<Action>);
static_assert(std::is_standard_layout_v<Action>);

static_assert(MSG_SIZE == 4, "Trace format depends on MSG_SIZE == 4");
static_assert(MEM_SIZE == 16, "Trace format depends on MEM_SIZE == 16");
static_assert(MAX_DRONES >= 1);

} // namespace swarmevolve

// ---------------------------------------------------------------------------
// Top-level using-declarations for AI convenience.
// AI source files include <src/types.h> and may write `Vector2D` unqualified.
// Engine source files use `swarmevolve::Vector2D` explicitly.
// ---------------------------------------------------------------------------
using Vector2D = swarmevolve::Vector2D;
using GameParams = swarmevolve::GameParams;
using AllyState = swarmevolve::AllyState;
using EnemyState = swarmevolve::EnemyState;
using Action = swarmevolve::Action;

using swarmevolve::MAX_DRONES;
using swarmevolve::MEM_SIZE;
using swarmevolve::MSG_SIZE;

#endif // SWARMEVOLVE_TYPES_H
```

## src/ai_abi.h

`src/ai_abi.h`, verbatim.

```cpp
// SPDX-License-Identifier: MIT
//
// Forward declarations of the AI entry points for both teams.
//
// The engine includes this header (NOT the AI .cpp files) so that the engine
// and AI are compiled in separate translation units. This:
//   1. prevents engine-internal helpers from leaking into AI code,
//   2. lets the orchestrator replace a team's .cpp without recompiling the
//      engine (future incremental-compilation milestone), and
//   3. makes `#pragma acc routine seq` live where it belongs — next to the
//      function definition in the AI translation unit.
//
// The signatures below MUST match `drone_ai` as defined in SPECIFICATION.md
// §2.1 exactly. A mismatch is a linker error.

#ifndef SWARMEVOLVE_AI_ABI_H
#define SWARMEVOLVE_AI_ABI_H

#include "types.h"

namespace TeamA {

void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action);

} // namespace TeamA

namespace TeamB {

void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action);

} // namespace TeamB

#endif // SWARMEVOLVE_AI_ABI_H
```

## Template (bots/template.cpp)

`bots/template.cpp`, verbatim.

```cpp
// SPDX-License-Identifier: MIT
//
// Bot template. It implements the ABI and does nothing: every drone stays
// where it is and never attacks. Replace the body of drone_ai.

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

namespace {

// Helpers and constants go here (const or constexpr only; no mutable globals).

} // namespace

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    // Both teams always have the same number of drones.
    const int n = params->num_drones_a;
    (void)n;
    (void)my_id;
    (void)allies;
    (void)enemies;
    (void)incoming_messages;
    (void)my_memory;

    // Every field of out_action must be set on every call.
    out_action->velocity.x = 0.0f;
    out_action->velocity.y = 0.0f;
    out_action->target_id = -1;
    for (int k = 0; k < MSG_SIZE; ++k) {
        out_action->message_out[k] = 0.0f;
    }
}

} // namespace TEAM_NS_PLACEHOLDER
```

## Example program: pursuit_v1

`src/baselines/pursuit_v1.cpp`, with its comments removed.

```cpp
// SPDX-License-Identifier: MIT

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

namespace {

constexpr float kFarAway = 1.0e18f;

}

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
    (void)my_memory;

    const Vector2D my_pos = allies[my_id].pos;
    const int my_cooldown = allies[my_id].cooldown;
    const int n_enemies = params->num_drones_b;

    int nearest = -1;
    float nearest_d2 = kFarAway;
    for (int i = 0; i < n_enemies; ++i) {
        if (!enemies[i].alive)
            continue;
        const float dx = enemies[i].pos.x - my_pos.x;
        const float dy = enemies[i].pos.y - my_pos.y;
        const float d2 = dx * dx + dy * dy;
        if (d2 < nearest_d2) {
            nearest_d2 = d2;
            nearest = i;
        }
    }

    if (nearest < 0) {
        out_action->velocity.x = 0.0f;
        out_action->velocity.y = 0.0f;
        out_action->target_id = -1;
    } else {
        const float dx = enemies[nearest].pos.x - my_pos.x;
        const float dy = enemies[nearest].pos.y - my_pos.y;
        const float dist = std::sqrt(nearest_d2);

        if (dist > 0.0f) {
            const float scale = params->max_velocity / dist;
            out_action->velocity.x = dx * scale;
            out_action->velocity.y = dy * scale;
        } else {
            out_action->velocity.x = 0.0f;
            out_action->velocity.y = 0.0f;
        }

        const bool in_range = dist <= params->disable_range;
        const bool off_cd = my_cooldown == 0;
        out_action->target_id = (in_range && off_cd) ? nearest : -1;
    }

    out_action->message_out[0] = my_pos.x;
    out_action->message_out[1] = my_pos.y;
    out_action->message_out[2] = static_cast<float>(nearest);
    out_action->message_out[3] = (nearest < 0) ? kFarAway : std::sqrt(nearest_d2);
}

}
```

## Example program: cluster_v1

`src/baselines/cluster_v1.cpp`, with its comments removed.

```cpp
// SPDX-License-Identifier: MIT

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

namespace {

constexpr float kFarAway = 1.0e18f;

constexpr float kClusterRadius = 40.0f;

}

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
    (void)my_memory;

    const Vector2D my_pos = allies[my_id].pos;
    const int my_cooldown = allies[my_id].cooldown;
    const int n_allies = params->num_drones_b;
    const int n_enemies = params->num_drones_a;

    float sum_x = 0.0f;
    float sum_y = 0.0f;
    int alive_count = 0;
    for (int i = 0; i < n_allies; ++i) {
        if (!allies[i].alive)
            continue;
        sum_x += allies[i].pos.x;
        sum_y += allies[i].pos.y;
        ++alive_count;
    }
    const float centroid_x =
        (alive_count > 0) ? (sum_x / static_cast<float>(alive_count)) : my_pos.x;
    const float centroid_y =
        (alive_count > 0) ? (sum_y / static_cast<float>(alive_count)) : my_pos.y;

    int focus = -1;
    float focus_d2 = kFarAway;
    for (int i = 0; i < n_enemies; ++i) {
        if (!enemies[i].alive)
            continue;
        const float ex = enemies[i].pos.x - centroid_x;
        const float ey = enemies[i].pos.y - centroid_y;
        const float d2 = ex * ex + ey * ey;
        if (d2 < focus_d2) {
            focus_d2 = d2;
            focus = i;
        }
    }

    float heading_x = 0.0f;
    float heading_y = 0.0f;
    {
        const float cdx = centroid_x - my_pos.x;
        const float cdy = centroid_y - my_pos.y;
        const float cdist = std::sqrt(cdx * cdx + cdy * cdy);

        float pursue_x = 0.0f, pursue_y = 0.0f;
        if (focus >= 0) {
            const float fdx = enemies[focus].pos.x - my_pos.x;
            const float fdy = enemies[focus].pos.y - my_pos.y;
            const float fd = std::sqrt(fdx * fdx + fdy * fdy);
            if (fd > 0.0f) {
                pursue_x = fdx / fd;
                pursue_y = fdy / fd;
            }
        }

        float cluster_x = 0.0f, cluster_y = 0.0f;
        if (cdist > 0.0f) {
            cluster_x = cdx / cdist;
            cluster_y = cdy / cdist;
        }

        float w_cluster = 0.0f;
        if (cdist > kClusterRadius) {
            w_cluster = (cdist - kClusterRadius) / cdist;
            if (w_cluster > 1.0f)
                w_cluster = 1.0f;
        }
        const float w_pursue = 1.0f - w_cluster;

        heading_x = w_pursue * pursue_x + w_cluster * cluster_x;
        heading_y = w_pursue * pursue_y + w_cluster * cluster_y;
    }

    const float hmag = std::sqrt(heading_x * heading_x + heading_y * heading_y);
    if (hmag > 0.0f) {
        const float scale = params->max_velocity / hmag;
        out_action->velocity.x = heading_x * scale;
        out_action->velocity.y = heading_y * scale;
    } else {
        out_action->velocity.x = 0.0f;
        out_action->velocity.y = 0.0f;
    }

    int action_target = -1;
    if (my_cooldown == 0) {
        const float range_sq = params->disable_range * params->disable_range;
        if (focus >= 0) {
            const float mdx = enemies[focus].pos.x - my_pos.x;
            const float mdy = enemies[focus].pos.y - my_pos.y;
            if (mdx * mdx + mdy * mdy <= range_sq) {
                action_target = focus;
            }
        }
        if (action_target < 0) {
            for (int i = 0; i < n_enemies; ++i) {
                if (!enemies[i].alive)
                    continue;
                const float dx = enemies[i].pos.x - my_pos.x;
                const float dy = enemies[i].pos.y - my_pos.y;
                if (dx * dx + dy * dy <= range_sq) {
                    action_target = i;
                    break;
                }
            }
        }
    }
    out_action->target_id = action_target;

    out_action->message_out[0] = centroid_x;
    out_action->message_out[1] = centroid_y;
    out_action->message_out[2] = static_cast<float>(focus);
    out_action->message_out[3] = static_cast<float>(alive_count);
}

}
```
