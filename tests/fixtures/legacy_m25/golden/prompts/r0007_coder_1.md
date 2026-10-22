You are a **C++ implementation specialist** for SwarmEvolve drone swarm combat.

Your role is to implement a tactical specification provided by a strategic planner. You will **NOT** see the After-Action Report (AAR) or battle history — only the tactic spec.

# Your Task

Implement the tactical specification below as **one complete C++17 source file** for Team B.

# Tactical Specification (from Planner)

```json
{
  "observe": {
    "key_metrics": [
      "Outcome: LOSS (1 win, 7 losses, 2 draws / 10 matches)",
      "Cooldown utilization: ours=0.40, theirs=0.70",
      "Focus-fire redundancy: 0.50",
      "Mean pairwise distance: ours=30.0, theirs=80.0",
      "Message bus entropy: 0.0"
    ]
  },
  "orient": {
    "why_we_failed": "Our drones chase the nearest enemy one at a time and arrive piecemeal, so the opponent trades evenly and then wins on numbers.",
    "what_enemy_exploited": "The opponent groups before engaging, so our first arrivals meet several shooters at once.",
    "constraints_violated": "We ignore message_out and my_memory entirely."
  },
  "decide": {
    "tactic_name": "New approach: Claim List",
    "mechanism": "Each drone picks the nearest alive enemy, closes at full speed, fires when the target is inside disable_range and its cooldown is zero, and backs away while cooling down if the target is closer than 1.5 times disable_range. Claims are kept in a list of enemy ids.",
    "why_this_counters_failure": "Focus-fire redundancy 0.50: claims stop two drones chasing one enemy."
  },
  "act": {
    "expected_changes": [
      {
        "metric": "focus_fire_redundancy",
        "old_value": 0.4,
        "target_value": 0.6,
        "reason": "fewer wasted engagements"
      },
      {
        "metric": "cooldown_utilization_us",
        "old_value": 0.5,
        "target_value": 0.3,
        "reason": "less duplicated targeting"
      }
    ]
  },
  "implementation_guidance": {
    "message_protocol": "message_out[0] = claimed enemy id, others 0",
    "memory_layout": "my_memory[0] = last target id",
    "special_cases": "no alive enemy: hold position"
  }
}
```

# Opponent (Reference Only)

You are fighting **champion_a_r7**:

```cpp
// SPDX-License-Identifier: MIT
//
// "Cluster + Focus-Fire" baseline (M3). FROZEN COPY.
//
// Uses placeholder namespace `TEAM_NS_PLACEHOLDER`; see
// src/baselines/pursuit_v1.cpp for the rationale.
//
// Strategy:
//   1. Compute the centroid of all alive allies. This biases the swarm to
//      stay cohesive (cluster).
//   2. Pick the enemy nearest to that centroid. Every teammate targets the
//      same enemy on the same tick (focus fire, deterministic because all
//      drones see an identical state snapshot per SPECIFICATION §3.2).
//   3. Move toward the centroid if far from it, else toward the focused
//      target. This prevents the swarm from fragmenting while still
//      closing on the enemy.
//   4. Attack if the focused target is within disable_range and own
//      cooldown is zero.
//   5. Broadcast the centroid and target id so the protocol is visible to
//      evolved descendants that may consume messages.
//
// Determinism notes:
//   * All drones compute the same centroid and the same nearest-enemy to
//     centroid independently, so focus-fire requires no coordination.
//   * Ties in "enemy nearest to centroid" are broken by lowest id (the
//     first-wins behavior of the `<` comparison), keeping results
//     byte-exact across identical inputs.
//
// Invariants: same as team_a_ai.cpp (no heap, no STL, no non-determinism).

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

namespace {

constexpr float kFarAway = 1.0e18f;

// Distance below which a drone considers itself "at the centroid" and will
// stop clustering in favor of pursuing the focused target. Chosen to be a
// small multiple of max_velocity so a cohesive swarm doesn't oscillate.
constexpr float kClusterRadius = 40.0f;

} // namespace

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
    (void)my_memory;

    const Vector2D my_pos = allies[my_id].pos;
    const int my_cooldown = allies[my_id].cooldown;
    const int n_allies = params->num_drones_b;  // this team
    const int n_enemies = params->num_drones_a; // opponents

    // -- Step 1: centroid of alive allies ---------------------------------
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
    // `alive_count` is at least 1 because `my_id` is alive (engine only
    // calls drone_ai for alive drones — see engine.cpp query_phase), but
    // we still guard the division for safety inside a #pragma acc routine.
    const float centroid_x =
        (alive_count > 0) ? (sum_x / static_cast<float>(alive_count)) : my_pos.x;
    const float centroid_y =
        (alive_count > 0) ? (sum_y / static_cast<float>(alive_count)) : my_pos.y;

    // -- Step 2: enemy nearest the centroid -------------------------------
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

    // -- Step 3: choose a movement heading --------------------------------
    //
    // The heading is a weighted sum of two unit vectors:
    //   (a) "pursue" — toward the focused enemy (primary objective)
    //   (b) "cluster" — toward the ally centroid (cohesion)
    //
    // Cluster weight decays with distance to the centroid: when the drone
    // is already close, it prioritizes attack; when far, it rejoins. This
    // avoids the "keep returning to centroid" oscillation that a hard
    // kClusterRadius threshold produces.
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

        // Cluster weight: 1.0 far from centroid, 0.0 when within
        // kClusterRadius. Smooth linear ramp avoids stop-start behaviour.
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

    // -- Step 4: attack decision ------------------------------------------
    //
    // If the team-focused target is in our own range, attack it (focus-fire
    // bonus). Otherwise, opportunistically attack *any* enemy in our own
    // range (prefer the one with lowest id for determinism). This prevents
    // the wasted ticks that a strict "only attack the team focus" rule
    // causes when different drones are close to different enemies early
    // in the match.
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
                    break; // lowest-id in-range enemy — deterministic
                }
            }
        }
    }
    out_action->target_id = action_target;

    // -- Step 5: broadcast ------------------------------------------------
    out_action->message_out[0] = centroid_x;
    out_action->message_out[1] = centroid_y;
    out_action->message_out[2] = static_cast<float>(focus);
    out_action->message_out[3] = static_cast<float>(alive_count);
}

} // namespace TEAM_NS_PLACEHOLDER

```

The opponent is **fixed**. Your tactic must counter its behavior.

# ABI Requirements (Mandatory)

```cpp
// src/types.h excerpt (do NOT redefine these)
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

```cpp
// src/ai_abi.h (entry point signature — do NOT change)
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

Your file **must**:
1. Start with:
   ```cpp
   #include "../ai_abi.h"
   #include "../types.h"
   ```
2. Wrap all code in `namespace TeamB { ... }`
3. Mark the entry point:
   ```cpp
   #pragma acc routine seq
   void drone_ai(...) { ... }
   ```

# Hard Constraints (Violations = Compile Failure)

1. **No heap:** No `new`, `delete`, `malloc`, `free`
2. **No STL containers:** No `std::vector`, `std::string`, `std::map`, `std::list`, etc.
3. **No threading:** No `std::thread`, `std::mutex`, `std::atomic`
4. **No I/O:** No `<iostream>`, `<fstream>`, `fopen`, `system`
5. **No inline assembly:** No `asm`, `__asm__`
6. **All loops bounded:** Prefer `for (int i = 0; i < MAX_DRONES; ++i)`. The guard injector caps loops at 1000 iterations, but that's a safety net, not a budget.
7. **No `goto` loops:** Rejected by injector
8. **Brace all loops:** `while (cond) { ... }` not `while (cond) stmt;`
9. **Pure function:** No static state, no `rand()`, no clocks

# Available State (Per Tick)

```cpp
int my_id;                       // your drone's index
const GameParams* params;        // arena/combat constants + current_tick
const AllyState* allies;         // teammates (full state, including cooldowns)
const EnemyState* enemies;       // enemies (position + alive, NO cooldowns)
const float incoming_messages[][MSG_SIZE]; // MSG_SIZE=4 per ally
float* my_memory;                // MEM_SIZE=16 floats, persistent, zeroed at tick 0
```

# Output (What You Write)

```cpp
out_action->velocity = {dx, dy}; // desired velocity (engine clamps to max_velocity)
out_action->target_id = idx;     // enemy index to attack, or -1
out_action->message_out[0..3] = ...; // broadcast to allies next tick
```

# Coordinate System

- Origin: top-left (0, 0)
- +X right, **+Y down** (screen space)
- Arena: `[0, arena_width] × [0, arena_height]`

# Combat Mechanics

- **Attack succeeds** when:
  - Target within `disable_range` (e.g., 50 units)
  - Attacker's `cooldown == 0`
  - Target is `alive`
- **Successful attack:**
  - Target dies instantly
  - Attacker cooldown set to `max_cooldown` (e.g., 10 ticks)
- **Mutual destruction** allowed (both die if both attack in range)
- **Focus-fire penalty:** N attackers on same target → all N pay cooldown, target dies once
- **Information asymmetry:** Enemy cooldowns are **hidden**

# Implementation Guidance (from Planner)

The planner provided:

**Message Protocol:**
```
message_out[0] = claimed enemy id, others 0
```

**Memory Layout:**
```
my_memory[0] = last target id
```

**Special Cases:**
```
no alive enemy: hold position
```

# Style Requirements

- C++17, compile cleanly under `-Wall -Wextra -Wshadow -Wpedantic -Werror`
- Sandbox suppresses `-Wunused-*`, but **don't rely on it** — read every variable you declare
- Shadowing, uninitialized reads, signed/unsigned compares remain **hard errors**
- Keep under ~250 lines
- Comments welcome, cleverness optional

# Implementation Checklist

Before writing, verify the tactic spec addresses these:

1. **Movement:** How does each drone compute `velocity`?
   - Example: move toward nearest enemy, maintain formation, kite at range, etc.

2. **Targeting:** How is `target_id` selected?
   - Example: nearest, lowest health (can't see health, so lowest index?), coordinated via messages, etc.

3. **Communication:** What goes in `message_out[0..3]`?
   - Example: `message_out[0] = target_id`, `message_out[1..2] = my position`, `message_out[3] = cooldown estimate`
   - If unused: set to 0.0f

4. **Persistent Memory:** What goes in `my_memory[0..15]`?
   - Example: `my_memory[0..9] = enemy_cooldown_estimates`, `my_memory[10..11] = last_position`
   - If unused: leave zeroed

5. **Edge Cases:**
   - All enemies dead → how to move? (patrol? cluster?)
   - All allies dead → your drone alone
   - `target_id` out of bounds or dead → set to -1
   - Distance calculations → avoid division by zero

# Helper Pattern (You May Use)

```cpp
// Distance between two points
inline float dist(float x1, float y1, float x2, float y2) {
    float dx = x2 - x1;
    float dy = y2 - y1;
    return sqrtf(dx * dx + dy * dy);
}

// Normalize a vector to unit length
inline void normalize(float& vx, float& vy) {
    float mag = sqrtf(vx * vx + vy * vy);
    if (mag > 1e-6f) {
        vx /= mag;
        vy /= mag;
    }
}

// Clamp velocity magnitude
inline void clamp_velocity(float& vx, float& vy, float max_vel) {
    float mag = sqrtf(vx * vx + vy * vy);
    if (mag > max_vel) {
        vx *= max_vel / mag;
        vy *= max_vel / mag;
    }
}
```

Use `<cmath>` for `sqrtf`, `fabsf`, `atan2f`. No other includes allowed.

# Response Format

Return **exactly one** fenced ```cpp``` code block. No prose before or after. The block must contain:

1. Includes
2. Namespace declaration
3. Helper functions (if any)
4. The `drone_ai` implementation

Example structure:

```cpp
#include "../ai_abi.h"
#include "../types.h"
#include <cmath>

namespace TeamB {

// Helpers (if needed)
inline float dist(...) { ... }

// Entry point
#pragma acc routine seq
void drone_ai(
    int my_id,
    const GameParams* params,
    const AllyState* allies,
    const EnemyState* enemies,
    const float incoming_messages[][MSG_SIZE],
    float* my_memory,
    Action* out_action
) {
    // 1. Parse inputs
    const int num_allies = params->num_drones_b;
    const int num_enemies = params->num_drones_a;
    const float max_vel = params->max_velocity;
    const float disable_r = params->disable_range;

    // 2. Implement tactic.mechanism from spec
    // ...

    // 3. Set outputs
    out_action->velocity = {vx, vy};
    out_action->target_id = selected_target;
    out_action->message_out[0] = ...;
    out_action->message_out[1] = ...;
    out_action->message_out[2] = ...;
    out_action->message_out[3] = ...;
}

} // namespace TeamB
```

# Scoring

Your code will be judged on:
1. **Compiles cleanly** (no warnings under strict flags)
2. **Implements the tactic** (mechanism matches spec)
3. **Handles edge cases** (doesn't crash on empty enemy list, etc.)
4. **Stays within ABI** (no banned constructs)

The planner predicted specific metric changes. Your implementation's success will be measured by **how closely the actual AAR matches those predictions**.

---

# Important Reminders

- **You are NOT the strategist.** Don't second-guess the tactic. Implement it faithfully.
- **No AAR access.** You don't know why the planner chose this tactic. Trust the spec.
- **Simple > clever.** Straightforward C++ that compiles is better than an elegant idea that violates ABI.
- **Test mentally:** Walk through: empty enemy list, all enemies dead, my drone alone, boundary cases.

Now implement the tactic in C++. Return only the fenced ```cpp``` block.
