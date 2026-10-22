// SPDX-License-Identifier: MIT
//
// Forward declarations of the AI entry points for both teams.
//
// The engine includes this header (NOT the AI .cpp files) so that the engine
// and AI are compiled in separate translation units. This:
//   1. prevents engine-internal helpers from leaking into AI code,
//   2. lets the orchestrator replace a team's .cpp without recompiling the
//      engine (future incremental-compilation milestone), and
//   3. makes `#pragma acc routine seq` live where it belongs — next to the
//      function definition in the AI translation unit.
//
// The signatures below MUST match `drone_ai` as defined in SPECIFICATION.md
// §2.1 exactly. A mismatch is a linker error.

#ifndef SWARMEVOLVE_AI_ABI_H
#define SWARMEVOLVE_AI_ABI_H

#include "types.h"

namespace TeamA {

void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action);

} // namespace TeamA

namespace TeamB {

void drone_ai(int my_id, const GameParams* params, const AllyState* allies,
              const EnemyState* enemies, const float incoming_messages[][MSG_SIZE],
              float* my_memory, Action* out_action);

} // namespace TeamB

#endif // SWARMEVOLVE_AI_ABI_H
