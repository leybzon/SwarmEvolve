// SPDX-License-Identifier: MIT
//
// TDR (Timeout Detection and Recovery) stress AI — M11 step 8.
//
// Purpose: verify that a **guard-bounded but intentionally inefficient**
// drone AI does not trigger a driver reset on the GPU. A true TDR would
// manifest as the CUDA runtime killing the kernel after ~2 s on the
// NVIDIA GB10 platform; a correctly guarded AI stays under that limit
// even when it does a lot of work.
//
// Strategy:
//   For each of the `kWorkIters` iterations (bounded by a hard literal
//   constant, NOT a runtime bound that an optimizer might remove), do
//   a trigonometric accumulate using the current allies array. The AI
//   then outputs a tiny velocity in a deterministic direction.
//
// kWorkIters is chosen so that the total per-tick cost is ≈ 500 µs on
// the GB10, well under the ~2 s TDR window but enough to noticeably
// dominate the kernel's hot-path time (making it observable in the nsys
// stats CSV).
//
// This file is a TEST FIXTURE, not a production baseline, hence it
// lives under tests/fixtures/. It is intentionally NOT placed in
// src/baselines/ (which requires full documentation discipline).

#include "ai_abi.h"
#include "types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

#pragma acc routine seq
void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action) {
    (void)incoming_messages;
    (void)enemies;

    // Hard compile-time bound — NOT read from a parameter, so an
    // optimizing compiler cannot hoist or eliminate it based on a
    // runtime condition. 5000 iterations × ~20 fmul/fadd/trig per iter
    // ≈ 100 k flops per drone per tick. At 50 drones × 1000 ticks that
    // is 5 G flops, which is a comfortable stress on the kernel without
    // approaching any plausible TDR window.
    constexpr int kWorkIters = 5000;

    float acc = 0.0f;
    const float x = allies[my_id].pos.x;
    const float y = allies[my_id].pos.y;

    // The body uses only cheap transcendentals that `#pragma acc routine
    // seq` is allowed to pull in (cmath's sinf/cosf). The loop is hard-
    // bounded; no early-exit, no while(), so it never trips any of the
    // orchestrator's guard-injection rules.
    for (int k = 0; k < kWorkIters; ++k) {
        const float t = static_cast<float>(k) * 1.0e-3f;
        acc += std::sin(x * t) * std::cos(y * t);
    }

    // Use `acc` so the whole loop isn't dead-code-eliminated.
    // Direction is deterministic per drone id; magnitude is tiny so the
    // drones stay roughly in place (we are measuring kernel work, not
    // combat outcomes).
    const float sign = (my_id & 1) ? -1.0f : 1.0f;
    out_action->velocity.x = sign * (0.001f + (acc - acc)); // `acc - acc == 0` keeps acc live
    out_action->velocity.y = 0.0f;
    out_action->target_id = -1;
    out_action->message_out[0] = acc;
    out_action->message_out[1] = 0.0f;
    out_action->message_out[2] = 0.0f;
    out_action->message_out[3] = 0.0f;

    (void)params;
    (void)my_memory;
}

} // namespace TEAM_NS_PLACEHOLDER
