// SPDX-License-Identifier: MIT
//
// SwarmEvolve engine entry point (M2).
//
// Responsibilities:
//   1. Parse a small, hand-rolled CLI (no external dep).
//   2. Seed initial drone positions via std::mt19937.
//   3. Run the four-phase game loop (SPECIFICATION.md §3) to termination.
//   4. Optionally write a JSON-Lines trace.
//   5. Emit the summary line on stdout and return the spec-mandated exit
//      code (0 / 1 / 2).
//
// All game state lives in file-scope static buffers (no heap). Phase logic
// is implemented in src/engine.h so tests can exercise it without main().
//
// The engine does NOT include AI .cpp files. Forward declarations live in
// src/ai_abi.h and the link step resolves `TeamA::drone_ai` /
// `TeamB::drone_ai` from src/a/*.cpp and src/b/*.cpp respectively.

#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <random>

#include "ai_abi.h"
#include "engine.h"
#include "types.h"

using namespace swarmevolve;
using swarmevolve::engine::Outcome;

// ---------------------------------------------------------------------------
// Command-line arguments.
// ---------------------------------------------------------------------------
namespace {

struct CliArgs {
    const char* record_path = nullptr;  // nullptr means "no trace"
    std::uint64_t seed      = 0;
    int drones_a            = 10;
    int drones_b            = 10;
    int max_ticks           = 1000;
    float arena_scale       = 1.0f;
    bool benchmark          = false;
    int repeats             = 1;
    bool help               = false;
};

void print_help() {
    std::fprintf(stderr,
        "swarmevolve [OPTIONS]\n"
        "\n"
        "Options:\n"
        "  --record <path>     Write JSON-Lines trace to <path>.\n"
        "  --seed <int>        Seed for initial-position PRNG (default: 0).\n"
        "  --drones-a <int>    Team A size, 1..MAX_DRONES (default: 10).\n"
        "  --drones-b <int>    Team B size, 1..MAX_DRONES (default: 10).\n"
        "  --max-ticks <int>   Simulation cap (default: 1000).\n"
        "  --arena-scale <f>   Multiplier on arena width/height (default: 1.0).\n"
        "                      M13 scaling uses sqrt(N/50) to keep density constant.\n"
        "  --benchmark         Benchmark mode: no trace, no logging, one\n"
        "                      JSON line per repeat emitted to stdout.\n"
        "  --repeats <int>     Benchmark mode: repeats per invocation (default: 1).\n"
        "  --help              Print this message.\n");
}

// Returns 0 on success, nonzero on parse error.
int parse_cli(int argc, char** argv, CliArgs* out) {
    for (int i = 1; i < argc; ++i) {
        const char* a = argv[i];
        auto need_value = [&](const char* flag) -> const char* {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "ERROR kind=cli detail=missing-value flag=%s\n", flag);
                return nullptr;
            }
            return argv[++i];
        };

        if (std::strcmp(a, "--help") == 0 || std::strcmp(a, "-h") == 0) {
            out->help = true;
        } else if (std::strcmp(a, "--record") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->record_path = v;
        } else if (std::strcmp(a, "--seed") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->seed = static_cast<std::uint64_t>(std::strtoull(v, nullptr, 10));
        } else if (std::strcmp(a, "--drones-a") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->drones_a = static_cast<int>(std::strtol(v, nullptr, 10));
        } else if (std::strcmp(a, "--drones-b") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->drones_b = static_cast<int>(std::strtol(v, nullptr, 10));
        } else if (std::strcmp(a, "--max-ticks") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->max_ticks = static_cast<int>(std::strtol(v, nullptr, 10));
        } else if (std::strcmp(a, "--arena-scale") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->arena_scale = std::strtof(v, nullptr);
        } else if (std::strcmp(a, "--benchmark") == 0) {
            out->benchmark = true;
        } else if (std::strcmp(a, "--repeats") == 0) {
            const char* v = need_value(a);
            if (!v) return 1;
            out->repeats = static_cast<int>(std::strtol(v, nullptr, 10));
        } else {
            std::fprintf(stderr, "ERROR kind=cli detail=unknown-flag flag=%s\n", a);
            return 1;
        }
    }
    if (out->drones_a < 1 || out->drones_a > MAX_DRONES ||
        out->drones_b < 1 || out->drones_b > MAX_DRONES) {
        std::fprintf(stderr,
            "ERROR kind=cli detail=drones-out-of-range a=%d b=%d max=%d\n",
            out->drones_a, out->drones_b, MAX_DRONES);
        return 1;
    }
    if (out->max_ticks < 1) {
        std::fprintf(stderr, "ERROR kind=cli detail=max-ticks-invalid value=%d\n", out->max_ticks);
        return 1;
    }
    if (out->arena_scale <= 0.0f || !std::isfinite(out->arena_scale)) {
        std::fprintf(stderr, "ERROR kind=cli detail=arena-scale-invalid value=%g\n",
                     static_cast<double>(out->arena_scale));
        return 1;
    }
    if (out->repeats < 1) {
        std::fprintf(stderr, "ERROR kind=cli detail=repeats-invalid value=%d\n", out->repeats);
        return 1;
    }
    if (out->benchmark && out->record_path != nullptr) {
        std::fprintf(stderr,
            "ERROR kind=cli detail=benchmark-excludes-record\n"
            "       --benchmark mode is side-effect-free by design; --record is forbidden.\n");
        return 1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// World state (file-scope, zero-initialized).
// ---------------------------------------------------------------------------
struct World {
    GameParams params{};
    AllyState  a[MAX_DRONES]{};
    AllyState  b[MAX_DRONES]{};
    Action     act_a[MAX_DRONES]{};
    Action     act_b[MAX_DRONES]{};
    float      msgs_a[MAX_DRONES][MSG_SIZE]{};
    float      msgs_b[MAX_DRONES][MSG_SIZE]{};
    float      mem_a[MAX_DRONES][MEM_SIZE]{};
    float      mem_b[MAX_DRONES][MEM_SIZE]{};
    // Per-tick scratch enemy views (one per team, projected from the
    // opposing AllyState). Kept in the World so managed memory /
    // present-data clauses see a single stable host pointer rather than
    // a fresh stack location each tick.
    EnemyState enemies_for_a[MAX_DRONES]{};
    EnemyState enemies_for_b[MAX_DRONES]{};
    // Combat-phase scratch (cooldowns + pending deaths). Likewise keeping
    // these in the World avoids stack-allocated arrays inside the tick loop,
    // which are unfriendly to OpenACC managed memory on nvc++.
    int        new_cd_a[MAX_DRONES]{};
    int        new_cd_b[MAX_DRONES]{};
    bool       deaths_a[MAX_DRONES]{};
    bool       deaths_b[MAX_DRONES]{};
};

// World is heap-allocated in main() so that large-MAX_DRONES builds
// (e.g. MAX_DRONES=100000 for the M13 scaling study) do not overflow
// the static-data segment or the default main-thread stack. The
// allocation is once per process; never inside the tick loop.

void init_world(World& w, const CliArgs& cli) {
    // Fill GameParams with spec defaults (SPECIFICATION.md §1.3 "Typical Values").
    w.params.arena_width   = 1000.0f * cli.arena_scale;
    w.params.arena_height  = 1000.0f * cli.arena_scale;
    w.params.max_velocity  = 5.0f;
    w.params.disable_range = 50.0f;
    w.params.max_cooldown  = 10;
    w.params.num_drones_a  = cli.drones_a;
    w.params.num_drones_b  = cli.drones_b;
    w.params.max_ticks     = cli.max_ticks;
    w.params.current_tick  = 0;

    std::mt19937_64 rng(cli.seed);
    // Use uniform_real_distribution for positions. Both the PRNG and the
    // distribution are part of the C++ standard but are NOT guaranteed to be
    // byte-identical across vendor libstdc++ / libc++ implementations in
    // corner cases — per-platform determinism is the documented guarantee
    // (SPECIFICATION §7.6).
    std::uniform_real_distribution<float> ux(0.0f, w.params.arena_width);
    std::uniform_real_distribution<float> uy(0.0f, w.params.arena_height);

    for (int i = 0; i < cli.drones_a; ++i) {
        w.a[i].id       = i;
        w.a[i].pos.x    = ux(rng);
        w.a[i].pos.y    = uy(rng);
        w.a[i].cooldown = 0;
        w.a[i].alive    = true;
    }
    for (int i = 0; i < cli.drones_b; ++i) {
        w.b[i].id       = i;
        w.b[i].pos.x    = ux(rng);
        w.b[i].pos.y    = uy(rng);
        w.b[i].cooldown = 0;
        w.b[i].alive    = true;
    }
    // Messages and memory are already zeroed by aggregate init.
}

// ---------------------------------------------------------------------------
// Trace writer — emits one JSON line per tick. The `outcome` field is added
// only to the final line (call write_trace_line with out_outcome != nullptr).
// Uses fprintf with "%.2f" per SPECIFICATION §4.2 example.
// ---------------------------------------------------------------------------
void write_team_array(std::FILE* f, const AllyState* drones, int n) {
    std::fputc('[', f);
    for (int i = 0; i < n; ++i) {
        if (i > 0) std::fputc(',', f);
        std::fprintf(f,
            "{\"id\":%d,\"x\":%.2f,\"y\":%.2f,\"cooldown\":%d,\"alive\":%s}",
            drones[i].id, drones[i].pos.x, drones[i].pos.y,
            drones[i].cooldown, drones[i].alive ? "true" : "false");
    }
    std::fputc(']', f);
}

void write_trace_line(std::FILE* f, int tick, const World& w, const char* outcome_tag_or_null) {
    std::fprintf(f, "{\"tick\":%d,\"team_a\":", tick);
    write_team_array(f, w.a, w.params.num_drones_a);
    std::fprintf(f, ",\"team_b\":");
    write_team_array(f, w.b, w.params.num_drones_b);
    if (outcome_tag_or_null) {
        std::fprintf(f, ",\"outcome\":\"%s\"", outcome_tag_or_null);
    }
    std::fprintf(f, "}\n");
}

// ---------------------------------------------------------------------------
// Query phase: call AI for each alive drone of one side.
//
// nvc++ cannot generate device code through a plain function pointer (no
// indirect calls on GPU without function tables). We therefore wrap each
// team's drone_ai in a tiny functor whose operator() is `acc routine seq`
// and forwards to the real AI. A template parameter lets OpenACC see the
// concrete callable type at the call site, so the kernel body inlines the
// functor and the inlined functor calls the fixed, acc-routine-seq AI.
// ---------------------------------------------------------------------------

struct TeamAAiCallable {
    #pragma acc routine seq
    void operator()(int my_id, const GameParams* p, const AllyState* a,
                    const EnemyState* e, const float (*msgs)[MSG_SIZE],
                    float* mem, Action* out) const {
        TeamA::drone_ai(my_id, p, a, e, msgs, mem, out);
    }
};

struct TeamBAiCallable {
    #pragma acc routine seq
    void operator()(int my_id, const GameParams* p, const AllyState* a,
                    const EnemyState* e, const float (*msgs)[MSG_SIZE],
                    float* mem, Action* out) const {
        TeamB::drone_ai(my_id, p, a, e, msgs, mem, out);
    }
};

// View helpers to convert AllyState array into an EnemyState array visible to
// the opposing AI. We pack into a fixed-size scratch buffer rather than
// aliasing (different layout). This runs once per tick per team and is
// cheap (n <= MAX_DRONES).
void project_enemies(const AllyState* src, EnemyState* dst, int n) {
    #pragma acc parallel loop copyin(src[0:n]) copyout(dst[0:n])
    for (int i = 0; i < n; ++i) {
        dst[i].id    = src[i].id;
        dst[i].pos   = src[i].pos;
        dst[i].alive = src[i].alive;
    }
}

template <typename AiCallable>
void query_phase(AiCallable fn, int my_n, int their_n,
                 const GameParams* params,
                 const AllyState* my_allies,
                 const EnemyState* enemy_view,
                 const float msgs[][MSG_SIZE],
                 float memory[][MEM_SIZE],
                 Action* out_actions) {
    // `their_n` is used only inside the `#pragma acc` data clause for the
    // enemy_view copyin bound. Non-OpenACC compilers skip the pragma, so we
    // touch `their_n` explicitly to silence -Werror=unused-parameter.
    (void)their_n;
    // Determinism note: each iteration writes only to out_actions[i] and
    // memory[i] (caller-disjoint per-drone slots), and reads shared state
    // read-only. There are no reductions, so thread ordering is
    // irrelevant — the result is bit-identical to the serial loop.
    // The OpenMP pragma is guarded so the GPU (OpenACC) build does not
    // accidentally fork a host thread team on top of the device launch.
    #pragma acc parallel loop                                                  \
        copyin(my_allies[0:my_n], enemy_view[0:their_n],                       \
               msgs[0:my_n][0:MSG_SIZE])                                       \
        copy(memory[0:my_n][0:MEM_SIZE])                                       \
        copyout(out_actions[0:my_n])
    #if defined(_OPENMP) && !defined(_OPENACC)
    #pragma omp parallel for schedule(static)
    #endif
    for (int i = 0; i < my_n; ++i) {
        if (!my_allies[i].alive) {
            // Dead drones skip AI and emit a zeroed action. This also
            // prevents a stale action from last tick's alive state from
            // accidentally routing a message.
            out_actions[i] = Action{};
            out_actions[i].target_id = -1;
            continue;
        }
        fn(i, params, my_allies, enemy_view, msgs, memory[i], &out_actions[i]);
    }
}

// Run a single match. Trace may be null. Returns the final outcome and the
// number of ticks executed (<= max_ticks) via out-parameters.
// This is extracted from main() so --benchmark mode can call it in a loop
// without going through main() twice.
Outcome run_match(World& w, std::FILE* trace, int* out_ticks_executed) {
    // Emit the pre-movement tick (tick 0) so the trace always includes the
    // spawn positions. This matches SPECIFICATION §4.1's example where the
    // first line has tick==0.
    if (trace) write_trace_line(trace, 0, w, nullptr);

    Outcome outcome = swarmevolve::engine::OUTCOME_DRAW;
    bool done = false;
    int t = 0;

    // Main loop. `current_tick` semantics: it is the tick index that the
    // upcoming Query phase is computing. The first Query/Movement/Combat
    // cycle produces the state for tick 1, written to the trace as
    // "tick": 1. Iteration count is bounded by max_ticks.
    for (t = 1; t <= w.params.max_ticks; ++t) {
        w.params.current_tick = t;

        // Phase 1: Query (both teams).
        project_enemies(w.b, w.enemies_for_a, w.params.num_drones_b);
        project_enemies(w.a, w.enemies_for_b, w.params.num_drones_a);

        query_phase(TeamAAiCallable{}, w.params.num_drones_a, w.params.num_drones_b,
                    &w.params, w.a, w.enemies_for_a,
                    w.msgs_a, w.mem_a, w.act_a);
        query_phase(TeamBAiCallable{}, w.params.num_drones_b, w.params.num_drones_a,
                    &w.params, w.b, w.enemies_for_b,
                    w.msgs_b, w.mem_b, w.act_b);

        // Phase 2: Movement.
        swarmevolve::engine::movement_phase(w.a, w.act_a, w.params.num_drones_a, w.params);
        swarmevolve::engine::movement_phase(w.b, w.act_b, w.params.num_drones_b, w.params);

        // Phase 3: Combat.
        std::memset(w.new_cd_a, 0, sizeof(int) * w.params.num_drones_a);
        std::memset(w.new_cd_b, 0, sizeof(int) * w.params.num_drones_b);
        std::memset(w.deaths_a, 0, sizeof(bool) * w.params.num_drones_a);
        std::memset(w.deaths_b, 0, sizeof(bool) * w.params.num_drones_b);
        swarmevolve::engine::combat_phase_one_side(
            w.a, w.act_a, w.params.num_drones_a,
            w.b, w.params.num_drones_b,
            w.params, w.new_cd_a, w.deaths_b);
        swarmevolve::engine::combat_phase_one_side(
            w.b, w.act_b, w.params.num_drones_b,
            w.a, w.params.num_drones_a,
            w.params, w.new_cd_b, w.deaths_a);

        // Phase 4: Cleanup.
        swarmevolve::engine::apply_cooldowns(w.a, w.new_cd_a, w.params.num_drones_a);
        swarmevolve::engine::apply_cooldowns(w.b, w.new_cd_b, w.params.num_drones_b);
        swarmevolve::engine::apply_deaths(w.a, w.deaths_a, w.params.num_drones_a);
        swarmevolve::engine::apply_deaths(w.b, w.deaths_b, w.params.num_drones_b);
        swarmevolve::engine::decrement_cooldowns(w.a, w.params.num_drones_a);
        swarmevolve::engine::decrement_cooldowns(w.b, w.params.num_drones_b);
        swarmevolve::engine::route_messages(w.a, w.act_a, w.msgs_a, w.params.num_drones_a);
        swarmevolve::engine::route_messages(w.b, w.act_b, w.msgs_b, w.params.num_drones_b);

        // Termination check.
        if (swarmevolve::engine::check_early_termination(
                w.a, w.params.num_drones_a,
                w.b, w.params.num_drones_b, &outcome)) {
            if (trace) {
                write_trace_line(trace, t, w, swarmevolve::engine::outcome_tag(outcome));
            }
            done = true;
            break;
        }

        // Last-iteration line with outcome tag inline.
        const bool is_last_iter = (t == w.params.max_ticks);
        if (trace) {
            if (is_last_iter) {
                const Outcome tick_limit_outcome = swarmevolve::engine::final_outcome(
                    w.a, w.params.num_drones_a, w.b, w.params.num_drones_b);
                write_trace_line(trace, t, w, swarmevolve::engine::outcome_tag(tick_limit_outcome));
            } else {
                write_trace_line(trace, t, w, nullptr);
            }
        }
    }

    if (!done) {
        outcome = swarmevolve::engine::final_outcome(
            w.a, w.params.num_drones_a, w.b, w.params.num_drones_b);
    }
    if (out_ticks_executed) *out_ticks_executed = std::min(t, w.params.max_ticks);
    return outcome;
}

// Pick a compile-time backend tag for the benchmark JSON output so the
// driver script can sanity-check which build it invoked.
constexpr const char* bench_backend_tag() {
#if defined(_OPENACC)
    return "gpu_openacc";
#elif defined(_OPENMP)
    return "cpu_omp";
#else
    return "cpu1";
#endif
}

}  // namespace

// ---------------------------------------------------------------------------
// Main.
// ---------------------------------------------------------------------------
int main(int argc, char** argv) {
    CliArgs cli{};
    if (parse_cli(argc, argv, &cli) != 0) return 10;
    if (cli.help) {
        print_help();
        return 0;
    }

    // Heap-allocate World once. At MAX_DRONES=100000 this is ~60 MB of
    // zero-initialized scratch; fine on the heap, fatal on the stack.
    auto w_owner = std::make_unique<World>();
    World& w = *w_owner;

    // -- Benchmark path: no trace, no logging, one JSON line per repeat. --
    if (cli.benchmark) {
        for (int r = 0; r < cli.repeats; ++r) {
            // Re-seed each repeat from a seed offset so we average over
            // different initial configurations, not the same one R times.
            // Each repeat resets world state from scratch.
            CliArgs repeat_cli = cli;
            repeat_cli.seed    = cli.seed + static_cast<std::uint64_t>(r);
            init_world(w, repeat_cli);

            const auto t0 = std::chrono::steady_clock::now();
            int ticks_executed = 0;
            Outcome outcome = run_match(w, /*trace=*/nullptr, &ticks_executed);
            const auto t1 = std::chrono::steady_clock::now();

            const double wall_ms =
                std::chrono::duration<double, std::milli>(t1 - t0).count();
            const double per_tick_us = (ticks_executed > 0)
                ? (wall_ms * 1000.0 / static_cast<double>(ticks_executed))
                : 0.0;

            // Single JSON line per repeat. Keep fields lexicographically
            // ordered so the driver can parse by name without peeking at
            // order. `outcome_code` is the int so downstream can filter by
            // win/loss/draw if interesting; `outcome_tag` is the string.
            std::printf(
                "{\"backend\":\"%s\",\"repeat\":%d,\"seed\":%llu,"
                "\"n_a\":%d,\"n_b\":%d,\"ticks_requested\":%d,"
                "\"ticks_executed\":%d,\"arena_scale\":%.6f,"
                "\"wall_ms\":%.6f,\"per_tick_us\":%.6f,"
                "\"outcome_code\":%d,\"outcome_tag\":\"%s\"}\n",
                bench_backend_tag(), r,
                static_cast<unsigned long long>(repeat_cli.seed),
                w.params.num_drones_a, w.params.num_drones_b,
                w.params.max_ticks, ticks_executed,
                static_cast<double>(cli.arena_scale),
                wall_ms, per_tick_us,
                static_cast<int>(outcome),
                swarmevolve::engine::outcome_tag(outcome));
            std::fflush(stdout);
        }
        return 0;
    }

    // -- Normal (spec) path: optional trace, summary line on stdout. --
    init_world(w, cli);

    std::FILE* trace = nullptr;
    if (cli.record_path) {
        trace = std::fopen(cli.record_path, "wb");
        if (!trace) {
            std::fprintf(stderr, "ERROR kind=io detail=fopen path=%s\n", cli.record_path);
            return 11;
        }
    }

    int ticks_executed = 0;
    Outcome outcome = run_match(w, trace, &ticks_executed);

    if (trace) std::fclose(trace);

    const int a_alive = swarmevolve::engine::count_alive(w.a, w.params.num_drones_a);
    const int b_alive = swarmevolve::engine::count_alive(w.b, w.params.num_drones_b);
    std::printf("outcome=%s a_alive=%d b_alive=%d ticks=%d\n",
                swarmevolve::engine::outcome_tag(outcome),
                a_alive, b_alive, w.params.current_tick);

    return static_cast<int>(outcome);
}
