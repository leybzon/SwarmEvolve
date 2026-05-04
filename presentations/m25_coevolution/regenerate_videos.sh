#!/usr/bin/env bash
# Regenerate M25 presentation videos with improved visualizer
# Uses H.264 codec, 8x slowdown, and subtle targeting arrows

set -e

M25_DIR="data/runs/m25_coevolve_100r"
VIDEOS_DIR="presentations/m25_coevolution/videos"
BASELINE="src/baselines/pursuit_v1.cpp"

echo "=== Regenerating M25 Presentation Videos ==="
echo ""

# Round 1: Baseline vs Champion
echo "📹 Round 1: Baseline (-0.8 fitness)"
./swarmevolve \
  --record /tmp/m25_r1.jsonl \
  --record-actions \
  --seed 42 \
  --max-ticks 150 \
  --drones-a 10 \
  --drones-b 10

python3 scripts/visualizer_v2.py \
  /tmp/m25_r1.jsonl \
  "${VIDEOS_DIR}/m25_r1_baseline.mp4" \
  --slowdown 8.0 \
  --codec h264 \
  --intro-text "Round 1: Baseline Team B\n66 LOC | -0.8 Fitness | Losing 8/10"

echo "✅ Round 1 done: ${VIDEOS_DIR}/m25_r1_baseline.mp4"
echo ""

# Round 13: Parity
echo "📹 Round 13: Parity (0.0 fitness)"
if [ -f "${M25_DIR}/round_0013/candidate.injected.cpp" ]; then
  cp "${M25_DIR}/round_0013/candidate.injected.cpp" src/b/team_b_ai.cpp
  cp "${M25_DIR}/round_0013/opponent_A.cpp" src/a/team_a_ai.cpp
  make clean && make -j4

  ./swarmevolve \
    --record /tmp/m25_r13.jsonl \
    --record-actions \
    --seed 42 \
    --max-ticks 150 \
    --drones-a 10 \
    --drones-b 10

  python3 scripts/visualizer_v2.py \
    /tmp/m25_r13.jsonl \
    "${VIDEOS_DIR}/m25_r13_parity.mp4" \
    --slowdown 8.0 \
    --codec h264 \
    --intro-text "Round 13: Parity\n97 LOC | 0.0 Fitness | 5/10 wins"

  echo "✅ Round 13 done: ${VIDEOS_DIR}/m25_r13_parity.mp4"
else
  echo "⚠️  Round 13 candidate not found, skipping"
fi
echo ""

# Round 31: Breakthrough
echo "📹 Round 31: Formation Spread Breakthrough (+0.9 fitness)"
if [ -f "${M25_DIR}/round_0031/candidate.injected.cpp" ]; then
  cp "${M25_DIR}/round_0031/candidate.injected.cpp" src/b/team_b_ai.cpp
  cp "${M25_DIR}/round_0031/opponent_A.cpp" src/a/team_a_ai.cpp
  make clean && make -j4

  ./swarmevolve \
    --record /tmp/m25_r31.jsonl \
    --record-actions \
    --seed 42 \
    --max-ticks 150 \
    --drones-a 10 \
    --drones-b 10

  python3 scripts/visualizer_v2.py \
    /tmp/m25_r31.jsonl \
    "${VIDEOS_DIR}/m25_r31_breakthrough.mp4" \
    --slowdown 8.0 \
    --codec h264 \
    --intro-text "Round 31: Formation Spread\n185 LOC | +0.9 Fitness | 9/10 wins"

  echo "✅ Round 31 done: ${VIDEOS_DIR}/m25_r31_breakthrough.mp4"
else
  echo "⚠️  Round 31 candidate not found, skipping"
fi
echo ""

# Round 41: Final
echo "📹 Round 41: Final State (+0.9 fitness)"
if [ -f "${M25_DIR}/round_0041/candidate.injected.cpp" ]; then
  cp "${M25_DIR}/round_0041/candidate.injected.cpp" src/b/team_b_ai.cpp
  cp "${M25_DIR}/round_0041/opponent_A.cpp" src/a/team_a_ai.cpp
  make clean && make -j4

  ./swarmevolve \
    --record /tmp/m25_r41.jsonl \
    --record-actions \
    --seed 42 \
    --max-ticks 150 \
    --drones-a 10 \
    --drones-b 10

  python3 scripts/visualizer_v2.py \
    /tmp/m25_r41.jsonl \
    "${VIDEOS_DIR}/m25_r41_final.mp4" \
    --slowdown 8.0 \
    --codec h264 \
    --intro-text "Round 41: Final State\n210 LOC | +0.9 Fitness | 9/10 wins"

  echo "✅ Round 41 done: ${VIDEOS_DIR}/m25_r41_final.mp4"
else
  echo "⚠️  Round 41 candidate not found, skipping"
fi
echo ""

echo "=== Video Regeneration Complete ==="
echo ""
echo "All videos saved to: ${VIDEOS_DIR}/"
ls -lh "${VIDEOS_DIR}/"*.mp4
echo ""
echo "✨ Videos now have:"
echo "  - H.264 codec (browser-compatible)"
echo "  - 8x slowdown (3.75 FPS playback)"
echo "  - Subtle targeting arrows (yellow, 60% length)"
echo "  - Tick counter (top-left)"
