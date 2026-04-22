// SPDX-License-Identifier: MIT
//
// Unit tests for the four game-loop phases in src/engine.h.
//
// Dependency-free (no GoogleTest) so the existing Makefile pattern
// (tests/test_*.cpp → build/test_*) continues to work without extra tooling.
// On test failure, the program prints a descriptive line to stderr and
// returns a non-zero exit code; the Makefile's test-cpp target will stop
// the run via `set -e`.
//
// Coverage (per IMPLEMENTATION_PLAN §4 M2 "Tests"):
//   - Velocity clamping (input 10·max → output exactly max).
//   - Boundary clamp at each arena edge.
//   - Combat range boundary.
//   - Mutual destruction: both die, both cooldowns set.
//   - Focus fire: 3 attackers, 1 target, all 3 cooldowns set, target dies.
//   - Invalid target IDs (−2, num_drones, own index, etc.) → no cooldown.
//   - Dead drones: messages zeroed on next tick.

#include "../src/engine.h"
#include "../src/types.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

// -- tiny test harness ------------------------------------------------------

int g_failures = 0;

#define CHECK(cond)                                                                \
    do {                                                                           \
        if (!(cond)) {                                                             \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);   \
            ++g_failures;                                                          \
        }                                                                          \
    } while (0)

#define CHECK_NEAR(a, b, tol)                                                       \
    do {                                                                            \
        const float _a = (a), _b = (b);                                             \
        if (std::fabs(_a - _b) > (tol)) {                                           \
            std::fprintf(stderr, "FAIL %s:%d  |%g - %g| > %g\n",                    \
                         __FILE__, __LINE__, _a, _b, (tol));                        \
            ++g_failures;                                                           \
        }                                                                           \
    } while (0)

// -- helpers ----------------------------------------------------------------

GameParams default_params() {
    GameParams p{};
    p.arena_width   = 1000.0f;
    p.arena_height  = 1000.0f;
    p.max_velocity  = 5.0f;
    p.disable_range = 50.0f;
    p.max_cooldown  = 10;
    p.num_drones_a  = 3;
    p.num_drones_b  = 3;
    p.max_ticks     = 1000;
    p.current_tick  = 0;
    return p;
}

AllyState make_drone(int id, float x, float y, int cooldown = 0, bool alive = true) {
    AllyState s{};
    s.id       = id;
    s.pos.x    = x;
    s.pos.y    = y;
    s.cooldown = cooldown;
    s.alive    = alive;
    return s;
}

// -- tests ------------------------------------------------------------------

void test_velocity_clamping() {
    GameParams p = default_params();
    AllyState drones[1] = { make_drone(0, 100.0f, 100.0f) };
    Action actions[1]{};
    // Input 10 × max_velocity along +x direction. After clamping, speed
    // should equal max_velocity exactly (no drift beyond FP epsilon).
    actions[0].velocity.x = 10.0f * p.max_velocity;
    actions[0].velocity.y = 0.0f;

    swarmevolve::engine::movement_phase(drones, actions, 1, p);

    CHECK_NEAR(drones[0].pos.x, 100.0f + p.max_velocity, 1e-5f);
    CHECK_NEAR(drones[0].pos.y, 100.0f,                  1e-5f);
}

void test_boundary_clamp_all_edges() {
    GameParams p = default_params();
    AllyState drones[4] = {
        make_drone(0, 0.0f, 500.0f),    // touching left
        make_drone(1, 1000.0f, 500.0f), // touching right
        make_drone(2, 500.0f, 0.0f),    // touching top
        make_drone(3, 500.0f, 1000.0f), // touching bottom
    };
    Action actions[4]{};
    // Try to push each drone past its boundary by max_velocity.
    actions[0].velocity.x = -10.0f;           // would go to -10
    actions[1].velocity.x =  10.0f;           // would go to 1010
    actions[2].velocity.y = -10.0f;           // would go to -10
    actions[3].velocity.y =  10.0f;           // would go to 1010

    swarmevolve::engine::movement_phase(drones, actions, 4, p);

    CHECK_NEAR(drones[0].pos.x, 0.0f,      1e-5f);
    CHECK_NEAR(drones[1].pos.x, 1000.0f,   1e-5f);
    CHECK_NEAR(drones[2].pos.y, 0.0f,      1e-5f);
    CHECK_NEAR(drones[3].pos.y, 1000.0f,   1e-5f);
}

void test_dead_drones_do_not_move() {
    GameParams p = default_params();
    AllyState drones[1] = { make_drone(0, 500.0f, 500.0f, /*cd=*/0, /*alive=*/false) };
    Action actions[1]{};
    actions[0].velocity.x = 100.0f;
    swarmevolve::engine::movement_phase(drones, actions, 1, p);
    CHECK_NEAR(drones[0].pos.x, 500.0f, 0.0f);
}

void test_combat_range_boundary_exact_hit() {
    GameParams p = default_params();
    p.num_drones_a = 1;
    p.num_drones_b = 1;

    AllyState a[1] = { make_drone(0, 0.0f, 0.0f) };
    AllyState b[1] = { make_drone(0, p.disable_range, 0.0f) };  // dist == range
    Action act_a[1]{};
    act_a[0].target_id = 0;

    int  new_cd_a[MAX_DRONES]   = {};
    bool deaths_b[MAX_DRONES]   = {};
    swarmevolve::engine::combat_phase_one_side(a, act_a, 1, b, 1, p, new_cd_a, deaths_b);

    CHECK(deaths_b[0] == true);
    CHECK(new_cd_a[0] == p.max_cooldown);
}

void test_combat_range_boundary_just_out_of_range() {
    GameParams p = default_params();
    p.num_drones_a = 1;
    p.num_drones_b = 1;

    AllyState a[1] = { make_drone(0, 0.0f, 0.0f) };
    AllyState b[1] = { make_drone(0, p.disable_range + 0.5f, 0.0f) };
    Action act_a[1]{};
    act_a[0].target_id = 0;

    int  new_cd_a[MAX_DRONES]   = {};
    bool deaths_b[MAX_DRONES]   = {};
    swarmevolve::engine::combat_phase_one_side(a, act_a, 1, b, 1, p, new_cd_a, deaths_b);

    CHECK(deaths_b[0] == false);
    CHECK(new_cd_a[0] == 0);  // no cooldown for out-of-range shot
}

void test_mutual_destruction() {
    // Both in range of each other, both attack. Both die in Cleanup; both
    // attackers pay cooldown because cooldowns are assigned at resolution
    // time, before deaths apply (SPECIFICATION §3.4 rule 5).
    GameParams p = default_params();
    p.num_drones_a = 1;
    p.num_drones_b = 1;

    AllyState a[1] = { make_drone(0, 0.0f, 0.0f) };
    AllyState b[1] = { make_drone(0, 10.0f, 0.0f) };  // well within range
    Action act_a[1]{}; act_a[0].target_id = 0;
    Action act_b[1]{}; act_b[0].target_id = 0;

    int  new_cd_a[MAX_DRONES] = {}, new_cd_b[MAX_DRONES] = {};
    bool deaths_a[MAX_DRONES] = {}, deaths_b[MAX_DRONES] = {};

    // Both attack phases read pre-death state → both succeed.
    swarmevolve::engine::combat_phase_one_side(a, act_a, 1, b, 1, p, new_cd_a, deaths_b);
    swarmevolve::engine::combat_phase_one_side(b, act_b, 1, a, 1, p, new_cd_b, deaths_a);

    CHECK(deaths_a[0] && deaths_b[0]);
    CHECK(new_cd_a[0] == p.max_cooldown);
    CHECK(new_cd_b[0] == p.max_cooldown);

    // Apply in cleanup order: cooldowns → deaths.
    swarmevolve::engine::apply_cooldowns(a, new_cd_a, 1);
    swarmevolve::engine::apply_cooldowns(b, new_cd_b, 1);
    swarmevolve::engine::apply_deaths(a, deaths_a, 1);
    swarmevolve::engine::apply_deaths(b, deaths_b, 1);

    CHECK(!a[0].alive && !b[0].alive);
    CHECK(a[0].cooldown == p.max_cooldown);
    CHECK(b[0].cooldown == p.max_cooldown);
}

void test_focus_fire_three_attackers_one_target() {
    GameParams p = default_params();
    p.num_drones_a = 3;
    p.num_drones_b = 1;

    AllyState a[3] = {
        make_drone(0, 10.0f, 0.0f),
        make_drone(1,  0.0f, 10.0f),
        make_drone(2, 20.0f, 20.0f),
    };
    AllyState b[1] = { make_drone(0, 0.0f, 0.0f) };
    Action act_a[3]{};
    act_a[0].target_id = 0;
    act_a[1].target_id = 0;
    act_a[2].target_id = 0;

    int  new_cd_a[MAX_DRONES] = {};
    bool deaths_b[MAX_DRONES] = {};
    swarmevolve::engine::combat_phase_one_side(a, act_a, 3, b, 1, p, new_cd_a, deaths_b);

    CHECK(deaths_b[0] == true);
    CHECK(new_cd_a[0] == p.max_cooldown);
    CHECK(new_cd_a[1] == p.max_cooldown);
    CHECK(new_cd_a[2] == p.max_cooldown);
}

void test_invalid_target_ids_do_not_charge_cooldown() {
    GameParams p = default_params();
    p.num_drones_a = 4;
    p.num_drones_b = 2;

    AllyState a[4] = {
        make_drone(0, 0.0f, 0.0f),
        make_drone(1, 0.0f, 0.0f),
        make_drone(2, 0.0f, 0.0f),
        make_drone(3, 0.0f, 0.0f),
    };
    AllyState b[2] = {
        make_drone(0, 500.0f, 500.0f, /*cd=*/0, /*alive=*/false),  // already dead
        make_drone(1, 5.0f, 0.0f),                                  // alive + in range
    };
    Action act_a[4]{};
    act_a[0].target_id = -2;    // invalid negative
    act_a[1].target_id = 99;    // >= n_def (out of range)
    act_a[2].target_id = 0;     // dead target
    act_a[3].target_id = 1;     // valid — should succeed

    int  new_cd_a[MAX_DRONES] = {};
    bool deaths_b[MAX_DRONES] = {};
    swarmevolve::engine::combat_phase_one_side(a, act_a, 4, b, 2, p, new_cd_a, deaths_b);

    CHECK(new_cd_a[0] == 0);
    CHECK(new_cd_a[1] == 0);
    CHECK(new_cd_a[2] == 0);
    CHECK(new_cd_a[3] == p.max_cooldown);
    CHECK(deaths_b[0] == false);  // was already dead, no new death flag
    CHECK(deaths_b[1] == true);
}

void test_dead_drone_messages_zeroed() {
    // After apply_deaths + route_messages, a newly-dead drone's message
    // slot in the outgoing message buffer is zeros (SPECIFICATION §3.5).
    AllyState drones[2] = {
        make_drone(0, 10.0f, 10.0f, /*cd=*/0, /*alive=*/true),
        make_drone(1, 20.0f, 20.0f, /*cd=*/0, /*alive=*/true),
    };
    Action actions[2]{};
    actions[0].message_out[0] = 7.5f;
    actions[0].message_out[1] = 8.5f;
    actions[1].message_out[0] = 1.25f;

    // Drone 1 dies this tick.
    bool deaths[2] = { false, true };
    swarmevolve::engine::apply_deaths(drones, deaths, 2);

    float msgs[MAX_DRONES][MSG_SIZE];
    // Pre-fill with nonzero sentinels to ensure route_messages actually
    // writes zeros and doesn't just leave stale data.
    for (int i = 0; i < MAX_DRONES; ++i)
        for (int j = 0; j < MSG_SIZE; ++j)
            msgs[i][j] = -999.0f;

    swarmevolve::engine::route_messages(drones, actions, msgs, 2);

    CHECK_NEAR(msgs[0][0], 7.5f, 0.0f);
    CHECK_NEAR(msgs[0][1], 8.5f, 0.0f);
    for (int j = 0; j < MSG_SIZE; ++j) {
        CHECK_NEAR(msgs[1][j], 0.0f, 0.0f);
    }
}

void test_cooldown_decrement_only_on_alive() {
    AllyState drones[2] = {
        make_drone(0, 0, 0, /*cd=*/5, /*alive=*/true),
        make_drone(1, 0, 0, /*cd=*/5, /*alive=*/false),
    };
    swarmevolve::engine::decrement_cooldowns(drones, 2);
    CHECK(drones[0].cooldown == 4);
    CHECK(drones[1].cooldown == 5);  // dead drone does not decrement
}

void test_termination_a_wins_when_b_empty() {
    AllyState a[2] = { make_drone(0, 0, 0), make_drone(1, 0, 0) };
    AllyState b[2] = {
        make_drone(0, 0, 0, 0, /*alive=*/false),
        make_drone(1, 0, 0, 0, /*alive=*/false),
    };
    swarmevolve::engine::Outcome out;
    const bool terminated = swarmevolve::engine::check_early_termination(a, 2, b, 2, &out);
    CHECK(terminated == true);
    CHECK(out == swarmevolve::engine::OUTCOME_TEAM_A_WIN);
}

void test_termination_draw_when_both_empty() {
    AllyState a[1] = { make_drone(0, 0, 0, 0, false) };
    AllyState b[1] = { make_drone(0, 0, 0, 0, false) };
    swarmevolve::engine::Outcome out;
    const bool terminated = swarmevolve::engine::check_early_termination(a, 1, b, 1, &out);
    CHECK(terminated == true);
    CHECK(out == swarmevolve::engine::OUTCOME_DRAW);
}

void test_attacker_on_cooldown_does_not_fire() {
    GameParams p = default_params();
    AllyState a[1] = { make_drone(0, 0.0f, 0.0f, /*cd=*/3) };
    AllyState b[1] = { make_drone(0, 10.0f, 0.0f) };
    Action act_a[1]{}; act_a[0].target_id = 0;

    int  new_cd_a[MAX_DRONES] = {};
    bool deaths_b[MAX_DRONES] = {};
    swarmevolve::engine::combat_phase_one_side(a, act_a, 1, b, 1, p, new_cd_a, deaths_b);

    CHECK(deaths_b[0] == false);
    CHECK(new_cd_a[0] == 0);
}

}  // namespace

int main() {
    test_velocity_clamping();
    test_boundary_clamp_all_edges();
    test_dead_drones_do_not_move();
    test_combat_range_boundary_exact_hit();
    test_combat_range_boundary_just_out_of_range();
    test_mutual_destruction();
    test_focus_fire_three_attackers_one_target();
    test_invalid_target_ids_do_not_charge_cooldown();
    test_dead_drone_messages_zeroed();
    test_cooldown_decrement_only_on_alive();
    test_termination_a_wins_when_b_empty();
    test_termination_draw_when_both_empty();
    test_attacker_on_cooldown_does_not_fire();

    if (g_failures > 0) {
        std::fprintf(stderr, "%d failure(s)\n", g_failures);
        return 1;
    }
    std::printf("OK (13 tests)\n");
    return 0;
}
