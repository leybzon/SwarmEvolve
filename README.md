# SwarmEvolve

**Evolutionary Software Development through LLM-Guided Co-Evolution**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Presentations](https://img.shields.io/badge/Presentations-GitHub%20Pages-blue)](https://leybzon.github.io/SwarmEvolve/)

SwarmEvolve is a research platform where Large Language Models compete by writing C++ control logic for autonomous drone swarms. Through co-evolutionary pressure and GPU-accelerated simulations, the system discovers emergent tactics, demonstrating how AI can iteratively improve complex algorithms through competitive selection.

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

## 🎯 Project Status

**Production Ready** - All core milestones complete (M0-M25). The system is fully functional end-to-end with co-evolution capabilities, comprehensive documentation, and browser-accessible presentations.

### Recent Milestones

**M25: Co-Evolution** (Complete - May 2026)
- [x] Dual-track co-evolutionary orchestration
- [x] Team A vs Team B alternating evolution
- [x] Fitness reversal demonstration (underdog defeats champion)
- [x] Red Queen dynamics with emergent tactics
- [x] Browser-accessible presentation with 17 visualizations
- [🎬 View M25 Presentation](https://leybzon.github.io/SwarmEvolve/m25_coevolution/)

**M20-M24: Refinements** (Complete)
- [x] Byte-identical reproducibility with per-track token budgets (M20)
- [x] Three-track evolutionary runners with resume semantics (M19)
- [x] Self-improvement retrospective and dual-LLM architecture (M22-M24)
- [x] Compile-retry loops with soft-fault tracking

**M0-M14: Core Platform** (Complete)
- [x] GPU-accelerated simulation engine (OpenACC)
- [x] Sandboxed LLM code execution
- [x] Evolutionary orchestration with fitness-based selection
- [x] Tournament system with Elo ratings
- [x] Deterministic replay and visualization

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for complete milestone history.

## Quick Start

### Prerequisites
- **macOS**: Xcode Command Line Tools (clang++)
- **Linux**: NVIDIA HPC SDK (nvc++)
- **Python**: 3.8+ with matplotlib, numpy, opencv-python

### Build & Run

```bash
# Clone repository
git clone https://github.com/leybzon/SwarmEvolve.git
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

### Evolution & Co-Evolution

```bash
# Set up API key
export ANTHROPIC_API_KEY=your-key-here

# Single-generation evolution (Team A vs pursuit_v1 baseline)
python3 scripts/evolve_dual.py \
    --opponent src/baselines/pursuit_v1.cpp \
    --as-team A \
    --planner-model claude-sonnet-4-20250514 \
    --coder-model claude-haiku-4-5 \
    --generations 10 \
    --n-matches 10 \
    --out-dir data/runs/evolve_test

# Co-evolution: Team A and Team B evolve against each other
python3 scripts/evolve_coevolve.py \
    --init-champion-a data/runs/m22_rq1_100gen/gen_0033/candidate.cpp \
    --init-champion-b src/baselines/pursuit_v1.cpp \
    --planner-model claude-sonnet-4-20250514 \
    --coder-model claude-haiku-4-5 \
    --rounds 100 \
    --n-matches 10 \
    --seed 42 \
    --out-dir data/runs/coevolve_test

# Tournament with Elo ratings
python3 scripts/tournament.py \
    --ai src/baselines/pursuit_v1.cpp --name pursuit \
    --ai src/baselines/cluster_v1.cpp --name cluster \
    --mode round_robin \
    --n-matches 10 \
    --out-dir data/runs/tourney
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

## 📚 Documentation

### Core Documentation
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and component interactions
- **[SPECIFICATION.md](SPECIFICATION.md)** - Complete technical specification and data models
- **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)** - Ordered milestones (M0-M25), tests, and CI practices
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Development workflow and directory structure

### Presentations
- **[M25: Code Evolution in the Wild](https://leybzon.github.io/SwarmEvolve/m25_coevolution/)** - Interactive browser presentation
- **[Presentations Index](https://leybzon.github.io/SwarmEvolve/)** - All presentations

### AI Assistant Guidance
- **[CLAUDE.md](CLAUDE.md)** - Instructions for Claude Code when working with this codebase
- **[presentations/CLAUDE.md](presentations/CLAUDE.md)** - Meta-guidance for presentation work

## 🤝 Contributing

Contributions are welcome! This is an active research project exploring:
1. Can LLMs iteratively improve complex algorithms through evolutionary pressure?
2. What emergent behaviors arise from multi-agent systems optimized by competing AI?
3. How does co-evolution accelerate learning compared to isolated evolution?

**Areas for Contribution:**
- Baseline AI strategy implementations
- Visualization enhancements (3D rendering, real-time playback)
- GPU optimization profiling
- LLM prompt engineering for code generation
- New evolutionary algorithms (Lamarckian, orthogenesis)
- Analysis tools for tactical emergence

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🎓 Research Highlights

### Key Findings from M25 Co-Evolution Experiment
- **Fitness Reversal**: Baseline code (66 LOC, -0.8 fitness) defeated a 100-generation champion (204 LOC, +1.0 fitness)
- **Learning Acceleration**: Co-evolution was 35% faster than isolated evolution
- **Emergent Tactics**: Formation Spread (80-unit spacing) emerged spontaneously at Round 31
- **Red Queen Effect**: Champion attempted 47 mutations, all rejected (trapped in local optimum)
- **Punctuated Equilibrium**: 6 tactical phases with sudden fitness jumps, not gradual improvement
- **Cost**: $10 in API credits, 90 minutes runtime, consumer GPU

### Research Questions
1. **Evolutionary Adaptability**: How does code complexity affect evolutionary flexibility?
2. **Emergent Behavior**: What novel tactics arise from multi-agent competition?
3. **LLM Code Quality**: Can LLMs generate performant, GPU-compatible C++ code?
4. **Co-evolution Dynamics**: Do competing populations accelerate each other's improvement?

## 📖 Citation

If you use SwarmEvolve in your research, please cite:

```bibtex
@software{swarmevolve2026,
  title = {SwarmEvolve: Evolutionary Software Development through LLM-Guided Co-Evolution},
  author = {Leybzon, Gene},
  year = {2026},
  url = {https://github.com/leybzon/SwarmEvolve},
  note = {M25: Code Evolution in the Wild}
}
```
