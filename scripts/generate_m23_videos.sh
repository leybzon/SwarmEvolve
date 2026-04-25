#!/usr/bin/env bash
# Generate video visualizations for M23 experiment key generations

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

M23_DIR="data/runs/m23_sustained_50gen"
VIZ_DIR="$M23_DIR/visualizations"
OPPONENT="src/baselines/pursuit_v1.cpp"

mkdir -p "$VIZ_DIR"

echo "Generating M23 visualizations..."

# Key generations to visualize (based on journal analysis)
# Gen 0: -0.9 (initial M22 champion)
# Gen 4: -0.367 (improvement)
# Gen 6: 0.0 (breakthrough)
# Gen 10: 0.0 (sustained)

GENERATIONS=(0 1 4 6 10)

for GEN in "${GENERATIONS[@]}"; do
    GEN_DIR="$M23_DIR/gen_$(printf '%04d' $GEN)"
    CANDIDATE="$GEN_DIR/candidate.injected.cpp"
    TRACE="$VIZ_DIR/gen_$(printf '%04d' $GEN)_trace.jsonl"
    VIDEO="$VIZ_DIR/gen_$(printf '%04d' $GEN).mp4"

    if [ ! -f "$CANDIDATE" ]; then
        echo "⚠️  Skipping gen $GEN: no candidate.injected.cpp"
        continue
    fi

    echo ""
    echo "=== Generation $GEN ==="

    # Get fitness and hypothesis from journal
    FITNESS=$(jq -r "select(.generation == $GEN) | .fitness" "$M23_DIR/journal.jsonl")
    HYPOTHESIS=$(jq -r "select(.generation == $GEN) | .hypothesis_tested[:80]" "$M23_DIR/journal.jsonl")

    echo "Fitness: $FITNESS"
    echo "Hypothesis: $HYPOTHESIS"

    # Generate trace file (single match, seed 42 for reproducibility)
    echo "Generating trace..."
    ./swarmevolve \
        --team-a "$CANDIDATE" \
        --team-b "$OPPONENT" \
        --record "$TRACE" \
        --seed $((42 + GEN))

    # Create intro text
    INTRO_TEXT="M23: Sustained Improvement Experiment\nGeneration $GEN\nFitness: $FITNESS"

    # Generate video
    echo "Rendering video..."
    python3 scripts/visualizer.py \
        "$TRACE" \
        "$VIDEO" \
        --intro-text "$INTRO_TEXT" \
        -v

    echo "✅ Created: $VIDEO"
done

echo ""
echo "=== Summary ==="
ls -lh "$VIZ_DIR"/*.mp4 2>/dev/null || echo "No videos created"
echo ""
echo "Videos saved to: $VIZ_DIR"
