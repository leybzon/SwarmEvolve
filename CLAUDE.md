# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SwarmEvolve is an evolutionary software development testbed where Large Language Models compete by writing C++ control logic for autonomous drone swarms. The system uses GPU parallelization to simulate thousands of matches rapidly, enabling evolutionary strategy development.

## Architecture

- **C++ Engine** (`src/engine.cpp`): Core physics, combat resolution, deterministic state management
- **C++ AI Modules** (`src/a/`, `src/b/`): LLM-generated drone logic wrapped in `TeamA` and `TeamB` namespaces
- **Python Orchestrator** (`scripts/orchestrator.py`): Compilation, LLM API calls, safety injection, visualization

## Build Commands

### macOS (Apple Silicon - CPU fallback)
```bash
clang++ -std=c++17 -O3 -o swarmevolve src/engine.cpp src/a/*.cpp src/b/*.cpp
```

### Linux (NVIDIA GPU - OpenACC acceleration)
```bash
nvc++ -std=c++17 -O3 -acc=gpu -gpu=managed -o swarmevolve src/engine.cpp src/a/*.cpp src/b/*.cpp
```

### Run with trace output
```bash
./swarmevolve --record trace.jsonl
python3 scripts/visualizer.py trace.jsonl output.mp4
```

## Critical Constraints

### GPU Memory Safety
- **No dynamic allocation** in AI code: No `new`, `malloc`, `std::vector`, `std::string`
- **POD structures only**: All data passed to AI must be Plain Old Data (see `src/types.h`)
- **Fixed-size arrays**: `MSG_SIZE=4`, `MEM_SIZE=16`, `MAX_DRONES=50` defined at compile time (runtime team size given by `GameParams::num_drones`, must satisfy `num_drones <= MAX_DRONES`)

### Namespace Isolation
- Team A AI must be in `namespace TeamA { ... }`
- Team B AI must be in `namespace TeamB { ... }`
- Both implement: `void drone_ai(int my_id, const GameParams* params, const AllyState* allies, const EnemyState* enemies, const float incoming_messages[][MSG_SIZE], float* my_memory, Action* out_action)`
- Mark functions with `#pragma acc routine seq` for GPU execution

### Security Requirements
- LLM-generated code **must** have loop guards injected by Python orchestrator
- Transform `while(cond)` → `int _g=0; while(cond) { if(++_g>1000) break; ... }`
- Prevents GPU TDR (Timeout Detection and Recovery) crashes

## Game Loop Phases (Deterministic)

1. **Query Phase**: Read-only parallel AI calls for all alive drones
2. **Movement Phase**: Clamp velocities, update positions, arena boundary enforcement
3. **Combat Phase**: Synchronous distance checks, mutual destruction allowed, cooldown penalties
4. **Cleanup Phase**: Apply deaths, decrement cooldowns, route messages for next tick

## Key Data Structures

Defined in `src/types.h`:
- `GameParams`: Arena bounds, velocity limits, combat ranges
- `AllyState`: Full teammate visibility (position, cooldown, alive status)
- `EnemyState`: Limited enemy visibility (position, alive status) - **cooldowns hidden**
- `Action`: Output per drone (velocity vector, target_id, message_out[4])

## Development Workflow

1. Write AI logic in `src/a/team_a_ai.cpp` or `src/b/team_b_ai.cpp`
2. Ensure proper namespace wrapping and OpenACC pragmas
3. Test compilation on macOS first (catches syntax errors quickly)
4. Run Python orchestrator for loop injection before compilation
5. Compile and execute inside the sandbox container (see ARCHITECTURE.md §Layer 4).
   Untrusted LLM binaries must not run on the host.
6. Generate trace files for match visualization (opt-in via `--record <path>`)

## Testing

- **Unit tests**: Test individual AI functions with mock game state
- **Integration tests**: Run full matches with known baseline AIs
- **Determinism tests**: Verify identical output for identical initial conditions
- **Safety tests**: Verify loop guard injection prevents infinite loops
