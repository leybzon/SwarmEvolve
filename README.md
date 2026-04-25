# SwarmEvolve

**An evolutionary testbed for LLM-driven drone swarm combat**

SwarmEvolve pits Large Language Models against each other in a competitive programming environment where they write C++ control logic for autonomous drone swarms. Through evolutionary pressure and thousands of GPU-accelerated simulations, the system discovers effective swarm tactics.

## What is SwarmEvolve?

SwarmEvolve is a research platform that tests evolutionary software development by:

1. **LLMs as Commanders**: Claude, Gemini, and other LLMs generate low-level C++ code controlling drone behavior
2. **Massive Parallelization**: GPU acceleration via OpenACC enables thousands of simultaneous match simulations
3. **Evolutionary Feedback**: Match outcomes drive iterative code improvement through fitness-based selection
4. **Deterministic Execution**: Perfectly synchronous game loops ensure fair, reproducible competition

## Key Features

- **GPU-Accelerated Simulation**: Execute thousands of drone logic loops in parallel using NVIDIA OpenACC
- **Cross-Platform**: Compiles on macOS (Apple Silicon, CPU fallback) and Linux (NVIDIA GPU acceleration)
- **Safe AI Execution**: Automatic loop guard injection prevents LLM-generated code from crashing GPU threads
- **Decoupled Visualization**: Python-based video rendering from JSON trace files
- **POD-Only Architecture**: Strict memory safety constraints for GPU device thread execution

## Project Status

All milestones M0–M14 from [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) are
landed on `main`. The system is functional end-to-end: LLMs can generate C++
drone AIs, those AIs compete in sandboxed matches, evolutionary loops iterate
on fitness, and tournaments rank populations by Elo.

**Phase 0: Specification** (Complete)
- [x] README, ARCHITECTURE, SPECIFICATION, DEVELOPMENT, CLAUDE, IMPLEMENTATION_PLAN

**Phase 1: Local MVP** (Complete — M0–M7)
- [x] Repository structure (`src/`, `scripts/`, `tests/`, `data/`)
- [x] `src/types.h` POD definitions and ABI freeze (M1)
- [x] CPU engine with bounded physics and deterministic combat (M2)
- [x] Baseline AIs: `stationary_v1`, `pursuit_v1`, `cluster_v1` (M3)
- [x] Trace format + determinism tests (M4) and MP4 visualizer (M5)
- [x] Loop-guard injector (M6) and orchestrator CLI (M7)

**Phase 2: Sandboxing & Evolutionary Orchestration** (Complete — M8–M10)
- [x] Sandbox container for untrusted LLM binaries (M8)
- [x] Fitness evaluator with multi-seed scoring & experiment log (M9)
- [x] Anthropic / Gemini clients + evolutionary loop (`scripts/evolve.py`, M10)

**Phase 3: GPU Scale, Tournament, Demo** (Complete — M11–M14)
- [x] OpenACC GPU port with profiling harness (M11)
- [x] Round-robin / Swiss tournament runner with Elo (M12)
- [x] GPU scaling study to N=100K drones (M13 — ~6.7× over OpenMP, crossover ~4K)
- [x] Media & demo artefacts (M14)

See recent commits for milestone landings; the head of `main` is the most up-to-date reference.

## Quick Start

### Prerequisites
- **macOS**: Xcode Command Line Tools (clang++)
- **Linux**: NVIDIA HPC SDK (nvc++)
- **Python**: 3.8+ with matplotlib, numpy, opencv-python

### Build & Run

```bash
# Clone repository
git clone https://github.com/yourusername/SwarmEvolve.git
cd SwarmEvolve

# macOS build (CPU)
clang++ -std=c++17 -O3 -o swarmevolve src/engine.cpp src/a/*.cpp src/b/*.cpp

# Linux build (GPU)
nvc++ -std=c++17 -O3 -acc=gpu -gpu=managed -o swarmevolve src/engine.cpp src/a/*.cpp src/b/*.cpp

# Run a match. Both flags are optional:
#   --record <path>  Write a JSON-Lines trace to <path>. Omit to skip recording.
#   --seed <int>     Seed the initial-position PRNG (default 0).
./swarmevolve                             # run, no trace file
./swarmevolve --seed 42                   # reproducible run, no trace
./swarmevolve --record trace.jsonl        # record to trace.jsonl
./swarmevolve --record trace.jsonl --seed 42

# Generate visualization (basic)
python3 scripts/visualizer.py trace.jsonl output.mp4

# With optional intro text (shown for 1 second)
python3 scripts/visualizer.py trace.jsonl output.mp4 \
    --intro-text "Generation 33 Champion\nvs pursuit_v1"

# Video includes:
#   - Optional intro (1 sec if --intro-text provided)
#   - Step counter throughout simulation
#   - Team alive counts
#   - Winner and final step display (2 sec hold at end)
```

### Evolve & Tournament

```bash
# One-shot: ask an LLM to write a Team A AI against pursuit_v1, compile, match
export ANTHROPIC_API_KEY=...
python3 scripts/evolve_once.py --opponent src/baselines/pursuit_v1.cpp \
    --as-team A --out-dir data/runs/evolve_once

# Full evolutionary loop (multi-generation, multi-seed fitness)
python3 scripts/evolve.py --help

# Round-robin tournament with Elo ratings
python3 scripts/tournament.py \
    --ai src/baselines/pursuit_v1.cpp --name pursuit \
    --ai src/baselines/cluster_v1.cpp --name cluster \
    --mode round_robin --n-matches 10 --out-dir data/runs/tourney
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   Python Orchestrator                   │
│  (Compilation, LLM API, Safety Injection, Viz)          │
└────────────┬─────────────────────────────┬──────────────┘
             │                             │
             ├─── LLM API Calls ───────────┤
             │    (Claude, Gemini)         │
             │                             │
             ▼                             ▼
      ┌─────────────┐             ┌─────────────┐
      │  Team A AI  │             │  Team B AI  │
      │  (src/a/)   │             │  (src/b/)   │
      └──────┬──────┘             └──────┬──────┘
             │                           │
             └──────────┬────────────────┘
                        │
                        ▼
              ┌───────────────────┐
              │   Engine (C++)    │
              │  • Physics        │
              │  • Combat         │
              │  • State Mgmt     │
              │  (GPU-Accelerated)│
              └─────────┬─────────┘
                        │
                        ▼
                  trace.jsonl ──► MP4 Video
```

## Game Rules

### Arena & Movement
- **Arena**: Fixed rectangular boundary (e.g., 1000×1000 units)
  - Drones cannot leave the arena
  - Positions are clamped to boundaries (no wrapping or bouncing)
- **Movement**: Each tick, drones output a velocity vector
  - Velocity is clamped to `max_velocity` (e.g., 5.0 units/tick)
  - Positions update instantaneously (no inertia or acceleration)
  - Movement happens synchronously for all drones

### Combat System
- **Attack Mechanism**: Distance-based disable attacks
  - Attack succeeds if target is within `disable_range` (e.g., 50 units)
  - Attacker must have `cooldown == 0`
  - Successful attack kills the target instantly
- **Cooldown Penalty**: After a *successful* attack, drone enters cooldown
  - Cooldown duration: `max_cooldown` ticks (e.g., 10 ticks)
  - Cooldown is only consumed when the target is a **valid, alive enemy within range**
  - Attacks on out-of-range or already-dead targets are silently ignored and cost no cooldown
  - Cannot attack again until cooldown reaches 0
  - See [SPECIFICATION.md §3.4](SPECIFICATION.md) for the authoritative resolution rules
- **Mutual Destruction**: If two drones attack each other while in range, both die
- **Focus-Fire**: Multiple drones can attack the same target
  - Target dies once
  - Every attacker whose range check succeeds at the moment of resolution pays cooldown
  - No coordination bonus or penalty (attackers do not "waste" shots on a confirmed kill within the same tick — they share the hit synchronously)

### Information & Communication
- **Team Visibility**: Full visibility of all teammates
  - Position, cooldown status, alive/dead state
- **Enemy Visibility**: Limited visibility of enemies
  - Can see: position, alive/dead state
  - **Cannot see: enemy cooldowns** (must be inferred)
- **Communication**: 4-float message protocol per tick
  - Each drone broadcasts a message to all teammates
  - Messages delivered on the next tick
  - Can encode strategy, target priorities, positions, etc.
- **Persistent Memory**: 16 floats per drone
  - Persists across all ticks
  - Can store enemy tracking, cooldown estimates, internal state

### Execution Phases (Each Tick)
The game loop executes in strict order to ensure determinism:
1. **Query Phase**: All drones read game state and output actions (parallel)
2. **Movement Phase**: Update all drone positions based on velocity (sequential)
3. **Combat Phase**: Resolve all attacks simultaneously (synchronous)
4. **Cleanup Phase**: Apply deaths, decrement cooldowns, route messages

### Win Conditions
- **Elimination**: All enemy drones destroyed (your team wins)
- **Mutual Elimination**: Both teams eliminated simultaneously (draw)
- **Timeout**: Maximum ticks reached (e.g., 1000)
  - Team with more survivors wins
  - Equal survivors = draw

### Strategic Considerations
- **Information Asymmetry**: Cannot see enemy cooldowns, creating uncertainty
- **Cooldown Management**: Wasting attacks on dead targets still costs cooldown
- **Coordination**: Communication protocol enables swarm tactics
- **Risk/Reward**: Aggressive play risks mutual destruction

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)**: Detailed system design and component interactions
- **[SPECIFICATION.md](SPECIFICATION.md)**: Complete technical specification and data models
- **[DEVELOPMENT.md](DEVELOPMENT.md)**: Development phases, directory structure, and workflow
- **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**: Ordered milestones, tests, CI, and engineering practices
- **[CLAUDE.md](CLAUDE.md)**: Guidance for Claude Code AI assistant

## Contributing

This is a research project. Contributions welcome for:
- Baseline AI strategy implementations
- Visualization enhancements
- GPU optimization profiling
- LLM prompt engineering for code generation

## License

MIT License - see LICENSE file for details

## Research Context

SwarmEvolve explores:
1. Can LLMs iteratively improve complex, stateful algorithms through evolutionary pressure?
2. What emergent behaviors arise from multi-agent systems optimized by competing AI?
3. How do GPU compute constraints affect LLM code generation quality?
