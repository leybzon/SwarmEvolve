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
