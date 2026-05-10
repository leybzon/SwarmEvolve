// SPDX-License-Identifier: MIT
//
// Team A AI — "Nearest-Enemy Pursuit" baseline (M3).
//
// Strategy:
//   1. Find the nearest alive enemy by Euclidean distance.
//   2. Move directly toward it, capped at max_velocity.
//   3. If the enemy is within disable_range and own cooldown is zero,
//      set target_id to that enemy.
//   4. Broadcast own (x, y) plus the chosen target id and distance via
//      message_out so teammates can potentially coordinate (future
//      baselines / evolved AIs may consume this).
//
// Invariants enforced by code review + pre-commit linter:
//   * No heap, no STL containers, no iostream, no threading.
//   * All loops are bounded by MAX_DRONES (compile-time constant), so the
//     guard-injection step is a defensive no-op for this file.
//   * Pure function of its inputs: no statics, no clocks, no rand().
//
// Cross-reference: IMPLEMENTATION_PLAN.md §5, SPECIFICATION.md §2.3.

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TeamA {

namespace {

// Sentinel "no enemy found" distance. 1e18f is representable in float32
// without overflow and is well beyond the arena's maximum possible
// Euclidean distance (sqrt(2) * 1000 ~= 1414).
constexpr float kFarAway = 1.0e18f;

} // namespace

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    // Incoming messages and persistent memory are unused by this baseline
    // but must still be declared in the signature to match the ABI.
    (void)incoming_messages;
    (void)my_memory;

    const Vector2D my_pos = allies[my_id].pos;
    const int my_cooldown = allies[my_id].cooldown;
    const int n_enemies = params->num_drones_b; // Team A's opponents.

    // -- Step 1: find nearest alive enemy ----------------------------------
    int nearest = -1;
    float nearest_d2 = kFarAway; // squared distance (sqrt deferred)
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

    // -- Step 2 & 3: move and (maybe) attack -------------------------------
    if (nearest < 0) {
        // No enemies left alive — stop moving, hold fire.
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
            // Coincident positions: stop moving this tick to avoid NaN.
            out_action->velocity.x = 0.0f;
            out_action->velocity.y = 0.0f;
        }

        const bool in_range = dist <= params->disable_range;
        const bool off_cd = my_cooldown == 0;
        out_action->target_id = (in_range && off_cd) ? nearest : -1;
    }

    // -- Step 4: broadcast for teammates ----------------------------------
    // Protocol (informal, stable within this baseline):
    //   message_out[0] = my_pos.x
    //   message_out[1] = my_pos.y
    //   message_out[2] = target id (cast from int; -1.0f means "no target")
    //   message_out[3] = distance to nearest enemy (or a large sentinel)
    out_action->message_out[0] = my_pos.x;
    out_action->message_out[1] = my_pos.y;
    out_action->message_out[2] = static_cast<float>(nearest);
    out_action->message_out[3] = (nearest < 0) ? kFarAway : std::sqrt(nearest_d2);
}

} // namespace TeamA
