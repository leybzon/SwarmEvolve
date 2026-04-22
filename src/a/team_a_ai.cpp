// SPDX-License-Identifier: MIT
//
// Team A AI — M2 STUB IMPLEMENTATION.
//
// This is a no-op AI used to bring the engine up end-to-end. It zeros its
// velocity and declines to attack, so matches run to max_ticks and end in a
// draw. M3 replaces this file with a non-trivial baseline ("nearest-enemy
// pursuit").
//
// Intentional constraints (same as real AI code):
//   * No heap, no STL containers, no iostream.
//   * Wrapped in `namespace TeamA`.
//   * Marked `#pragma acc routine seq` so nvc++ can lower it to a GPU
//     device routine.

#include "../ai_abi.h"
#include "../types.h"

namespace TeamA {

#pragma acc routine seq
void drone_ai(int          my_id,
              const GameParams* params,
              const AllyState*  allies,
              const EnemyState* enemies,
              const float       incoming_messages[][MSG_SIZE],
              float*            my_memory,
              Action*           out_action) {
    // Suppress unused-parameter warnings (-Wunused-parameter is on under
    // -Wextra). A real AI will use every one of these.
    (void)my_id;
    (void)params;
    (void)allies;
    (void)enemies;
    (void)incoming_messages;
    (void)my_memory;

    out_action->velocity.x   = 0.0f;
    out_action->velocity.y   = 0.0f;
    out_action->target_id    = -1;
    out_action->message_out[0] = 0.0f;
    out_action->message_out[1] = 0.0f;
    out_action->message_out[2] = 0.0f;
    out_action->message_out[3] = 0.0f;
}

}  // namespace TeamA
