# SwarmEvolve Development Guide

This document outlines the development phases, directory structure, workflow, and implementation roadmap for SwarmEvolve.

## Development Phases

### Phase 1: Local MVP (Current Objective)

**Goal**: Create a functional simulation that runs on macOS with CPU execution and produces visualizations.

#### 1.1 Repository Structure

```
DroneEvolution/
├── src/
│   ├── types.h              # POD data structures (GameParams, AllyState, etc.)
│   ├── engine.cpp           # Main simulation loop
│   ├── a/
│   │   └── team_a_ai.cpp    # Team A AI implementation (baseline or LLM-generated)
│   └── b/
│       └── team_b_ai.cpp    # Team B AI implementation
├── scripts/
│   ├── orchestrator.py      # Main coordinator (compilation, LLM, safety)
│   ├── visualizer.py        # JSON trace → MP4 converter
│   ├── inject_guards.py     # Loop guard injection module
│   └── llm_client.py        # API wrappers for Claude, Gemini
├── tests/
│   ├── test_engine.cpp      # Unit tests for engine logic
│   ├── test_determinism.py  # Verify identical outputs
│   └── test_baseline_ai.py  # Baseline AI performance tests
├── data/
│   ├── traces/              # Output .jsonl files
│   └── videos/              # Output .mp4 files
├── docs/
│   ├── ARCHITECTURE.md      # System architecture (this file's sibling)
│   ├── SPECIFICATION.md     # Technical specification
│   └── DEVELOPMENT.md       # This file
├── .gitignore
├── README.md
├── CLAUDE.md                # Claude Code guidance
└── Makefile                 # Build automation
```

#### 1.2 Implementation Tasks

**Task 1.1: Core Data Structures**
- Create `src/types.h` with POD structures
- Verify compatibility: compile with both `clang++` and `nvc++` (if available)
- Write simple test to ensure structures are trivially copyable

**Task 1.2: Engine Core**
- Implement `src/engine.cpp`:
  - Initialization (random spawn positions)
  - Four-phase game loop (Query, Movement, Combat, Cleanup)
  - Boundary enforcement
  - Velocity clamping
  - Distance-based combat resolution
  - JSON Lines trace output
- Compile and run with dummy AI (all drones stationary)

**Task 1.3: Baseline AI Implementations**
- Create `src/a/team_a_ai.cpp`:
  - Simple "nearest-enemy pursuit" strategy
  - Attack when in range and cooldown is zero
  - Use namespace `TeamA` with OpenACC pragmas
- Create `src/b/team_b_ai.cpp`:
  - Different baseline (e.g., "stay together, focus-fire")
  - Use namespace `TeamB`

**Task 1.4: Visualization Pipeline**
- Implement `scripts/visualizer.py`:
  - Parse `.jsonl` trace file
  - Render arena with `matplotlib` or `opencv`:
    - Blue circles for Team A
    - Red circles for Team B
    - Gray circles for dead drones
    - Semi-transparent circle for attack range
    - Cooldown bar below each drone
  - Generate `.mp4` video at 30 FPS

**Task 1.5: Orchestrator (Basic)**
- Implement `scripts/orchestrator.py`:
  - Detect OS (macOS vs. Linux)
  - Compile with appropriate flags
  - Run simulation with `--record trace.jsonl`
  - Call visualizer on output
  - Return match outcome and statistics

**Task 1.6: Testing**
- Write determinism test: identical inputs → identical outputs
- Write baseline performance test: run 100 matches, compute win rate
- Verify trace files are valid JSON Lines

#### 1.7 Acceptance Criteria

- [ ] Simulation compiles on macOS with `clang++`
- [ ] Baseline AIs produce reasonable behavior (no crashes, drones move/attack)
- [ ] JSON trace files generated correctly
- [ ] Visualization renders video showing combat evolution
- [ ] Determinism test passes: 10 runs with same seed produce identical traces
- [ ] Performance: 10 drones per team, 1000 ticks completes in < 5 seconds

---

### Phase 2: Evolutionary Orchestration

**Goal**: Implement multi-round fitness evaluation, loop guard injection, and LLM API integration.

#### 2.1 Implementation Tasks

**Task 2.1: Loop Guard Injection**
- Implement `scripts/inject_guards.py`:
  - Parse C++ with regex or `libclang` AST
  - Identify `while`, `for`, `do-while` loops
  - Inject guard variables: `int _guard_N = 0; if (++_guard_N > 1000) break;`
  - Handle nested loops (unique guard per loop)
  - Write transformed code to temporary file

**Task 2.2: Multi-Round Fitness Evaluation**
- Extend `scripts/orchestrator.py`:
  - Run N matches (e.g., 100) with same AI code
  - Aggregate outcomes into fitness score:
    - Mean score: average advantage
    - Win rate: percentage of matches won
    - Survival rate: average drones alive at end
  - Return fitness dictionary

**Task 2.3: LLM API Client**
- Implement `scripts/llm_client.py`:
  - Wrappers for Anthropic Claude API
  - Wrappers for Google Gemini API
  - Prompt template:
    ```
    You are an AI developing drone swarm tactics. Here is your current code:

    [CURRENT_CODE]

    Your recent match results:
    - Win rate: 35%
    - Average survivors: 2.3 drones
    - Common failure mode: Drones cluster and become vulnerable to focus-fire

    Modify the C++ code to improve performance. Constraints:
    - Must use namespace TeamA
    - No dynamic allocation (new, malloc, vector, string)
    - Must include #pragma acc routine seq
    - Must implement: void drone_ai(int my_id, const GameParams* params, ...)

    Return ONLY the C++ code, no explanation.
    ```
  - Parse LLM response to extract code blocks
  - Retry logic for malformed responses

**Task 2.4: Evolutionary Loop**
- Implement evolutionary orchestrator:
  1. Start with baseline AI code
  2. Evaluate fitness over 100 matches
  3. Call LLM with current code + fitness feedback
  4. Inject loop guards into LLM-generated code
  5. Attempt compilation:
     - If success: evaluate fitness
     - If failure: retry with error message
  6. If new fitness > old fitness, keep new code
  7. Repeat for N generations (e.g., 50)

**Task 2.5: Logging & Checkpoints**
- Log all generations: code, fitness, compilation status
- Save checkpoints: best code every 10 generations
- Generate fitness curve plot: `generation vs. fitness`

#### 2.2 Acceptance Criteria

- [ ] Loop guard injection transforms all loops correctly
- [ ] Injected guards prevent infinite loops (test with intentionally bad code)
- [ ] Multi-round evaluation produces consistent fitness scores
- [ ] LLM API successfully generates valid C++ code
- [ ] Evolutionary loop runs end-to-end for 50 generations
- [ ] Fitness improves over generations (manual validation of strategies)

---

### Phase 3: GPU Scale & LLM Integration

**Goal**: Deploy to NVIDIA GPU cluster, verify OpenACC scaling, and run large-scale evolutionary experiments.

#### 3.1 Implementation Tasks

**Task 3.1: GPU Deployment**
- Set up Linux environment with NVIDIA HPC SDK
- Install `nvc++` compiler
- Verify GPU availability: `nvidia-smi`
- Compile SwarmEvolve with `-acc=gpu -gpu=managed`
- Run profiling: `nsys profile ./swarmevolve`

**Task 3.2: OpenACC Optimization**
- Verify parallelization:
  - Check `nvc++ -Minfo=accel` output
  - Confirm GPU kernels are launched
- Profile GPU utilization:
  - Kernel execution time
  - Memory transfer overhead
  - Occupancy metrics
- Optimize if needed:
  - Adjust `#pragma acc` clauses
  - Minimize CPU↔GPU transfers
  - Increase drones per match (e.g., 50 per team)

**Task 3.3: Scale Testing**
- Benchmark performance:
  - 10 drones/team: X matches/second
  - 50 drones/team: Y matches/second
  - 100 drones/team: Z matches/second
- Target: 1000 matches in < 60 seconds for 10 drones/team

**Task 3.4: LLM Integration**
- Connect to Claude API with production keys
- Connect to Gemini API
- Implement A/B testing:
  - Team A evolved by Claude
  - Team B evolved by Gemini
  - Run tournament: 100 matches per pairing
- Compare evolutionary strategies:
  - Which LLM produces better tactics?
  - Which LLM adapts faster?

**Task 3.5: Distributed Execution**
- Run multiple evolutionary branches in parallel
- Each branch:
  - Independent LLM
  - Independent seed AI
  - Evolves for 100 generations
- Final tournament: all evolved AIs compete

**Task 3.6: Analysis & Visualization**
- Generate comprehensive reports:
  - Fitness curves for all branches
  - Strategy diversity analysis
  - Common emergent behaviors
  - Video compilation of best matches
- Publish results: blog post, research paper, GitHub README

#### 3.2 Acceptance Criteria

- [ ] Simulation runs on NVIDIA GPU with OpenACC
- [ ] GPU version is 10-100× faster than CPU
- [ ] 1000 matches complete in < 60 seconds (10 drones/team)
- [ ] Claude and Gemini both successfully evolve strategies
- [ ] Final tournament produces measurable performance differences
- [ ] Video showcase demonstrates emergent swarm behaviors

---

## Directory Structure Details

### `src/` - C++ Source Code

#### `src/types.h`
Defines all POD structures. No function implementations.

#### `src/engine.cpp`
Main simulation engine. Contains:
- `main()` function
- Game loop implementation
- Trace file writing
- Command-line argument parsing

Example structure:
```cpp
#include "types.h"
// Forward-declare AI entry points (no separate headers; namespaces live in the .cpp files)
namespace TeamA { void drone_ai(int, const GameParams*, const AllyState*, const EnemyState*,
                                const float (*)[MSG_SIZE], float*, Action*); }
namespace TeamB { void drone_ai(int, const GameParams*, const AllyState*, const EnemyState*,
                                const float (*)[MSG_SIZE], float*, Action*); }
#include <cstdio>
#include <cmath>
#include <cstring>

int main(int argc, char** argv) {
    // Parse args: --record trace.jsonl
    // Initialize game state
    // Run game loop
    // Write trace
    // Print outcome
    return 0;
}
```

#### `src/a/team_a_ai.cpp`, `src/b/team_b_ai.cpp`
AI implementations. Must provide:
```cpp
#include "../types.h"

namespace TeamA {
    #pragma acc routine seq
    void drone_ai(...) {
        // Strategy implementation
    }
}
```

### `scripts/` - Python Orchestration

#### `scripts/orchestrator.py`
Main coordinator. Command-line interface:
```bash
python3 scripts/orchestrator.py \
    --team-a src/a/team_a_ai.cpp \
    --team-b src/b/team_b_ai.cpp \
    --num-matches 100 \
    --output results.json \
    --visualize data/videos/match.mp4
```

#### `scripts/visualizer.py`
Trace renderer. Command-line interface:
```bash
python3 scripts/visualizer.py \
    data/traces/match_001.jsonl \
    data/videos/match_001.mp4 \
    --fps 30 \
    --resolution 1920x1080
```

#### `scripts/inject_guards.py`
Loop guard injection. Command-line interface:
```bash
python3 scripts/inject_guards.py \
    src/a/team_a_ai.cpp \
    src/a/team_a_ai_safe.cpp
```

#### `scripts/llm_client.py`
API client library. Example usage:
```python
from llm_client import ClaudeClient

client = ClaudeClient(api_key="...")
response = client.generate_ai_code(
    current_code="...",
    fitness_feedback="...",
    constraints="..."
)
print(response["code"])
```

### `tests/` - Testing

#### `tests/test_engine.cpp`
Unit tests for engine logic:
- Velocity clamping
- Boundary enforcement
- Combat resolution
- Cooldown decrement

Compile and run:
```bash
clang++ -std=c++17 tests/test_engine.cpp -o test_engine
./test_engine
```

#### `tests/test_determinism.py`
Determinism verification:
```python
import subprocess
import hashlib

def test_determinism():
    trace1 = run_simulation("--seed 42")
    trace2 = run_simulation("--seed 42")
    assert hashlib.md5(trace1).hexdigest() == hashlib.md5(trace2).hexdigest()
```

#### `tests/test_baseline_ai.py`
Baseline performance tests:
```python
def test_baseline_performance():
    fitness = evaluate_fitness("src/a/team_a_ai.cpp", num_matches=100)
    assert fitness["mean"] > -0.5  # Should not lose badly
    assert fitness["wins"] > 20    # Should win at least 20%
```

### `data/` - Output Files

#### `data/traces/`
JSON Lines trace files. Naming convention:
- `match_YYYYMMDD_HHMMSS_<team_a_hash>_vs_<team_b_hash>.jsonl`

#### `data/videos/`
MP4 visualizations. Naming matches traces:
- `match_YYYYMMDD_HHMMSS_<team_a_hash>_vs_<team_b_hash>.mp4`

---

## Workflow

### Local Development (macOS)

```bash
# 1. Write AI code
vim src/a/team_a_ai.cpp

# 2. Compile
make build-macos

# 3. Run simulation
./swarmevolve --record data/traces/test.jsonl

# 4. Visualize
python3 scripts/visualizer.py data/traces/test.jsonl data/videos/test.mp4

# 5. Watch video
open data/videos/test.mp4
```

### GPU Deployment (Linux)

```bash
# 1. SSH to GPU cluster
ssh gpu-node-01

# 2. Load NVIDIA HPC SDK
module load nvhpc/23.1

# 3. Compile with GPU support
make build-linux-gpu

# 4. Run batch simulations
python3 scripts/orchestrator.py \
    --team-a src/a/team_a_ai.cpp \
    --team-b src/b/team_b_ai.cpp \
    --num-matches 1000 \
    --output results.json

# 5. Profile GPU performance
nsys profile --stats=true ./swarmevolve

# 6. Download results
scp gpu-node-01:~/DroneEvolution/data/videos/*.mp4 ./local_videos/
```

### Evolutionary Experimentation

```bash
# Run evolutionary loop for 50 generations
python3 scripts/evolve.py \
    --llm claude \
    --generations 50 \
    --matches-per-gen 100 \
    --output experiments/claude_v1/

# Compare multiple LLMs
python3 scripts/evolve.py --llm claude --output experiments/claude/ &
python3 scripts/evolve.py --llm gemini --output experiments/gemini/ &
wait

# Run tournament
python3 scripts/tournament.py \
    experiments/claude/best.cpp \
    experiments/gemini/best.cpp \
    --num-matches 1000 \
    --output tournament_results.json
```

---

## Makefile

```makefile
# Makefile for SwarmEvolve

CXX_MACOS = clang++
CXX_LINUX = nvc++
CXXFLAGS = -std=c++17 -O3
ACCFLAGS = -acc=gpu -gpu=managed -Minfo=accel

SRC = src/engine.cpp src/a/team_a_ai.cpp src/b/team_b_ai.cpp
TARGET = swarmevolve

.PHONY: all build-macos build-linux-gpu clean test

all: build-macos

build-macos:
	$(CXX_MACOS) $(CXXFLAGS) $(SRC) -o $(TARGET)

build-linux-gpu:
	$(CXX_LINUX) $(CXXFLAGS) $(ACCFLAGS) $(SRC) -o $(TARGET)

test:
	$(CXX_MACOS) $(CXXFLAGS) tests/test_engine.cpp -o test_engine
	./test_engine
	python3 tests/test_determinism.py
	python3 tests/test_baseline_ai.py

clean:
	rm -f $(TARGET) test_engine
	rm -rf data/traces/*.jsonl
	rm -rf data/videos/*.mp4

run-demo:
	./$(TARGET) --record data/traces/demo.jsonl
	python3 scripts/visualizer.py data/traces/demo.jsonl data/videos/demo.mp4
	@echo "Video generated: data/videos/demo.mp4"
```

---

## Git Workflow

### Branching Strategy

- `main`: Stable releases only
- `develop`: Integration branch
- `feature/*`: Individual features (e.g., `feature/loop-guards`)
- `experiment/*`: Evolutionary experiments (not merged)

### Commit Messages

Follow conventional commits:
```
feat: Add loop guard injection system
fix: Resolve boundary clamping edge case
perf: Optimize GPU memory transfers
docs: Update architecture documentation
test: Add determinism verification tests
```

### Pull Request Process

1. Create feature branch: `git checkout -b feature/my-feature`
2. Implement feature with tests
3. Run test suite: `make test`
4. Push and open PR to `develop`
5. CI runs: compilation (macOS + Linux), tests, linting
6. Code review and merge

---

## Performance Targets

### Phase 1 (CPU, macOS)
- 10 drones/team, 1000 ticks: < 5 seconds
- 100 matches: < 10 minutes

### Phase 2 (CPU, macOS)
- 50 generations, 100 matches/gen: < 12 hours

### Phase 3 (GPU, Linux)
- 10 drones/team, 1000 ticks: < 0.05 seconds
- 1000 matches: < 60 seconds
- 100 generations, 1000 matches/gen: < 2 hours

---

## Troubleshooting

### Compilation Errors

**Error**: `undefined reference to TeamA::drone_ai`
- **Cause**: Missing namespace or incorrect function signature
- **Fix**: Verify namespace wrapping and exact signature match

**Error**: `#pragma acc` not recognized
- **Cause**: Using `clang++` (expected, not an error)
- **Fix**: This is normal for macOS builds

### Runtime Errors

**Error**: Simulation hangs indefinitely
- **Cause**: Infinite loop in AI code (loop guards not injected)
- **Fix**: Run `inject_guards.py` before compilation

**Error**: GPU TDR crash (Windows: "Display driver stopped responding")
- **Cause**: Kernel exceeded timeout (usually > 2 seconds)
- **Fix**: Reduce `num_drones` or `max_ticks`, verify loop guards

### Performance Issues

**Symptom**: GPU version slower than CPU
- **Cause**: Insufficient parallelism (too few drones)
- **Fix**: Increase `num_drones` to 50+, check `nvc++ -Minfo=accel` output

**Symptom**: Excessive memory usage
- **Cause**: Trace file too large (storing every tick for long matches)
- **Fix**: Reduce trace frequency (e.g., record every 10th tick)

---

## Next Steps After Phase 3

### Potential Extensions

1. **3D Simulation**: Extend to 3D arena with altitude control
2. **Energy Management**: Add fuel/energy constraints
3. **Varied Unit Types**: Scouts (fast, weak) vs. Tanks (slow, strong)
4. **Fog of War**: Limited enemy visibility based on distance
5. **Terrain**: Obstacles, cover, line-of-sight constraints
6. **Multi-Team Battles**: 3+ teams competing simultaneously
7. **Transfer Learning**: Pre-train on simpler tasks, fine-tune for SwarmEvolve
8. **Human vs. AI**: Allow human players to design strategies and compete

### Research Questions

1. Do evolutionary strategies converge or oscillate (rock-paper-scissors dynamics)?
2. Can LLMs discover novel tactics not seen in training data?
3. How does GPU memory constraint affect LLM code generation quality?
4. Can one LLM's evolved strategies be used to bootstrap another LLM's learning?

---

## Resources

### External Documentation
- [NVIDIA HPC SDK Documentation](https://docs.nvidia.com/hpc-sdk/)
- [OpenACC Best Practices](https://www.openacc.org/best-practices)
- [Anthropic Claude API](https://docs.anthropic.com/)
- [Google Gemini API](https://ai.google.dev/docs)

### Internal Documentation
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design
- [SPECIFICATION.md](SPECIFICATION.md) - Technical specification
- [CLAUDE.md](CLAUDE.md) - Claude Code AI guidance

### Community
- GitHub Discussions: Design proposals, feature requests
- Issues: Bug reports, feature tracking
- Discord: Real-time collaboration (if applicable)
