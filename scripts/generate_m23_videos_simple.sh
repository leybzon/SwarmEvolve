#!/usr/bin/env bash
# Generate video visualizations for M23 experiment key generations
# Simple version: manual compile and run

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

M23_DIR="data/runs/m23_sustained_50gen"
VIZ_DIR="$M23_DIR/visualizations"
OPPONENT="src/baselines/pursuit_v1.cpp"
ENGINE_SRC="src/engine.cpp"

mkdir -p "$VIZ_DIR"

echo "Generating M23 visualizations..."
echo ""

# Helper function to compile and run a match
generate_video_for_gen() {
    local GEN=$1
    local GEN_DIR="$M23_DIR/gen_$(printf '%04d' $GEN)"
    local CANDIDATE="$GEN_DIR/candidate.injected.cpp"
    local TRACE="$VIZ_DIR/gen_$(printf '%04d' $GEN)_trace.jsonl"
    local VIDEO="$VIZ_DIR/gen_$(printf '%04d' $GEN).mp4"
    local BINARY="/tmp/swarmevolve_gen${GEN}"

    if [ ! -f "$CANDIDATE" ]; then
        echo "⚠️  Skipping gen $GEN: no candidate.injected.cpp"
        return
    fi

    echo "=== Generation $GEN ==="

    # Get fitness from journal
    local FITNESS=$(jq -r "select(.generation == $GEN) | .fitness" "$M23_DIR/journal.jsonl")
    echo "Fitness: $FITNESS"

    # Compile match (Team A = candidate, Team B = opponent)
    echo "Compiling..."
    clang++ -std=c++17 -O3 \
        -DTEAM_A_SRC="\"$CANDIDATE\"" \
        -DTEAM_B_SRC="\"$OPPONENT\"" \
        -o "$BINARY" \
        "$ENGINE_SRC" \
        "$CANDIDATE" \
        "$OPPONENT" 2>&1 | grep -i "error\|warning" || true

    if [ ! -f "$BINARY" ]; then
        echo "❌ Compilation failed for gen $GEN"
        return
    fi

    # Run match with trace recording
    echo "Running match..."
    "$BINARY" --record "$TRACE" --seed $((42 + GEN))

    # Create intro text
    local INTRO_TEXT="M23: Sustained Improvement Experiment\nGeneration $GEN\nFitness: $FITNESS"

    # Generate video
    echo "Rendering video..."
    python3 scripts/visualizer.py \
        "$TRACE" \
        "$VIDEO" \
        --intro-text "$INTRO_TEXT"

    # Cleanup binary
    rm -f "$BINARY"

    echo "✅ Created: $VIDEO"
    echo ""
}

# Key generations (based on journal analysis)
# Gen 0: -0.9 (initial M22 champion)
# Gen 1: -0.733 (improvement)
# Gen 4: -0.367 (significant improvement)
# Gen 6: 0.0 (breakthrough)
# Gen 10: 0.0 (sustained)

for GEN in 0 1 4 6 10; do
    generate_video_for_gen $GEN
done

echo "=== Summary ==="
ls -lh "$VIZ_DIR"/*.mp4 2>/dev/null || echo "No videos created"
echo ""
echo "Videos saved to: $VIZ_DIR"
