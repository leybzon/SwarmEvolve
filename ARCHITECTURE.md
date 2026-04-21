# SwarmEvolve Architecture

This document describes the system architecture, component interactions, and technical design decisions for SwarmEvolve.

## Design Principles

1. **Strict Decoupling**: Simulation engine, AI code, and visualization are completely independent
2. **Deterministic Execution**: Identical inputs always produce identical outputs for fairness
3. **GPU-First Design**: All data structures and algorithms designed for GPU device thread execution
4. **Cross-Platform Portability**: Single codebase compiles for both macOS (CPU) and Linux (GPU)
5. **Safety by Design**: Multiple layers of protection against untrusted LLM-generated code

## System Components

### 1. C++ Simulation Engine (`src/engine.cpp`)

**Purpose**: Core game simulation with deterministic physics and combat resolution

**Responsibilities**:
- Initialize game state with configurable parameters
- Execute the main game loop (up to `max_ticks`)
- Coordinate the four-phase execution cycle
- Enforce arena boundaries and velocity constraints
- Resolve combat interactions synchronously
- Generate JSON Lines trace output for visualization

**Key Design Decisions**:
- **No STL containers**: Uses raw C arrays for GPU compatibility
- **Synchronous phases**: Read-only query phase strictly separated from state mutation phases
- **Deterministic floating-point**: No RNG, no time-based behavior, no race conditions
- **Trace-based debugging**: All state changes recorded to `.jsonl` for post-mortem analysis

**GPU Acceleration**:
All four tick phases are parallelized where it makes sense — per-drone loops in Query,
Movement, Combat (target resolution), and Cleanup all carry `#pragma acc parallel loop`.
The engine aims for maximum useful acceleration, not just the Query phase.

```cpp
// Query
#pragma acc parallel loop present(drones, actions)
for (int i = 0; i < num_drones; i++) {
    if (drones[i].alive) {
        TeamA::drone_ai(i, &params, allies, enemies, messages, memory[i], &actions[i]);
    }
}

// Movement
#pragma acc parallel loop present(drones, actions)
for (int i = 0; i < num_drones; i++) { /* clamp & integrate */ }

// Combat (per-attacker; writes to pending_deaths are independent per target, but
//         care is needed — use atomic OR or tolerate idempotent writes)
#pragma acc parallel loop present(drones_a, drones_b, actions_a, pending_deaths_b)
for (int i = 0; i < num_drones; i++) { /* range + target check */ }

// Cleanup (apply deaths, decrement cooldowns, route messages)
#pragma acc parallel loop present(drones, pending_deaths, messages, actions)
for (int i = 0; i < num_drones; i++) { /* ... */ }
```

**Combat parallelism note**: Multiple attackers targeting the same drone only ever
write `true` to the same `pending_deaths[target]` slot. This write is idempotent, so
a plain parallel loop is safe even without atomics. Cooldown writes target `drones[i].cooldown`
and are per-attacker (no aliasing).

### 2. AI Modules (`src/a/`, `src/b/`)

**Purpose**: Team-specific drone control logic (LLM-generated or baseline)

**Structure**:
```
src/
├── a/
│   └── team_a_ai.cpp  (namespace TeamA)
└── b/
    └── team_b_ai.cpp  (namespace TeamB)
```

**Interface Contract**:
```cpp
namespace TeamA {
    #pragma acc routine seq
    void drone_ai(
        int my_id,
        const GameParams* params,
        const AllyState* allies,
        const EnemyState* enemies,
        const float incoming_messages[][MSG_SIZE],
        float* my_memory,           // MEM_SIZE floats, persistent across ticks
        Action* out_action          // Output: velocity, target, message
    );
}
```

**Constraints**:
- Must compile with both clang++ and nvc++ without modifications
- No heap allocation (new, malloc, std::vector, std::string)
- No system calls, file I/O, or threading
- Must complete within 1000 iterations (enforced by loop guards)
- Must use `#pragma acc routine seq` for GPU device execution

### 3. Python Orchestrator (`scripts/orchestrator.py`)

**Purpose**: High-level coordination, compilation, safety, and visualization

**Responsibilities**:

#### 3.1 OS-Aware Compilation
```python
def compile_for_platform():
    if platform.system() == "Darwin":  # macOS
        return ["clang++", "-std=c++17", "-O3", ...]
    else:  # Linux
        return ["nvc++", "-std=c++17", "-O3", "-acc=gpu", "-gpu=managed", ...]
```

#### 3.2 Safety Code Injection
**Problem**: LLM-generated `while(true)` loops cause GPU TDR crashes

**Solution**: AST/regex transformation before compilation
```python
# Transform:
while (condition) {
    // user code
}

# Into:
int _guard_0 = 0;
while (condition) {
    if (++_guard_0 > 1000) break;
    // user code
}
```

#### 3.3 LLM API Integration
- Call Claude/Gemini APIs with previous match results and fitness scores
- Provide AI with:
  - Current code
  - Match outcome statistics
  - Opponent behavior observations
  - API constraints and examples
- Parse generated C++ code and validate syntax

#### 3.4 Fitness Evaluation
```python
def evaluate_fitness(team_code, num_matches=100):
    scores = []
    for match in range(num_matches):
        result = run_simulation(team_code)
        scores.append(calculate_score(result))
    return {
        "mean": np.mean(scores),
        "std": np.std(scores),
        "wins": sum(s > 0 for s in scores)
    }
```

#### 3.5 Visualization Pipeline
```python
def jsonl_to_video(trace_file, output_mp4):
    frames = []
    for line in open(trace_file):
        state = json.loads(line)
        frame = render_arena(state)  # matplotlib/opencv
        frames.append(frame)
    write_video(frames, output_mp4)
```

### 4. Data Layer (`src/types.h`)

**Purpose**: Define the strict interface boundary between engine and AI

**Key Structures**:

```cpp
// Fixed-size constants (no dynamic allocation)
constexpr int MSG_SIZE = 4;    // Float communication protocol
constexpr int MEM_SIZE = 16;   // Persistent memory per drone

struct Vector2D {
    float x, y;
};

struct GameParams {
    float arena_width, arena_height;
    float max_velocity;
    float disable_range;
    int max_cooldown;
    int num_drones_a;   // Team A size
    int num_drones_b;   // Team B size (equal to num_drones_a by default)
    int max_ticks;
    int current_tick;
};

struct AllyState {
    int id;
    Vector2D pos;
    int cooldown;
    bool alive;
};

struct EnemyState {
    int id;
    Vector2D pos;
    bool alive;
    // CRITICAL: cooldowns intentionally hidden
};

struct Action {
    Vector2D velocity;
    int target_id;               // -1 = no attack
    float message_out[MSG_SIZE]; // Broadcast to team next tick
};
```

**Design Rationale**:
- **POD Requirement**: GPU device memory must be copyable with `memcpy`
- **Information Asymmetry**: Enemy cooldowns hidden to create strategic depth
- **Fixed Arrays**: No `std::vector`, all sizes known at compile time
- **Float Protocol**: Messages are raw floats, AI must define semantics

## Execution Flow

### Game Loop Architecture

```
Tick 0:  Initialize → Query → Movement → Combat → Cleanup
         │            │        │          │         │
         ├─ Set positions, cooldowns, alive status
         │            │        │          │         │
         │            ├─ Call all drone_ai() in parallel (read-only)
         │                     │          │         │
         │                     ├─ Clamp velocities, update positions
         │                                │         │
         │                                ├─ Resolve attacks, mark deaths
         │                                          │
         │                                          ├─ Apply deaths, route messages
         │
Tick 1:  Query → Movement → Combat → Cleanup
         ...
Tick N:  Check win condition → Write trace → Exit
```

### Phase Details

#### Query Phase (Parallel, Read-Only)
```cpp
#pragma acc parallel loop
for (int i = 0; i < num_drones; i++) {
    if (team_a_drones[i].alive) {
        TeamA::drone_ai(i, params, allies_a, enemies_b, messages_a, memory_a[i], &actions_a[i]);
    }
}
```
- All drones read **identical** game state snapshot
- No state mutations allowed
- GPU threads run in parallel

#### Movement Phase (Parallel)
```cpp
for (int i = 0; i < num_drones; i++) {
    if (drones[i].alive) {
        // Clamp velocity
        float speed = sqrt(v.x*v.x + v.y*v.y);
        if (speed > max_velocity) {
            v.x *= max_velocity / speed;
            v.y *= max_velocity / speed;
        }

        // Update position
        drones[i].pos.x += v.x;
        drones[i].pos.y += v.y;

        // Enforce arena boundaries
        drones[i].pos.x = clamp(drones[i].pos.x, 0, arena_width);
        drones[i].pos.y = clamp(drones[i].pos.y, 0, arena_height);
    }
}
```

#### Combat Phase (Synchronous, Order-Independent)
```cpp
// Use temporary death buffers (one per team) to ensure synchronous resolution.
// SPECIFICATION.md §3.4 uses a single pending_deaths[MAX_DRONES * 2] buffer;
// splitting per team is equivalent and easier to read.
bool pending_deaths_a[MAX_DRONES] = {false};
bool pending_deaths_b[MAX_DRONES] = {false};

// Example: resolve Team A -> Team B attacks
for (int i = 0; i < num_drones; i++) {
    if (!team_a_drones[i].alive) continue;
    if (team_a_actions[i].target_id == -1) continue;
    if (team_a_drones[i].cooldown > 0) continue;

    int target = team_a_actions[i].target_id;
    if (target < 0 || target >= num_drones) continue;
    if (!team_b_drones[target].alive) continue;

    float dx = team_a_drones[i].pos.x - team_b_drones[target].pos.x;
    float dy = team_a_drones[i].pos.y - team_b_drones[target].pos.y;
    float dist = sqrt(dx*dx + dy*dy);

    if (dist <= disable_range) {
        pending_deaths_b[target] = true;
        team_a_drones[i].cooldown = max_cooldown;  // Only successful attacks cost cooldown
    }
}
// Mirror for Team B -> Team A
```

**Critical Rule**: Mutual destruction is valid. If A attacks B and B attacks A, both die if in range.

**Focus-Fire Penalty**: If 3 drones attack the same target, all 3 suffer full cooldowns but the target only dies once.

#### Cleanup Phase
```cpp
// Apply deaths
for (int i = 0; i < num_drones; i++) {
    if (pending_deaths[i]) {
        drones[i].alive = false;
    }
}

// Decrement cooldowns
for (int i = 0; i < num_drones; i++) {
    if (drones[i].alive && drones[i].cooldown > 0) {
        drones[i].cooldown--;
    }
}

// Route messages for next tick.
// Dead drones do not broadcast — their slots are zeroed.
for (int i = 0; i < num_drones; i++) {
    for (int j = 0; j < MSG_SIZE; j++) {
        incoming_messages[i][j] = drones[i].alive ? actions[i].message_out[j] : 0.0f;
    }
}
```

## Cross-Platform Strategy

### Compilation Differences

| Aspect          | macOS (clang++)        | Linux (nvc++)                |
|-----------------|------------------------|------------------------------|
| Compiler        | Apple Clang 14+        | NVIDIA HPC SDK 23.1+         |
| OpenACC         | Ignored (graceful)     | GPU acceleration enabled     |
| Execution       | Single-threaded CPU    | Parallel GPU threads         |
| Performance     | ~100 drones, 1000 ticks| ~10,000 drones, 1000 ticks   |
| Purpose         | Development/debugging  | Production simulations       |

### Pragma Strategy
```cpp
#pragma acc parallel loop present(data)  // nvc++: GPU launch, clang++: ignored
for (int i = 0; i < N; i++) {
    #pragma acc routine seq              // nvc++: device function, clang++: ignored
    process(i);
}
```

## Security Architecture

### Threat Model
- **Adversary**: Untrusted LLM-generated C++ code
- **Attack Vectors**:
  - Infinite loops (GPU TDR crash)
  - Out-of-bounds array access
  - Stack overflow (deep recursion)
  - Undefined behavior (null dereference)

### Defenses

#### Layer 1: Compilation-Time Injection (Python)
- Parse generated code before compilation
- Inject loop guards into all `while`, `for`, `do-while`
- Reject code with inline assembly or system calls

#### Layer 2: Compile-Time Constraints (C++)
- No standard library beyond `<cmath>`
- All pointers are `const` (except `out_action` and `my_memory`)
- Fixed-size arrays prevent buffer overruns

#### Layer 3: Runtime Monitoring (Engine / Orchestrator)
- Timeout detection: if the simulation process exceeds 10 s, the Python orchestrator SIGKILLs it
- Watchdog (Python-side thread in the orchestrator, **not** inside the GPU/AI code) monitors GPU activity via `nvidia-smi` polling
- Sanitizer builds (ASan/UBSan) for development testing
- NOTE: The "no threading" rule in SPECIFICATION §2.2 applies strictly to the AI modules (`src/a/*`, `src/b/*`); the engine and Python orchestrator may use threads.

#### Layer 4: Isolation (Deployment)

LLM-generated code is always compiled and executed inside a sandbox. The recommended
configuration (the "Claude-suggested" baseline) is:

- **Container**: rootless Podman/Docker image built from a minimal base (e.g.,
  `nvidia/cuda:*-runtime` on Linux, `alpine` + `clang` on macOS CI). The container is
  single-purpose: compile + run + emit trace.
- **Filesystem**: read-only rootfs with two bind-mounts — `/work/src` (read-only, the
  post-guard-injected sources) and `/work/out` (read-write, for `trace.jsonl` only).
- **Network**: `--network=none`. LLM code must have no outbound network access.
- **Resource limits**:
  - CPU: 1–2 cores (`--cpus=2`)
  - RAM: 512 MiB (`--memory=512m`)
  - PIDs: 64 (`--pids-limit=64`)
  - Wall-clock: orchestrator-side `timeout 10s`
- **Syscall filter**: default `seccomp` profile; drop all Linux capabilities
  (`--cap-drop=ALL`), `--security-opt no-new-privileges`.
- **GPU**: expose a single GPU via `--gpus device=N`; GPU device reset after TDR.
- **Separation**: compilation and execution use the **same** container image but
  different invocations; the compiled binary never touches the host filesystem.

For local fast-iteration on a developer workstation, the same container is used with a
looser timeout; the docs no longer suggest running untrusted compiled binaries directly
on the host.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Python Orchestrator                      │
└────┬─────────────────┬───────────────────┬──────────────────┘
     │                 │                   │
     │ 1. Generate    │ 2. Inject         │ 3. Compile
     │    C++ code     │    loop guards    │    (platform-aware)
     │                 │                   │
     ▼                 ▼                   ▼
┌─────────┐      ┌──────────┐       ┌──────────────┐
│ LLM API │─────▶│ AST Parse│──────▶│ clang++/nvc++│
└─────────┘      └──────────┘       └──────┬───────┘
                                            │ 4. Link
                                            ▼
                                    ┌───────────────┐
                                    │  swarmevolve  │
                                    │   (binary)    │
                                    └───────┬───────┘
                                            │ 5. Execute
                                            ▼
                                    ┌───────────────┐
                                    │  Game Loop    │
                                    │  (GPU/CPU)    │
                                    └───────┬───────┘
                                            │ 6. Trace
                                            ▼
                                    ┌───────────────┐
                                    │  trace.jsonl  │
                                    └───────┬───────┘
                                            │ 7. Visualize
                                            ▼
                                    ┌───────────────┐
                                    │  output.mp4   │
                                    └───────────────┘
```

## Performance Considerations

### GPU Memory Model
- **Managed Memory**: `-gpu=managed` allows automatic CPU↔GPU transfers
- **Data Transfers**: Engine allocates all arrays, GPU copies on first access
- **Coherency**: No mid-kernel synchronization, all data consistent per phase

### Optimization Strategies
1. **Minimize Branching**: Avoid divergent code paths in AI logic
2. **Coalesced Access**: Access arrays in contiguous patterns
3. **Register Pressure**: Keep `my_memory[16]` and local vars minimal
4. **Occupancy**: Maximize threads per block (engine configures automatically)

### Profiling Tools
```bash
# NVIDIA profiler
nsys profile ./swarmevolve
ncu --set full --target-processes all ./swarmevolve

# CPU profiling (macOS)
instruments -t "Time Profiler" ./swarmevolve
```

## Future Architecture Extensions

### Planned Enhancements
1. **Distributed Simulation**: MPI across multiple GPU nodes
2. **Incremental Compilation**: Cache unchanged AI modules
3. **Live Code Reload**: Hot-swap AI without restarting engine
4. **Hardware Abstraction**: Support AMD ROCm, Intel oneAPI

### Research Directions
1. **Learned Optimizations**: LLMs optimize their own GPU code
2. **Co-Evolution**: Multiple teams evolve simultaneously
3. **Transfer Learning**: Pre-train on simpler environments
