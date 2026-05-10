"""Tests for scripts/visualizer.py (M5).

Verified:
  1. ``render_trace`` on the golden trace produces an MP4 whose frame
     count equals the line count (117 frames for the M4 golden).
  2. Output frames have the requested resolution.
  3. Y-down convention: a drone at (x, small_y) renders in the TOP half
     of the frame (not the bottom — a common off-by-flipped-axis bug).
  4. Team A (blue-ish) and Team B (red) are visually distinct in the
     rendered frame (dominant color channel per team pixel matches).
  5. Dead drones do NOT draw a range ring (absence-test prevents
     regressions where dead drones falsely advertise in-range status).
  6. CLI rejects nonexistent trace with exit code 2 and structured stderr.

Tests skip if opencv-python is not installed (so a minimal dev env
without the heavy cv2 wheel still runs the rest of the suite).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_TRACE = REPO_ROOT / "tests" / "fixtures" / "golden" / "seed42_pursuit_vs_cluster.jsonl"
VISUALIZER = REPO_ROOT / "scripts" / "visualizer.py"

# Make scripts/ importable for direct function calls.
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from visualizer import RenderConfig, _render_frame, render_trace


def _read_frame_count(mp4_path: Path) -> int:
    cap = cv2.VideoCapture(str(mp4_path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def _read_frame_size(mp4_path: Path) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(mp4_path))
    try:
        return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Integration: golden trace → MP4
# ---------------------------------------------------------------------------


def test_render_golden_trace_produces_mp4(tmp_path: Path) -> None:
    fps = 30
    out = tmp_path / "demo.mp4"
    n_written = render_trace(GOLDEN_TRACE, out, RenderConfig(fps=fps, width=400, height=400))

    assert out.exists() and out.stat().st_size > 0
    n_lines = sum(1 for line in GOLDEN_TRACE.read_text().splitlines() if line.strip())
    # Since M23 the writer also appends a 2-second outcome hold (no intro
    # frame here because intro_text defaults to ""): trace_lines + 2*fps.
    expected = n_lines + 2 * fps
    assert n_written == expected, f"writer wrote {n_written}, expected {expected}"

    # VideoCapture sometimes reports frame count - 1 depending on muxer
    # quirks; tolerate ±1.
    read_back = _read_frame_count(out)
    assert abs(read_back - expected) <= 1, f"cv2 read {read_back}, expected={expected}"


def test_render_honours_requested_resolution(tmp_path: Path) -> None:
    out = tmp_path / "res.mp4"
    render_trace(GOLDEN_TRACE, out, RenderConfig(fps=15, width=320, height=240))
    w, h = _read_frame_size(out)
    assert (w, h) == (320, 240), f"got {w}x{h}"


# ---------------------------------------------------------------------------
# Unit: per-frame pixel assertions. Use ``_render_frame`` directly so we
# don't pay encoder overhead per test and so frame assertions are exact
# (the MP4 codec is lossy).
# ---------------------------------------------------------------------------


def _make_line(team_a: list[dict], team_b: list[dict], tick: int = 0) -> dict:
    return {"tick": tick, "team_a": team_a, "team_b": team_b}


def _drone(did: int, x: float, y: float, alive: bool = True, cooldown: int = 0) -> dict:
    return {"id": did, "x": x, "y": y, "cooldown": cooldown, "alive": alive}


def test_ydown_drone_near_top_renders_in_top_half() -> None:
    """A drone at y=50 (near arena top) must render in the TOP half of the frame.

    Matplotlib-style Y-flip would put y=50 at the bottom — this test
    catches that regression.
    """
    cfg = RenderConfig(width=200, height=200, arena_w=1000.0, arena_h=1000.0)
    line = _make_line(team_a=[_drone(0, x=500.0, y=50.0)], team_b=[_drone(0, x=0.0, y=999.0)])
    frame = _render_frame(line, cfg)

    # Team A is blue-ish (first channel heavy). Find the brightest blue
    # pixel; assert its Y coordinate is in the top half.
    blue_channel = frame[:, :, 0].astype(np.int32)
    # Subtract other channels so we pick up the drone body and not the grid.
    dominance = blue_channel - frame[:, :, 1].astype(np.int32) - frame[:, :, 2].astype(np.int32)
    yy, _ = np.unravel_index(int(np.argmax(dominance)), dominance.shape)
    assert yy < cfg.height // 2, f"Team A drone at y=50 rendered at pixel y={yy}, expected top half"


def test_team_colors_are_distinct() -> None:
    cfg = RenderConfig(width=200, height=200)
    line = _make_line(team_a=[_drone(0, x=250.0, y=500.0)], team_b=[_drone(0, x=750.0, y=500.0)])
    frame = _render_frame(line, cfg)

    # Left quarter: Team A (blue-dominant). Right quarter: Team B (red-dominant).
    left = frame[:, : cfg.width // 2, :]
    right = frame[:, cfg.width // 2 :, :]

    # Pick the single pixel per side with the strongest blue-minus-red
    # (Team A) and red-minus-blue (Team B) score.
    a_score = left[:, :, 0].astype(np.int32) - left[:, :, 2].astype(np.int32)
    b_score = right[:, :, 2].astype(np.int32) - right[:, :, 0].astype(np.int32)
    assert a_score.max() > 50, "Team A side not blue-dominant at drone pixel"
    assert b_score.max() > 50, "Team B side not red-dominant at drone pixel"


def test_dead_drone_does_not_draw_range_ring() -> None:
    """Dead drones must not advertise a range ring.

    We compare two frames: one with an alive drone, one with the same
    drone dead. The alive frame must have strictly more "range ring"
    pixels (faint grey, COLOR_RANGE) than the dead one.
    """
    cfg = RenderConfig(width=400, height=400)
    alive_line = _make_line(
        team_a=[_drone(0, x=500.0, y=500.0, alive=True)],
        team_b=[_drone(0, x=10.0, y=10.0, alive=False)],
    )
    dead_line = _make_line(
        team_a=[_drone(0, x=500.0, y=500.0, alive=False)],
        team_b=[_drone(0, x=10.0, y=10.0, alive=False)],
    )
    f_alive = _render_frame(alive_line, cfg)
    f_dead = _render_frame(dead_line, cfg)

    # COLOR_RANGE = (72, 72, 72) — count pixels matching exactly.
    def ring_pixels(f: np.ndarray) -> int:
        return int(np.sum((f[:, :, 0] == 72) & (f[:, :, 1] == 72) & (f[:, :, 2] == 72)))

    assert ring_pixels(f_alive) > ring_pixels(f_dead) + 10, (
        "alive frame should have noticeably more range-ring pixels than dead frame"
    )


def test_cooldown_bar_scales_with_cooldown() -> None:
    cfg = RenderConfig(width=400, height=400, max_cooldown=10)
    cyan = (0, 220, 220)

    def cyan_pixels(frame: np.ndarray) -> int:
        return int(
            np.sum(
                (frame[:, :, 0] == cyan[0])
                & (frame[:, :, 1] == cyan[1])
                & (frame[:, :, 2] == cyan[2])
            )
        )

    f_zero = _render_frame(_make_line([_drone(0, 500, 500, cooldown=0)], []), cfg)
    f_half = _render_frame(_make_line([_drone(0, 500, 500, cooldown=5)], []), cfg)
    f_full = _render_frame(_make_line([_drone(0, 500, 500, cooldown=10)], []), cfg)

    assert cyan_pixels(f_zero) == 0, "cooldown=0 must draw no bar"
    assert cyan_pixels(f_half) > 0, "cooldown=5 must draw a bar"
    assert cyan_pixels(f_full) > cyan_pixels(f_half), "bar must grow with cooldown"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_cli_rejects_missing_trace(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jsonl"
    proc = subprocess.run(
        [sys.executable, str(VISUALIZER), str(missing), str(tmp_path / "out.mp4")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "trace-not-found" in proc.stderr


def test_malformed_trace_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    # Missing required "team_b" field.
    bad.write_text(json.dumps({"tick": 0, "team_a": []}) + "\n")
    with pytest.raises(ValueError, match="missing field 'team_b'"):
        render_trace(bad, tmp_path / "out.mp4", RenderConfig())
