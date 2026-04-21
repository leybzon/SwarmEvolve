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

**Phase 1: Local MVP** (Current)
- [ ] Repository structure initialization
- [ ] Core engine with bounded physics and deterministic combat
- [ ] Baseline dummy AI implementations
- [ ] Python compiler/visualizer pipeline
- [ ] JSON trace → MP4 video rendering

**Phase 2: Evolutionary Orchestration** (Upcoming)
- [ ] Multi-round fitness evaluation
- [ ] Loop guard injection system
- [ ] LLM compilation retry logic

**Phase 3: GPU Scale & LLM Integration** (Future)
- [ ] NVIDIA Spark cluster deployment
- [ ] OpenACC parallelization verification
- [ ] Claude/Gemini API integration

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

# Run simulation with trace recording
./swarmevolve --record trace.jsonl

# Generate visualization
python3 scripts/visualizer.py trace.jsonl output.mp4
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

- **Arena**: Fixed rectangular boundary, positions clamped to edges
- **Movement**: Velocity vectors limited by `max_velocity`, updated synchronously
- **Combat**: Distance-based disable attacks with cooldown periods
- **Information Asymmetry**: Drones see enemy positions but not enemy cooldowns
- **Communication**: 4-float message protocol between team members per tick
- **Memory**: 16-float persistent memory per drone across ticks

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)**: Detailed system design and component interactions
- **[SPECIFICATION.md](SPECIFICATION.md)**: Complete technical specification and data models
- **[DEVELOPMENT.md](DEVELOPMENT.md)**: Development phases, directory structure, and workflow
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
