// SPDX-License-Identifier: MIT
//
// SwarmEvolve engine core — pure-function phase implementations.
//
// This header factors the four game-loop phases out of engine.cpp so that
// unit tests (tests/test_engine_phases.cpp) can exercise each phase with
// hand-built state without going through a full match.
//
// Rationale for the header-only approach:
//   * The engine is a single translation unit in production (see the
//     Makefile's SRC_ENGINE variable). Inlining the phase logic here keeps
//     the public surface small and avoids a separate .o file.
//   * Tests can #include "engine.h" directly without linking engine.cpp
//     (which defines main()).
//
// All functions are deterministic and allocation-free. They operate on
// caller-owned fixed-size arrays.
//
// Cross-reference: SPECIFICATION.md §3 (Game Loop).

#ifndef SWARMEVOLVE_ENGINE_H
#define SWARMEVOLVE_ENGINE_H

#include "types.h"

#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace swarmevolve::engine {

// ---------------------------------------------------------------------------
// Outcome codes (match SPECIFICATION §4.3 exit codes).
// ---------------------------------------------------------------------------
enum Outcome : int {
    OUTCOME_TEAM_A_WIN = 0,
    OUTCOME_TEAM_B_WIN = 1,
    OUTCOME_DRAW = 2,
};

inline const char* outcome_tag(Outcome o) {
    switch (o) {
    case OUTCOME_TEAM_A_WIN:
        return "TEAM_A_WIN";
    case OUTCOME_TEAM_B_WIN:
        return "TEAM_B_WIN";
    case OUTCOME_DRAW:
        return "DRAW";
    }
    return "UNKNOWN";
}

// ---------------------------------------------------------------------------
// Count alive drones.
// ---------------------------------------------------------------------------
inline int count_alive(const AllyState* drones, int n) {
    int c = 0;
    for (int i = 0; i < n; ++i) {
        if (drones[i].alive)
            ++c;
    }
    return c;
}

// ---------------------------------------------------------------------------
// Phase 2: Movement.
//   * Velocity is clamped to `max_velocity` (Euclidean).
//   * Position updated in place.
//   * Position clamped to [0, arena_width] x [0, arena_height].
//   * Dead drones do not move.
//
// GPU: each iteration is per-drone independent (writes only to drones[i].pos,
// reads actions[i] and params). Safe for `#pragma acc parallel loop`.
// ---------------------------------------------------------------------------
inline void movement_phase(AllyState* drones, const Action* actions, int n,
                           const GameParams& params) {
// Explicit bounds are required because nvc++ cannot infer the size of
// the raw pointer parameters. Without them, the runtime falls back to
// 1-byte transfers which silently corrupt the output. See the
// companion note in combat_phase_one_side.
#pragma acc parallel loop copy(drones[0 : n]) copyin(actions[0 : n])
    for (int i = 0; i < n; ++i) {
        if (!drones[i].alive)
            continue;

        float vx = actions[i].velocity.x;
        float vy = actions[i].velocity.y;

        const float speed = std::sqrt(vx * vx + vy * vy);
        if (speed > params.max_velocity) {
            const float scale = params.max_velocity / speed;
            vx *= scale;
            vy *= scale;
        }

        drones[i].pos.x += vx;
        drones[i].pos.y += vy;

        if (drones[i].pos.x < 0.0f)
            drones[i].pos.x = 0.0f;
        if (drones[i].pos.x > params.arena_width)
            drones[i].pos.x = params.arena_width;
        if (drones[i].pos.y < 0.0f)
            drones[i].pos.y = 0.0f;
        if (drones[i].pos.y > params.arena_height)
            drones[i].pos.y = params.arena_height;
    }
}

// ---------------------------------------------------------------------------
// Phase 3: Combat resolution (one attacking team).
//
// Records attacker cooldowns in `attacker_cooldowns_out` (one per attacker)
// and pending target deaths in `pending_deaths_target` (one per target team
// member). Both output arrays must be pre-sized to n_att and n_def
// respectively. Caller zeros them before the call.
//
// "Successful attack" criteria (SPECIFICATION §3.4):
//   1. attacker alive AND attacker.cooldown == 0
//   2. attacker.action.target_id in [0, n_def)
//   3. target alive at resolution time (i.e., before deaths are applied)
//   4. Euclidean distance <= params.disable_range
//
// Each successful attacker pays `max_cooldown`. Deaths are *not* applied
// here; they accumulate in pending_deaths_target so that multiple
// attackers within the same combat phase all count as successful
// (focus-fire rule, SPECIFICATION §3.4 rule 7).
//
// GPU: the outer loop is parallelised. Writes are:
//   * `attacker_cooldowns_out[i]` — per-attacker (no aliasing across threads).
//   * `pending_deaths_target[target_id]` — many attackers may write `true` to
//     the same slot. The write is idempotent (`true` always), so a plain
//     parallel loop is race-safe without atomics. See ARCHITECTURE.md
//     §"Combat parallelism note".
// ---------------------------------------------------------------------------
inline void combat_phase_one_side(const AllyState* attackers, const Action* attacker_actions,
                                  int n_att, const AllyState* defenders, int n_def,
                                  const GameParams& params, int* attacker_cooldowns_out,
                                  bool* pending_deaths_target) {
// NOTE: We read `defenders[t].alive` rather than consulting the death
// buffer. Deaths recorded in this same loop iteration do NOT affect
// whether later attackers' shots on the same target succeed — that is
// the focus-fire semantics required by the spec.
//
// Explicit data clauses are required because nvc++ cannot infer bounds
// from raw `bool*` / `int*` parameters (it defaults to 1 byte, which
// silently corrupts the transfer back to host). `present_or_copy(...)`
// stays safe under managed memory (the clause becomes a no-op when the
// pointer is managed) and correct under explicit copy.
// NOTE on `copy` vs `copyout` for `attacker_cooldowns_out`: we need
// `copy` (not `copyout`) because the kernel only writes slots belonging
// to *successful* attackers. Slots for misses, cooldown-holders, and
// dead attackers are left untouched on the device. With `copyout` those
// slots would be uninitialized device memory (random garbage) when
// transferred back. With `copy`, the host's zeroed buffer is uploaded
// first, so untouched slots round-trip as zero. Caller contract: zero
// `attacker_cooldowns_out` before the call.
#pragma acc parallel loop copyin(attackers[0 : n_att], attacker_actions[0 : n_att],                \
                                 defenders[0 : n_def])                                             \
    copy(attacker_cooldowns_out[0 : n_att], pending_deaths_target[0 : n_def])
    for (int i = 0; i < n_att; ++i) {
        if (!attackers[i].alive)
            continue;
        if (attackers[i].cooldown > 0)
            continue;

        const int target_id = attacker_actions[i].target_id;
        if (target_id < 0 || target_id >= n_def)
            continue;
        if (!defenders[target_id].alive)
            continue;

        const float dx = attackers[i].pos.x - defenders[target_id].pos.x;
        const float dy = attackers[i].pos.y - defenders[target_id].pos.y;
        const float dist_sq = dx * dx + dy * dy;
        const float range_sq = params.disable_range * params.disable_range;

        // Using squared distance avoids an sqrt but is algebraically
        // equivalent for the <= comparison in IEEE-754 (both sides are
        // finite non-negative floats).
        if (dist_sq <= range_sq) {
            pending_deaths_target[target_id] = true;
            attacker_cooldowns_out[i] = params.max_cooldown;
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 4 helper: apply a freshly-computed cooldown array.
// Successful attackers' cooldowns (from combat_phase_one_side) are assigned
// BEFORE deaths are applied, matching the spec's "cooldown at resolution
// time" rule for mutual-destruction cases.
// ---------------------------------------------------------------------------
inline void apply_cooldowns(AllyState* drones, const int* new_cooldowns, int n) {
#pragma acc parallel loop copyin(new_cooldowns[0 : n]) copy(drones[0 : n])
    for (int i = 0; i < n; ++i) {
        if (new_cooldowns[i] > 0) {
            drones[i].cooldown = new_cooldowns[i];
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 4 helper: apply pending deaths.
// ---------------------------------------------------------------------------
inline void apply_deaths(AllyState* drones, const bool* pending_deaths, int n) {
#pragma acc parallel loop copyin(pending_deaths[0 : n]) copy(drones[0 : n])
    for (int i = 0; i < n; ++i) {
        if (pending_deaths[i])
            drones[i].alive = false;
    }
}

// ---------------------------------------------------------------------------
// Phase 4 helper: decrement cooldowns on alive drones.
// Applied AFTER apply_deaths — a drone that just died keeps its cooldown
// snapshot (useful for traces / stats) but dead drones never decrement.
// ---------------------------------------------------------------------------
inline void decrement_cooldowns(AllyState* drones, int n) {
#pragma acc parallel loop copy(drones[0 : n])
    for (int i = 0; i < n; ++i) {
        if (drones[i].alive && drones[i].cooldown > 0) {
            drones[i].cooldown -= 1;
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 4 helper: route messages for the next tick.
// Alive drones publish their `message_out`; dead drones' slots are zeroed
// (SPECIFICATION §3.5 / §7.4).
// ---------------------------------------------------------------------------
inline void route_messages(const AllyState* drones, const Action* actions,
                           float out_messages[][MSG_SIZE], int n) {
#pragma acc parallel loop copyin(drones[0 : n], actions[0 : n])                                    \
    copyout(out_messages[0 : n][0 : MSG_SIZE])
    for (int i = 0; i < n; ++i) {
        if (drones[i].alive) {
            for (int j = 0; j < MSG_SIZE; ++j) {
                out_messages[i][j] = actions[i].message_out[j];
            }
        } else {
            for (int j = 0; j < MSG_SIZE; ++j) {
                out_messages[i][j] = 0.0f;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Termination check (SPECIFICATION §3.6).
//   Returns true and sets *out_outcome when the match has ended.
//   Returns false when the loop should continue.
// The `current_tick >= max_ticks` rule is evaluated by the caller so that
// the last recorded trace line can include the outcome tag.
// ---------------------------------------------------------------------------
inline bool check_early_termination(const AllyState* a, int na, const AllyState* b, int nb,
                                    Outcome* out_outcome) {
    const int a_alive = count_alive(a, na);
    const int b_alive = count_alive(b, nb);

    if (a_alive == 0 && b_alive == 0) {
        *out_outcome = OUTCOME_DRAW;
        return true;
    }
    if (a_alive == 0) {
        *out_outcome = OUTCOME_TEAM_B_WIN;
        return true;
    }
    if (b_alive == 0) {
        *out_outcome = OUTCOME_TEAM_A_WIN;
        return true;
    }
    return false;
}

inline Outcome final_outcome(const AllyState* a, int na, const AllyState* b, int nb) {
    const int a_alive = count_alive(a, na);
    const int b_alive = count_alive(b, nb);
    if (a_alive > 0 && b_alive == 0)
        return OUTCOME_TEAM_A_WIN;
    if (b_alive > 0 && a_alive == 0)
        return OUTCOME_TEAM_B_WIN;
    if (a_alive == 0 && b_alive == 0)
        return OUTCOME_DRAW;
    if (a_alive > b_alive)
        return OUTCOME_TEAM_A_WIN;
    if (b_alive > a_alive)
        return OUTCOME_TEAM_B_WIN;
    return OUTCOME_DRAW;
}

} // namespace swarmevolve::engine

#endif // SWARMEVOLVE_ENGINE_H
