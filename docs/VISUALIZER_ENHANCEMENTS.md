# Visualizer Enhancements

**Date:** 2026-04-25

---

## Summary

Enhanced `scripts/visualizer.py` to provide richer context in rendered match videos.

## New Features

### 1. Step Counter (Throughout Video)

**Before:**
- Small "tick=N" in corner

**After:**
- Prominent "Step N" display in top-left
- Larger font (0.65 size, weight 2)
- More readable during playback

### 2. Outcome Frame (End of Video)

**Before:**
- Video ended on final simulation frame
- Winner unclear from last frame

**After:**
- 2-second hold frame showing:
  - Large centered winner text: "TEAM A WINS!" / "TEAM B WINS!" / "DRAW"
  - Colored winner text (Team A = blue, Team B = red, Draw = grey)
  - Final statistics: "Final Step: N | Team A: M Team B: K"
- Automatically computed from final trace state

### 3. Optional Intro Text (Start of Video)

**Before:**
- Video started immediately with simulation

**After:**
- Optional 1-second intro frame if `--intro-text` provided
- Centered text on dark background
- Supports multi-line text (use `\n` in command line)
- Skipped entirely if not provided (backwards compatible)

## Usage Examples

### Basic (No Intro)

```bash
python3 scripts/visualizer.py trace.jsonl output.mp4
```

**Output:**
- 100 simulation frames (assuming 100-tick match)
- 60 outcome frames (2 sec at 30 fps)
- Total: 160 frames

### With Intro Text

```bash
python3 scripts/visualizer.py trace.jsonl output.mp4 \
    --intro-text "Generation 33 Champion\nvs pursuit_v1"
```

**Output:**
- 30 intro frames (1 sec at 30 fps)
- 100 simulation frames
- 60 outcome frames
- Total: 190 frames

### Multi-Line Intro

```bash
python3 scripts/visualizer.py trace.jsonl output.mp4 \
    --intro-text "M23 Experiment\nGeneration 15\nClaim+Kite Tactic v2"
```

## Technical Details

### New `RenderConfig` Field

```python
@dataclass(frozen=True)
class RenderConfig:
    # ... existing fields ...
    intro_text: str | None = None  # optional intro text shown for 1 second
```

### New Helper Functions

1. **`_draw_intro_frame(cfg: RenderConfig) -> np.ndarray`**
   - Creates centered intro text on dark background
   - Handles multi-line text (split on `\n`)
   - Only called if `cfg.intro_text` is not None

2. **`_draw_outcome_frame(final_tick, outcome, a_alive, b_alive, cfg) -> np.ndarray`**
   - Determines winner from outcome string or alive counts
   - Colors winner text appropriately
   - Shows final step number and team counts

### Updated `render_trace()` Logic

```python
def render_trace(trace_path, out_path, cfg):
    # 1. Optional intro (1 sec)
    if cfg.intro_text:
        write intro_frame × cfg.fps

    # 2. All simulation frames
    for line in trace:
        write simulation_frame
        track final_state

    # 3. Outcome frame (2 sec)
    write outcome_frame × (cfg.fps * 2)
```

### Enhanced HUD

**Previous:**
```
tick=42  A=8  B=7  DRAW
```

**Current:**
```
Step 42
Team A: 8  Team B: 7
DRAW
```

More structured, easier to read at a glance.

## Backwards Compatibility

✅ **Fully backwards compatible**

- All existing code works unchanged
- `--intro-text` is optional (defaults to `None`)
- If not provided, no intro frame is rendered
- Outcome frame always shown (improves all videos)

## Performance Impact

**Minimal:**
- Intro: +30 frames (1 sec) if enabled
- Outcome: +60 frames (2 sec) always
- Total overhead: ~2-3 seconds rendering time for typical 100-frame match

## Use Cases

### 1. Evolution Experiment Videos

```bash
python3 scripts/visualizer.py \
    data/runs/m23_sustained_50gen/gen_0042/trace_sample.jsonl \
    viz_gen42.mp4 \
    --intro-text "M23 Generation 42\nRefined: Claim+Kite - Extended Retreat"
```

Clearly labels which generation and tactic being visualized.

### 2. Tournament Match Videos

```bash
python3 scripts/visualizer.py \
    data/runs/tourney/match_012.jsonl \
    match_012.mp4 \
    --intro-text "Tournament Round 4\nCluster_v1 vs Pursuit_v1"
```

Context for which AIs are competing.

### 3. Publication/Demo Videos

```bash
python3 scripts/visualizer.py \
    data/runs/m22_rq1_100gen/gen_0033/best_match.jsonl \
    demo_winning_tactic.mp4 \
    --intro-text "LLM-Discovered Tactic\nClaim-Arbitrated Targeting + Post-Shot Kiting\nFitness: +1.0 (10-0 wins)"
```

Self-documenting videos for presentations.

## Testing

### Smoke Tests

```bash
# Test with intro
python3 scripts/visualizer.py \
    data/runs/tourney_demo/opus47/match/trace.jsonl \
    /tmp/test_intro.mp4 \
    --intro-text "Test Match\nLine 2"

# Test without intro (backwards compatibility)
python3 scripts/visualizer.py \
    data/runs/tourney_demo/opus47/match/trace.jsonl \
    /tmp/test_no_intro.mp4

# Verify frame counts
# With intro: 30 (intro) + 100 (sim) + 60 (outcome) = 190 frames
# Without: 0 (intro) + 100 (sim) + 60 (outcome) = 160 frames
```

### Validated

✅ Both test cases passed:
- `test_intro.mp4`: 190 frames, 619 KB
- `test_no_intro.mp4`: 160 frames, 562 KB
- Demo video created: `data/viz_demo_enhanced.mp4`

## Future Enhancements (Not Implemented)

Potential follow-ups:

1. **Metric overlay**: Show AAR metrics during playback
2. **Tactic annotations**: Highlight coordinated behaviors (claims, kiting)
3. **Replay controls**: Interactive HTML5 player with seek
4. **Comparison mode**: Side-by-side before/after for evolution

---

## Files Modified

- `scripts/visualizer.py`:
  - Added `intro_text` to `RenderConfig`
  - Implemented `_draw_intro_frame()`
  - Implemented `_draw_outcome_frame()`
  - Enhanced `_draw_hud()` with prominent step counter
  - Updated `render_trace()` to render intro + outcome frames
  - Added `--intro-text` CLI argument

- `README.md`:
  - Updated visualization examples with new features
  - Documented video structure (intro/steps/outcome)

## Demo

Created `data/viz_demo_enhanced.mp4`:
- Tournament match with intro text
- Shows all new features (intro, step counter, outcome)
- 615 KB, 190 frames

---

**Status:** Complete and tested ✅
