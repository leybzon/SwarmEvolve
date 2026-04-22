// SPDX-License-Identifier: MIT
//
// "Drift" test fixture — moves every drone in the +x direction at half
// speed each tick, never attacks. Paired with the three frozen
// baselines (pursuit, cluster, stationary) this gives tests/test_tournament.py
// a distinct fourth participant so the M12 exit-criterion test ("4-AI
// tournament rankings stable across 3 reruns") has the population it
// needs.
//
// Lives under tests/fixtures/, not src/baselines/, because it is NOT a
// versioned baseline — it exists solely to exercise tournament
// scheduling and rating code.

#include "../ai_abi.h"
#include "../types.h"

namespace TEAM_NS_PLACEHOLDER {

#pragma acc routine seq
void drone_ai(int          my_id,
              const GameParams* params,
              const AllyState*  allies,
              const EnemyState* enemies,
              const float       incoming_messages[][MSG_SIZE],
              float*            my_memory,
              Action*           out_action) {
    (void)my_id;
    (void)allies;
    (void)enemies;
    (void)incoming_messages;
    (void)my_memory;

    out_action->velocity.x     = params->max_velocity * 0.5f;
    out_action->velocity.y     = 0.0f;
    out_action->target_id      = -1;
    out_action->message_out[0] = 0.0f;
    out_action->message_out[1] = 0.0f;
    out_action->message_out[2] = 0.0f;
    out_action->message_out[3] = 0.0f;
}

}  // namespace TEAM_NS_PLACEHOLDER
