// SPDX-License-Identifier: MIT
//
// Team B AI — M2 STUB IMPLEMENTATION.
//
// Identical behavior to src/a/team_a_ai.cpp but wrapped in namespace TeamB.
// Replaced by a "cluster + focus-fire" baseline in M3.

#include "../ai_abi.h"
#include "../types.h"

namespace TeamB {

#pragma acc routine seq
void drone_ai(int          my_id,
              const GameParams* params,
              const AllyState*  allies,
              const EnemyState* enemies,
              const float       incoming_messages[][MSG_SIZE],
              float*            my_memory,
              Action*           out_action) {
    (void)my_id;
    (void)params;
    (void)allies;
    (void)enemies;
    (void)incoming_messages;
    (void)my_memory;

    out_action->velocity.x     = 0.0f;
    out_action->velocity.y     = 0.0f;
    out_action->target_id      = -1;
    out_action->message_out[0] = 0.0f;
    out_action->message_out[1] = 0.0f;
    out_action->message_out[2] = 0.0f;
    out_action->message_out[3] = 0.0f;
}

}  // namespace TeamB
