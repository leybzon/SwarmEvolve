// SPDX-License-Identifier: MIT
//
// SwarmEvolve POD data structures.
//
// This header is the ONLY interface between the engine and AI modules. It
// must remain:
//   - pure C++17, no STL containers, no heap, no <iostream>
//   - compilable by clang++, g++, and nvc++
//   - Plain Old Data (trivially copyable, standard layout)
//
// See SPECIFICATION.md §1 for the authoritative field descriptions.

#ifndef SWARMEVOLVE_TYPES_H
#define SWARMEVOLVE_TYPES_H

#include <cstdint>
#include <type_traits>

namespace swarmevolve {

// ---------------------------------------------------------------------------
// Compile-time constants (SPECIFICATION.md §1.1)
// ---------------------------------------------------------------------------

/// Number of floats per inter-drone message payload.
inline constexpr int MSG_SIZE = 4;

/// Number of floats in per-drone persistent memory.
inline constexpr int MEM_SIZE = 16;

/// Maximum drones per team. Runtime team size (GameParams::num_drones_{a,b})
/// must satisfy `num_drones <= MAX_DRONES`.
inline constexpr int MAX_DRONES = 50;

// ---------------------------------------------------------------------------
// Vector2D  (SPECIFICATION.md §1.2)
// Coordinate system: origin top-left, +X right, +Y DOWN (screen space).
// ---------------------------------------------------------------------------
struct Vector2D {
    float x;
    float y;
};

// ---------------------------------------------------------------------------
// GameParams  (SPECIFICATION.md §1.3)
// All fields are constant during a match except `current_tick`.
// Team A size and Team B size are stored separately to permit asymmetric
// ("handicap") matches; by default the orchestrator sets them equal.
// ---------------------------------------------------------------------------
struct GameParams {
    float arena_width;
    float arena_height;
    float max_velocity;
    float disable_range;
    int   max_cooldown;
    int   num_drones_a;
    int   num_drones_b;
    int   max_ticks;
    int   current_tick;
};

// ---------------------------------------------------------------------------
// AllyState  (SPECIFICATION.md §1.4)
// Full visibility of teammate state including cooldown.
// ---------------------------------------------------------------------------
struct AllyState {
    int       id;
    Vector2D  pos;
    int       cooldown;
    bool      alive;
    // Explicit padding to a 4-byte multiple for predictable layout across
    // compilers. The static_assert below verifies size.
    std::uint8_t _pad[3];
};

// ---------------------------------------------------------------------------
// EnemyState  (SPECIFICATION.md §1.5)
// Limited visibility: no cooldown field. Information asymmetry is enforced
// at the type level — AI cannot access something that does not exist in the
// struct.
// ---------------------------------------------------------------------------
struct EnemyState {
    int       id;
    Vector2D  pos;
    bool      alive;
    std::uint8_t _pad[3];
};

// ---------------------------------------------------------------------------
// Action  (SPECIFICATION.md §1.6)
// AI output per tick. target_id == -1 means "do not attack".
// ---------------------------------------------------------------------------
struct Action {
    Vector2D  velocity;
    int       target_id;
    float     message_out[MSG_SIZE];
};

// ---------------------------------------------------------------------------
// Compile-time ABI guarantees.
// A change to any of these lines is a breaking ABI change and must be
// accompanied by an update to tests/fixtures/abi_golden.txt.
// ---------------------------------------------------------------------------

static_assert(std::is_trivially_copyable_v<Vector2D>);
static_assert(std::is_standard_layout_v<Vector2D>);
static_assert(sizeof(Vector2D) == 8, "Vector2D must be 8 bytes");

static_assert(std::is_trivially_copyable_v<GameParams>);
static_assert(std::is_standard_layout_v<GameParams>);

static_assert(std::is_trivially_copyable_v<AllyState>);
static_assert(std::is_standard_layout_v<AllyState>);
static_assert(sizeof(AllyState) <= 32, "AllyState should fit in half a cacheline");

static_assert(std::is_trivially_copyable_v<EnemyState>);
static_assert(std::is_standard_layout_v<EnemyState>);

static_assert(std::is_trivially_copyable_v<Action>);
static_assert(std::is_standard_layout_v<Action>);

static_assert(MSG_SIZE == 4, "Trace format depends on MSG_SIZE == 4");
static_assert(MEM_SIZE == 16, "Trace format depends on MEM_SIZE == 16");
static_assert(MAX_DRONES >= 1);

}  // namespace swarmevolve

// ---------------------------------------------------------------------------
// Top-level using-declarations for AI convenience.
// AI source files include <src/types.h> and may write `Vector2D` unqualified.
// Engine source files use `swarmevolve::Vector2D` explicitly.
// ---------------------------------------------------------------------------
using Vector2D   = swarmevolve::Vector2D;
using GameParams = swarmevolve::GameParams;
using AllyState  = swarmevolve::AllyState;
using EnemyState = swarmevolve::EnemyState;
using Action     = swarmevolve::Action;

using swarmevolve::MAX_DRONES;
using swarmevolve::MEM_SIZE;
using swarmevolve::MSG_SIZE;

#endif  // SWARMEVOLVE_TYPES_H
