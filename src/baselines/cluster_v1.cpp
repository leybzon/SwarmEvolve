// SPDX-License-Identifier: MIT
//
// "Cluster + Focus-Fire" baseline (M3). FROZEN COPY.
//
// Uses placeholder namespace `TEAM_NS_PLACEHOLDER`; see
// src/baselines/pursuit_v1.cpp for the rationale.
//
// Strategy:
//   1. Compute the centroid of all alive allies. This biases the swarm to
//      stay cohesive (cluster).
//   2. Pick the enemy nearest to that centroid. Every teammate targets the
//      same enemy on the same tick (focus fire, deterministic because all
//      drones see an identical state snapshot per SPECIFICATION §3.2).
//   3. Move toward the centroid if far from it, else toward the focused
//      target. This prevents the swarm from fragmenting while still
//      closing on the enemy.
//   4. Attack if the focused target is within disable_range and own
//      cooldown is zero.
//   5. Broadcast the centroid and target id so the protocol is visible to
//      evolved descendants that may consume messages.
//
// Determinism notes:
//   * All drones compute the same centroid and the same nearest-enemy to
//     centroid independently, so focus-fire requires no coordination.
//   * Ties in "enemy nearest to centroid" are broken by lowest id (the
//     first-wins behavior of the `<` comparison), keeping results
//     byte-exact across identical inputs.
//
// Invariants: same as team_a_ai.cpp (no heap, no STL, no non-determinism).

#include "../ai_abi.h"
#include "../types.h"

#include <cmath>

namespace TEAM_NS_PLACEHOLDER {

namespace {

constexpr float kFarAway = 1.0e18f;

// Distance below which a drone considers itself "at the centroid" and will
// stop clustering in favor of pursuing the focused target. Chosen to be a
// small multiple of max_velocity so a cohesive swarm doesn't oscillate.
constexpr float kClusterRadius = 40.0f;

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
    const int      n_allies    = params->num_drones_b;  // this team
    const int      n_enemies   = params->num_drones_a;  // opponents

    // -- Step 1: centroid of alive allies ---------------------------------
    float sum_x = 0.0f;
    float sum_y = 0.0f;
    int   alive_count = 0;
    for (int i = 0; i < n_allies; ++i) {
        if (!allies[i].alive) continue;
        sum_x += allies[i].pos.x;
        sum_y += allies[i].pos.y;
        ++alive_count;
    }
    // `alive_count` is at least 1 because `my_id` is alive (engine only
    // calls drone_ai for alive drones — see engine.cpp query_phase), but
    // we still guard the division for safety inside a #pragma acc routine.
    const float centroid_x = (alive_count > 0) ? (sum_x / static_cast<float>(alive_count)) : my_pos.x;
    const float centroid_y = (alive_count > 0) ? (sum_y / static_cast<float>(alive_count)) : my_pos.y;

    // -- Step 2: enemy nearest the centroid -------------------------------
    int   focus    = -1;
    float focus_d2 = kFarAway;
    for (int i = 0; i < n_enemies; ++i) {
        if (!enemies[i].alive) continue;
        const float ex = enemies[i].pos.x - centroid_x;
        const float ey = enemies[i].pos.y - centroid_y;
        const float d2 = ex * ex + ey * ey;
        if (d2 < focus_d2) {
            focus_d2 = d2;
            focus    = i;
        }
    }

    // -- Step 3: choose a movement heading --------------------------------
    //
    // The heading is a weighted sum of two unit vectors:
    //   (a) "pursue" — toward the focused enemy (primary objective)
    //   (b) "cluster" — toward the ally centroid (cohesion)
    //
    // Cluster weight decays with distance to the centroid: when the drone
    // is already close, it prioritizes attack; when far, it rejoins. This
    // avoids the "keep returning to centroid" oscillation that a hard
    // kClusterRadius threshold produces.
    float heading_x = 0.0f;
    float heading_y = 0.0f;
    {
        const float cdx  = centroid_x - my_pos.x;
        const float cdy  = centroid_y - my_pos.y;
        const float cdist = std::sqrt(cdx * cdx + cdy * cdy);

        float pursue_x = 0.0f, pursue_y = 0.0f;
        if (focus >= 0) {
            const float fdx = enemies[focus].pos.x - my_pos.x;
            const float fdy = enemies[focus].pos.y - my_pos.y;
            const float fd  = std::sqrt(fdx * fdx + fdy * fdy);
            if (fd > 0.0f) {
                pursue_x = fdx / fd;
                pursue_y = fdy / fd;
            }
        }

        float cluster_x = 0.0f, cluster_y = 0.0f;
        if (cdist > 0.0f) {
            cluster_x = cdx / cdist;
            cluster_y = cdy / cdist;
        }

        // Cluster weight: 1.0 far from centroid, 0.0 when within
        // kClusterRadius. Smooth linear ramp avoids stop-start behaviour.
        float w_cluster = 0.0f;
        if (cdist > kClusterRadius) {
            w_cluster = (cdist - kClusterRadius) / cdist;
            if (w_cluster > 1.0f) w_cluster = 1.0f;
        }
        const float w_pursue = 1.0f - w_cluster;

        heading_x = w_pursue * pursue_x + w_cluster * cluster_x;
        heading_y = w_pursue * pursue_y + w_cluster * cluster_y;
    }

    const float hmag = std::sqrt(heading_x * heading_x + heading_y * heading_y);
    if (hmag > 0.0f) {
        const float scale = params->max_velocity / hmag;
        out_action->velocity.x = heading_x * scale;
        out_action->velocity.y = heading_y * scale;
    } else {
        out_action->velocity.x = 0.0f;
        out_action->velocity.y = 0.0f;
    }

    // -- Step 4: attack decision ------------------------------------------
    //
    // If the team-focused target is in our own range, attack it (focus-fire
    // bonus). Otherwise, opportunistically attack *any* enemy in our own
    // range (prefer the one with lowest id for determinism). This prevents
    // the wasted ticks that a strict "only attack the team focus" rule
    // causes when different drones are close to different enemies early
    // in the match.
    int action_target = -1;
    if (my_cooldown == 0) {
        const float range_sq = params->disable_range * params->disable_range;
        if (focus >= 0) {
            const float mdx = enemies[focus].pos.x - my_pos.x;
            const float mdy = enemies[focus].pos.y - my_pos.y;
            if (mdx * mdx + mdy * mdy <= range_sq) {
                action_target = focus;
            }
        }
        if (action_target < 0) {
            for (int i = 0; i < n_enemies; ++i) {
                if (!enemies[i].alive) continue;
                const float dx = enemies[i].pos.x - my_pos.x;
                const float dy = enemies[i].pos.y - my_pos.y;
                if (dx * dx + dy * dy <= range_sq) {
                    action_target = i;
                    break;  // lowest-id in-range enemy — deterministic
                }
            }
        }
    }
    out_action->target_id = action_target;

    // -- Step 5: broadcast ------------------------------------------------
    out_action->message_out[0] = centroid_x;
    out_action->message_out[1] = centroid_y;
    out_action->message_out[2] = static_cast<float>(focus);
    out_action->message_out[3] = static_cast<float>(alive_count);
}

}  // namespace TEAM_NS_PLACEHOLDER
