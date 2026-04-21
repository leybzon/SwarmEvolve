# SwarmEvolve Technical Specification

This document provides the complete technical specification for SwarmEvolve, including data models, game rules, API contracts, and implementation requirements.

## 1. Data Models

All data structures must be Plain Old Data (POD) types compatible with GPU device memory. No dynamic allocation is permitted.

### 1.1 Constants

```cpp
// src/types.h
constexpr int MSG_SIZE = 4;    // Number of floats per inter-drone message
constexpr int MEM_SIZE = 16;   // Number of floats in persistent drone memory
constexpr int MAX_DRONES = 50; // Maximum drones per team (compile-time limit)
```

### 1.2 Vector2D

```cpp
struct Vector2D {
    float x;
    float y;
};
```

**Usage**: Positions, velocities, directional vectors

**Coordinate System**:
- Origin (0, 0) is top-left corner
- X-axis increases rightward
- Y-axis increases downward

### 1.3 GameParams

```cpp
struct GameParams {
    float arena_width;      // Arena width in units (e.g., 1000.0)
    float arena_height;     // Arena height in units (e.g., 1000.0)
    float max_velocity;     // Maximum movement speed per tick (e.g., 5.0)
    float disable_range;    // Attack range in units (e.g., 50.0)
    int max_cooldown;       // Ticks to wait after attacking (e.g., 10)
    int num_drones;         // Actual number of drones per team
    int max_ticks;          // Maximum simulation duration (e.g., 1000)
    int current_tick;       // Current tick number (0-indexed)
};
```

**Immutability**: All fields are constant during a match except `current_tick`.

**Typical Values**:
```cpp
GameParams default_params = {
    .arena_width = 1000.0f,
    .arena_height = 1000.0f,
    .max_velocity = 5.0f,
    .disable_range = 50.0f,
    .max_cooldown = 10,
    .num_drones = 10,
    .max_ticks = 1000,
    .current_tick = 0
};
```

### 1.4 AllyState

```cpp
struct AllyState {
    int id;              // Drone ID (0 to num_drones-1)
    Vector2D pos;        // Current position
    int cooldown;        // Remaining cooldown ticks (0 = can attack)
    bool alive;          // Alive status
};
```

**Information Symmetry**: Full visibility of teammate state, including cooldowns.

**Array Layout**: Engine passes `const AllyState allies[MAX_DRONES]` where `allies[my_id]` contains own state.

### 1.5 EnemyState

```cpp
struct EnemyState {
    int id;              // Enemy drone ID (0 to num_drones-1)
    Vector2D pos;        // Current position
    bool alive;          // Alive status
    // CRITICAL: Cooldown is intentionally hidden
};
```

**Information Asymmetry**: Drones can see enemy positions and alive status but **not** enemy cooldowns. This creates strategic depth—AI must infer attack patterns.

**Rationale**: Prevents perfect counter-strategies; encourages predictive modeling.

### 1.6 Action

```cpp
struct Action {
    Vector2D velocity;           // Desired movement vector
    int target_id;               // Enemy ID to attack, or -1 to hold fire
    float message_out[MSG_SIZE]; // Communication payload for next tick
};
```

**Output Contract**: AI must populate this structure every tick.

**Field Details**:

- `velocity`: Desired movement direction and magnitude
  - Will be clamped to `max_velocity` by engine
  - Does not accumulate (velocity is replaced, not added)

- `target_id`:
  - Range: `-1` or `[0, num_drones-1]`
  - `-1` means "do not attack this tick"
  - Invalid IDs (dead enemies, out of range) are silently ignored

- `message_out`:
  - Arbitrary float protocol defined by AI
  - Delivered to all teammates on **next tick** via `incoming_messages`
  - Typical uses: coordinate attacks, share enemy tracking, communicate intent

## 2. API Contract

### 2.1 Function Signature

```cpp
namespace TeamA {
    #pragma acc routine seq
    void drone_ai(
        int my_id,
        const GameParams* params,
        const AllyState* allies,
        const EnemyState* enemies,
        const float incoming_messages[][MSG_SIZE],
        float* my_memory,
        Action* out_action
    );
}
```

**Parameters**:

1. `int my_id`
   - This drone's ID within the team
   - Range: `[0, params->num_drones-1]`
   - Use to index `allies[my_id]` for own state

2. `const GameParams* params`
   - Pointer to global game configuration
   - Read-only access
   - Valid for entire match duration

3. `const AllyState* allies`
   - Array of size `params->num_drones`
   - Contains full state of all teammates (including self)
   - Read-only access

4. `const EnemyState* enemies`
   - Array of size `params->num_drones`
   - Contains partial state of all enemies (no cooldowns)
   - Read-only access

5. `const float incoming_messages[][MSG_SIZE]`
   - 2D array: `[num_drones][MSG_SIZE]`
   - `incoming_messages[ally_id]` contains message from `ally_id` on previous tick
   - First tick: all zeros
   - Dead drones: messages frozen at last sent value

6. `float* my_memory`
   - Array of `MEM_SIZE` floats
   - Persistent across all ticks for this drone
   - Initialized to zeros at match start
   - Can store internal state (e.g., enemy tracking, cooldown estimates)

7. `Action* out_action`
   - Output parameter: must be populated before returning
   - Uninitialized on entry (no default values)

### 2.2 Execution Constraints

**Required Pragma**: `#pragma acc routine seq`
- Marks function for GPU device execution
- Ignored by clang++, required for nvc++

**Namespace Isolation**:
- Team A must use `namespace TeamA { ... }`
- Team B must use `namespace TeamB { ... }`
- Prevents linker symbol collisions

**Forbidden Operations**:
- No `new`, `malloc`, `free`, `delete`
- No `std::vector`, `std::string`, `std::map`
- No file I/O, network I/O, system calls
- No threading, mutexes, atomics
- No recursion exceeding 10 levels
- No unbounded loops (enforced by Python injection)

**Allowed Operations**:
- `<cmath>` functions: `sqrt`, `sin`, `cos`, `atan2`, `abs`, `pow`, `fmod`
- Fixed-size arrays on stack (limited to ~1KB)
- Basic arithmetic, conditionals, bounded loops
- Helper functions within the same namespace

### 2.3 Example Implementation

```cpp
namespace TeamA {
    #pragma acc routine seq
    void drone_ai(
        int my_id,
        const GameParams* params,
        const AllyState* allies,
        const EnemyState* enemies,
        const float incoming_messages[][MSG_SIZE],
        float* my_memory,
        Action* out_action
    ) {
        // Get own state
        Vector2D my_pos = allies[my_id].pos;
        int my_cooldown = allies[my_id].cooldown;

        // Find nearest alive enemy
        int nearest_enemy = -1;
        float min_dist = 1e9f;

        for (int i = 0; i < params->num_drones; i++) {
            if (!enemies[i].alive) continue;

            float dx = enemies[i].pos.x - my_pos.x;
            float dy = enemies[i].pos.y - my_pos.y;
            float dist = sqrt(dx*dx + dy*dy);

            if (dist < min_dist) {
                min_dist = dist;
                nearest_enemy = i;
            }
        }

        // Move toward nearest enemy
        if (nearest_enemy != -1) {
            float dx = enemies[nearest_enemy].pos.x - my_pos.x;
            float dy = enemies[nearest_enemy].pos.y - my_pos.y;
            float dist = sqrt(dx*dx + dy*dy);

            out_action->velocity.x = (dx / dist) * params->max_velocity;
            out_action->velocity.y = (dy / dist) * params->max_velocity;

            // Attack if in range and cooldown is zero
            if (dist <= params->disable_range && my_cooldown == 0) {
                out_action->target_id = nearest_enemy;
            } else {
                out_action->target_id = -1;
            }
        } else {
            // No enemies left, stop moving
            out_action->velocity.x = 0.0f;
            out_action->velocity.y = 0.0f;
            out_action->target_id = -1;
        }

        // Broadcast position to team
        out_action->message_out[0] = my_pos.x;
        out_action->message_out[1] = my_pos.y;
        out_action->message_out[2] = (float)nearest_enemy;
        out_action->message_out[3] = min_dist;
    }
}
```

## 3. Game Loop Specification

The engine executes a deterministic, synchronous game loop with four distinct phases per tick.

### 3.1 Initialization

```cpp
// Set initial positions (e.g., random or grid-based)
for (int i = 0; i < num_drones; i++) {
    team_a_drones[i] = {
        .id = i,
        .pos = {random(0, arena_width), random(0, arena_height)},
        .cooldown = 0,
        .alive = true
    };
    team_b_drones[i] = { /* similar */ };
}

// Zero-initialize memories and messages
memset(team_a_memory, 0, sizeof(team_a_memory));
memset(team_b_memory, 0, sizeof(team_b_memory));
memset(team_a_messages, 0, sizeof(team_a_messages));
memset(team_b_messages, 0, sizeof(team_b_messages));
```

### 3.2 Phase 1: Query (Parallel, Read-Only)

**Purpose**: Collect actions from all alive drones without mutating state.

```cpp
#pragma acc parallel loop present(team_a_drones, team_a_actions)
for (int i = 0; i < num_drones; i++) {
    if (team_a_drones[i].alive) {
        TeamA::drone_ai(
            i,
            &params,
            team_a_drones,      // allies
            team_b_drones,      // enemies (const, cooldowns hidden)
            team_a_messages,
            team_a_memory[i],
            &team_a_actions[i]
        );
    }
}

// Repeat for team_b_drones calling TeamB::drone_ai
```

**Guarantees**:
- All drones see **identical** state snapshot
- No state mutations during this phase
- GPU threads execute in parallel (order-independent)

### 3.3 Phase 2: Movement

**Purpose**: Update drone positions based on actions.

```cpp
for (int i = 0; i < num_drones; i++) {
    if (!drones[i].alive) continue;

    Vector2D v = actions[i].velocity;

    // Clamp velocity to max_velocity
    float speed = sqrt(v.x * v.x + v.y * v.y);
    if (speed > params.max_velocity) {
        v.x = (v.x / speed) * params.max_velocity;
        v.y = (v.y / speed) * params.max_velocity;
    }

    // Update position
    drones[i].pos.x += v.x;
    drones[i].pos.y += v.y;

    // Clamp to arena boundaries
    if (drones[i].pos.x < 0.0f) drones[i].pos.x = 0.0f;
    if (drones[i].pos.x > params.arena_width) drones[i].pos.x = params.arena_width;
    if (drones[i].pos.y < 0.0f) drones[i].pos.y = 0.0f;
    if (drones[i].pos.y > params.arena_height) drones[i].pos.y = params.arena_height;
}
```

**Rules**:
1. Velocity vectors are **clamped** to `max_velocity`, not accumulated
2. Positions updated instantaneously (no inertia or acceleration)
3. Drones stop at arena boundaries (no wrapping or bouncing)
4. Dead drones do not move

### 3.4 Phase 3: Combat (Synchronous)

**Purpose**: Resolve all attack actions simultaneously.

```cpp
bool pending_deaths[MAX_DRONES * 2] = {false};  // Both teams

// Process Team A attacks
for (int i = 0; i < num_drones; i++) {
    if (!team_a_drones[i].alive) continue;
    if (team_a_drones[i].cooldown > 0) continue;
    if (team_a_actions[i].target_id == -1) continue;

    int target_id = team_a_actions[i].target_id;
    if (target_id < 0 || target_id >= num_drones) continue;
    if (!team_b_drones[target_id].alive) continue;

    // Calculate distance using post-movement positions
    float dx = team_a_drones[i].pos.x - team_b_drones[target_id].pos.x;
    float dy = team_a_drones[i].pos.y - team_b_drones[target_id].pos.y;
    float dist = sqrt(dx*dx + dy*dy);

    if (dist <= params.disable_range) {
        pending_deaths[num_drones + target_id] = true;  // Mark Team B drone
        team_a_drones[i].cooldown = params.max_cooldown;  // Attacker pays cooldown
    }
}

// Repeat for Team B attacks on Team A
// ...
```

**Rules**:

1. **Distance Check**: Uses Euclidean distance after movement phase
2. **Range Requirement**: `dist <= disable_range`
3. **Cooldown Requirement**: Attacker must have `cooldown == 0`
4. **Synchronous Death**: Deaths recorded in buffer, applied in Cleanup phase
5. **Mutual Destruction**: If A attacks B and B attacks A, both can die if in range
6. **Cooldown Penalty**: Attacker pays `max_cooldown` regardless of success
   - Even if target is already dead or out of range, cooldown is consumed (if valid target_id)
7. **Focus-Fire**: Multiple attackers on same target:
   - Target dies once
   - All attackers pay full cooldown
   - No bonus/penalty for coordination

**Anti-Patterns**:
- Targeting self: Ignored (cannot attack own team)
- Targeting dead enemies: Cooldown **not** consumed (check happens before cooldown penalty)
- Out of range: Cooldown **not** consumed

### 3.5 Phase 4: Cleanup

**Purpose**: Apply state changes and prepare for next tick.

```cpp
// Apply deaths
for (int i = 0; i < num_drones; i++) {
    if (pending_deaths[i]) team_a_drones[i].alive = false;
    if (pending_deaths[num_drones + i]) team_b_drones[i].alive = false;
}

// Decrement cooldowns
for (int i = 0; i < num_drones; i++) {
    if (team_a_drones[i].alive && team_a_drones[i].cooldown > 0) {
        team_a_drones[i].cooldown--;
    }
    if (team_b_drones[i].alive && team_b_drones[i].cooldown > 0) {
        team_b_drones[i].cooldown--;
    }
}

// Route messages for next tick
for (int i = 0; i < num_drones; i++) {
    if (team_a_drones[i].alive) {
        for (int j = 0; j < MSG_SIZE; j++) {
            team_a_messages[i][j] = team_a_actions[i].message_out[j];
        }
    }
    // Dead drones: messages remain unchanged (frozen)
}

// Increment tick counter
params.current_tick++;
```

### 3.6 Termination Conditions

The simulation ends when any condition is met:

1. **Team A Eliminated**: All Team A drones are dead
2. **Team B Eliminated**: All Team B drones are dead
3. **Mutual Elimination**: Both teams dead simultaneously (draw)
4. **Tick Limit**: `current_tick >= max_ticks` (draw)

**Outcome Calculation**:
```cpp
enum Outcome { TEAM_A_WIN, TEAM_B_WIN, DRAW };

Outcome determine_winner() {
    int a_alive = count_alive(team_a_drones);
    int b_alive = count_alive(team_b_drones);

    if (a_alive > 0 && b_alive == 0) return TEAM_A_WIN;
    if (b_alive > 0 && a_alive == 0) return TEAM_B_WIN;
    if (a_alive == 0 && b_alive == 0) return DRAW;

    // Tick limit reached
    if (a_alive > b_alive) return TEAM_A_WIN;
    if (b_alive > a_alive) return TEAM_B_WIN;
    return DRAW;
}
```

## 4. Trace Format Specification

### 4.1 JSON Lines (.jsonl)

Each line is a valid JSON object representing one tick:

```json
{"tick":0,"team_a":[{"id":0,"x":123.4,"y":567.8,"cooldown":0,"alive":true},...],"team_b":[...]}
{"tick":1,"team_a":[{"id":0,"x":128.4,"y":572.8,"cooldown":0,"alive":true},...],"team_b":[...]}
...
{"tick":142,"team_a":[{"id":0,"x":450.2,"y":300.1,"cooldown":5,"alive":false},...],"team_b":[...],"outcome":"TEAM_B_WIN"}
```

**Fields**:
- `tick`: Current tick number (0-indexed)
- `team_a`, `team_b`: Arrays of drone states
  - `id`: Drone ID
  - `x`, `y`: Position coordinates
  - `cooldown`: Remaining cooldown ticks
  - `alive`: Boolean alive status
- `outcome`: (Final tick only) "TEAM_A_WIN", "TEAM_B_WIN", or "DRAW"

### 4.2 Generation

```cpp
void write_trace_line(FILE* f, int tick, const AllyState* a, const AllyState* b, int n) {
    fprintf(f, "{\"tick\":%d,\"team_a\":[", tick);
    for (int i = 0; i < n; i++) {
        if (i > 0) fprintf(f, ",");
        fprintf(f, "{\"id\":%d,\"x\":%.2f,\"y\":%.2f,\"cooldown\":%d,\"alive\":%s}",
                a[i].id, a[i].pos.x, a[i].pos.y, a[i].cooldown, a[i].alive ? "true" : "false");
    }
    fprintf(f, "],\"team_b\":[");
    // Repeat for team_b
    fprintf(f, "]}\n");
}
```

## 5. Compilation Specification

### 5.1 macOS (Development)

```bash
clang++ \
    -std=c++17 \
    -O3 \
    -o swarmevolve \
    src/engine.cpp \
    src/a/team_a_ai.cpp \
    src/b/team_b_ai.cpp
```

**Flags**:
- `-std=c++17`: Modern C++ features
- `-O3`: Maximum optimization
- OpenACC pragmas are ignored (no GPU execution)

### 5.2 Linux (Production)

```bash
nvc++ \
    -std=c++17 \
    -O3 \
    -acc=gpu \
    -gpu=managed \
    -Minfo=accel \
    -o swarmevolve \
    src/engine.cpp \
    src/a/team_a_ai.cpp \
    src/b/team_b_ai.cpp
```

**Flags**:
- `-acc=gpu`: Enable OpenACC GPU acceleration
- `-gpu=managed`: Use CUDA Unified Memory (automatic transfers)
- `-Minfo=accel`: Print acceleration diagnostics

### 5.3 Safety Injection (Python Pre-Processing)

Before compilation, the orchestrator transforms AI code:

**Input** (`team_a_ai.cpp`):
```cpp
void drone_ai(...) {
    while (searching) {
        // AI logic
    }

    for (int i = 0; i < num_drones; i++) {
        // More logic
    }
}
```

**Output** (temporary file):
```cpp
void drone_ai(...) {
    int _guard_0 = 0;
    while (searching) {
        if (++_guard_0 > 1000) break;
        // AI logic
    }

    int _guard_1 = 0;
    for (int i = 0; i < num_drones; i++) {
        if (++_guard_1 > 1000) break;
        // More logic
    }
}
```

**Algorithm**:
1. Parse C++ using regex or `libclang` AST
2. Identify all loop constructs
3. Inject unique guard variable with 1000-iteration limit
4. Write transformed code to temporary file
5. Compile temporary file instead of original

## 6. Fitness Evaluation

### 6.1 Match Outcome Scoring

```python
def score_match(outcome: str, ticks: int, a_alive: int, b_alive: int) -> float:
    """
    Returns score from Team A's perspective.
    Positive = Team A advantage, Negative = Team B advantage
    """
    if outcome == "TEAM_A_WIN":
        return 1.0 + (a_alive / num_drones) * 0.5  # Bonus for survivors
    elif outcome == "TEAM_B_WIN":
        return -1.0 - (b_alive / num_drones) * 0.5  # Penalty for enemy survivors
    else:  # DRAW
        return (a_alive - b_alive) / num_drones  # Proportional to advantage
```

### 6.2 Multi-Match Aggregation

```python
def evaluate_fitness(team_a_code: str, num_matches: int = 100) -> dict:
    scores = []
    for i in range(num_matches):
        result = run_simulation(team_a_code, baseline_team_b)
        scores.append(score_match(result))

    return {
        "mean": np.mean(scores),
        "std": np.std(scores),
        "min": np.min(scores),
        "max": np.max(scores),
        "wins": sum(s > 0 for s in scores),
        "losses": sum(s < 0 for s in scores),
        "draws": sum(s == 0 for s in scores)
    }
```

## 7. Edge Cases & Clarifications

### 7.1 Simultaneous Attacks
- If A attacks B and B attacks A, both succeed if in range
- Both drones die simultaneously
- Both attacks are counted for statistics

### 7.2 Focus Fire
- 3 drones attack same target: target dies, all 3 pay cooldown
- No efficiency bonus or penalty
- Encourages AI to coordinate targets dynamically

### 7.3 Invalid Target IDs
- `target_id < -1` or `target_id >= num_drones`: Ignored, no cooldown penalty
- `target_id == my_id`: Ignored (cannot attack self)
- `target_id` references same team: Ignored (friendly fire impossible)

### 7.4 Dead Drone State
- Position frozen at death location
- Cooldown frozen at current value
- Messages frozen at last broadcast
- `alive == false` visible to all

### 7.5 Boundary Behavior
- Drones cannot leave arena
- Position clamped to `[0, width]` and `[0, height]`
- No bouncing, wrapping, or physics

### 7.6 Floating-Point Determinism
- All operations use IEEE 754 single precision
- No random number generation
- No time-based behavior
- GPU and CPU produce identical results (within floating-point epsilon)
