#include "../ai_abi.h"
#include "../types.h"
#include <cmath>

namespace TeamB {

namespace {
inline float dist(float x1, float y1, float x2, float y2) {
    const float dx = x2 - x1;
    const float dy = y2 - y1;
    return sqrtf(dx * dx + dy * dy);
}
}  // namespace

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
    const AllyState& me = allies[my_id];
    int best = -1;
    float best_d = 1e30f;
    for (int i = 0; i < params->num_drones_a; ++i) {
        if (!enemies[i].alive) {
            continue;
        }
        const float d = dist(me.pos.x, me.pos.y, enemies[i].pos.x, enemies[i].pos.y);
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    out_action->velocity = {0.0f, 0.0f};
    out_action->target_id = -1;
    for (int k = 0; k < MSG_SIZE; ++k) {
        out_action->message_out[k] = 0.0f;
    }
    if (best < 0) {
        return;
    }
    float vx = enemies[best].pos.x - me.pos.x;
    float vy = enemies[best].pos.y - me.pos.y;
    if (me.cooldown > 0 && best_d < 1.3f * params->disable_range) {
        vx = -vx;
        vy = -vy;
    }
    const float mag = sqrtf(vx * vx + vy * vy);
    if (mag > 1e-6f) {
        vx *= params->max_velocity / mag;
        vy *= params->max_velocity / mag;
    }
    out_action->velocity = {vx, vy};
    if (best_d <= params->disable_range && me.cooldown == 0) {
        out_action->target_id = best;
    }
    out_action->message_out[0] = static_cast<float>(best);
    my_memory[0] = static_cast<float>(best);
}

}  // namespace TeamB