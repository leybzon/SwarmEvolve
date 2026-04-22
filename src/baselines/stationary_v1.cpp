// SPDX-License-Identifier: MIT
//
// "Stationary" baseline — does nothing. Used exclusively by
// tests/test_baselines.py to verify that a non-trivial AI (e.g. pursuit)
// can defeat a do-nothing opponent ≥ 95% of matches.
//
// This file is a FROZEN COPY. See src/baselines/README.md for the
// policy on updating baselines.
//
// NOTE: The namespace is deliberately left as a TEMPLATE (`TEAM_NS_PLACEHOLDER`).
// The test renders this file into either `namespace TeamA` or
// `namespace TeamB` before compiling, so one source serves both sides.

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

}  // namespace TEAM_NS_PLACEHOLDER
