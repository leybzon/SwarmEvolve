The baseline nearest-enemy pursuit policy is a reasonable starting
point. I will keep it unchanged for this generation while I study the
AAR and look for a better strategy.

```cpp
// SPDX-License-Identifier: MIT
//
// Mini-track smoke fixture (M20). FROZEN. The `reproduce.py` CI harness
// feeds this file to the mock LLM so two back-to-back track_a runs
// must produce byte-identical fingerprints. Do not edit unless you
// also re-bless the reproducibility digest.

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TeamA {

namespace {

constexpr float kFarAway = 1.0e18f;

}  // namespace

#pragma acc routine seq
void drone_ai(int          my_id,
              const GameParams* params,
              const AllyState*  allies,
              const EnemyState* enemies,
              const float       incoming_messages[][MSG_SIZE],
              float*            my_memory,
              Action*           out_action) {
    (void)incoming_messages;
    (void)my_memory;

    const Vector2D my_pos      = allies[my_id].pos;
    const int      my_cooldown = allies[my_id].cooldown;
    const int      n_enemies   = params->num_drones_b;

    int   nearest    = -1;
    float nearest_d2 = kFarAway;
    for (int i = 0; i < n_enemies; ++i) {
        if (!enemies[i].alive) continue;
        const float dx = enemies[i].pos.x - my_pos.x;
        const float dy = enemies[i].pos.y - my_pos.y;
        const float d2 = dx * dx + dy * dy;
        if (d2 < nearest_d2) {
            nearest_d2 = d2;
            nearest    = i;
        }
    }

    if (nearest < 0) {
        out_action->velocity.x = 0.0f;
        out_action->velocity.y = 0.0f;
        out_action->target_id  = -1;
    } else {
        const float dx   = enemies[nearest].pos.x - my_pos.x;
        const float dy   = enemies[nearest].pos.y - my_pos.y;
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
        const bool off_cd   = my_cooldown == 0;
        out_action->target_id = (in_range && off_cd) ? nearest : -1;
    }

    out_action->message_out[0] = my_pos.x;
    out_action->message_out[1] = my_pos.y;
    out_action->message_out[2] = static_cast<float>(nearest);
    out_action->message_out[3] = (nearest < 0) ? kFarAway : std::sqrt(nearest_d2);
}

}  // namespace TeamA
```
