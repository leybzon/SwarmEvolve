"""Render a SwarmEvolve JSONL trace to an MP4 video with H.264 codec.

This is an improved version of visualizer.py with:
1. H.264 codec support via ffmpeg for browser compatibility
2. Slowdown multiplier for slow-motion playback
3. Targeting arrows showing which drone is targeting which enemy
4. Tick counter already in top-left corner

Usage:
    python3 scripts/visualizer_v2.py <trace.jsonl> <out.mp4>
        [--fps 30] [--resolution WIDTHxHEIGHT] [--arena WxH]
        [--disable-range FLOAT] [--no-range-ring] [--slowdown MULT]
        [--codec {h264,mp4v}]
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

LOG = logging.getLogger("visualizer_v2")

# BGR (OpenCV byte order)
COLOR_BG = (32, 32, 32)
COLOR_GRID = (48, 48, 48)
COLOR_A = (255, 128, 32)           # blue-ish (Team A)
COLOR_B = (32, 32, 255)            # red (Team B)
COLOR_DEAD = (96, 96, 96)
COLOR_RANGE = (72, 72, 72)
COLOR_CD_BAR = (0, 220, 220)
COLOR_HUD = (255, 255, 255)

DRONE_RADIUS_PX = 6
COOLDOWN_BAR_W_PX = 14
COOLDOWN_BAR_H_PX = 2
GRID_STEP_UNITS = 200.0


@dataclass(frozen=True)
class RenderConfig:
    """Rendering configuration."""
    fps: int = 30
    width: int = 800
    height: int = 800
    arena_w: float = 1000.0
    arena_h: float = 1000.0
    disable_range: float = 50.0
    draw_range_ring: bool = True
    max_cooldown: int = 10
    intro_text: str | None = None
    slowdown: float = 1.0  # playback slowdown multiplier (e.g., 4.0 for 4x slower)
    codec: str = "h264"    # "h264" (ffmpeg) or "mp4v" (cv2)


def _world_to_px(x: float, y: float, cfg: RenderConfig) -> tuple[int, int]:
    """Map drone-space (x, y) → pixel-space (px, py). Both are Y-down."""
    px = int(round(x / cfg.arena_w * cfg.width))
    py = int(round(y / cfg.arena_h * cfg.height))
    px = max(0, min(cfg.width - 1, px))
    py = max(0, min(cfg.height - 1, py))
    return px, py


def _draw_grid(frame: np.ndarray, cfg: RenderConfig) -> None:
    """Paint faint gridlines every GRID_STEP_UNITS of drone space."""
    x = GRID_STEP_UNITS
    while x < cfg.arena_w:
        gx, _ = _world_to_px(x, 0.0, cfg)
        cv2.line(frame, (gx, 0), (gx, cfg.height - 1), COLOR_GRID, 1)
        x += GRID_STEP_UNITS
    y = GRID_STEP_UNITS
    while y < cfg.arena_h:
        _, gy = _world_to_px(0.0, y, cfg)
        cv2.line(frame, (0, gy), (cfg.width - 1, gy), COLOR_GRID, 1)
        y += GRID_STEP_UNITS


def _draw_targeting_arrow(frame: np.ndarray, attacker: dict, target: dict,
                          color: tuple[int, int, int], cfg: RenderConfig) -> None:
    """Draw dotted arrow from attacker to target with small tip in team color."""
    # Don't check if target is alive - show arrows to dead targets too (last known position)
    if not attacker.get("alive", True):
        return

    ax, ay = _world_to_px(float(attacker["x"]), float(attacker["y"]), cfg)
    tx, ty = _world_to_px(float(target["x"]), float(target["y"]), cfg)

    # Calculate distance
    dx = tx - ax
    dy = ty - ay
    dist = np.sqrt(dx*dx + dy*dy)

    if dist < 5:  # Too close, skip arrow
        return

    # Draw dotted line by drawing small segments with gaps
    dot_length = 4  # pixels per dot
    gap_length = 6  # pixels between dots
    segment_length = dot_length + gap_length
    num_dots = int(dist / segment_length)

    for i in range(num_dots):
        t1 = (i * segment_length) / dist
        t2 = min((i * segment_length + dot_length) / dist, 1.0)
        x1 = int(ax + dx * t1)
        y1 = int(ay + dy * t1)
        x2 = int(ax + dx * t2)
        y2 = int(ay + dy * t2)
        cv2.line(frame, (x1, y1), (x2, y2), color, 1, lineType=cv2.LINE_AA)

    # Draw arrowhead at the end
    tip_length_px = min(8, dist * 0.15)  # Small arrow tip
    angle = np.arctan2(dy, dx)
    tip_angle = 0.4  # radians

    # Calculate arrowhead points
    p1_x = int(tx - tip_length_px * np.cos(angle - tip_angle))
    p1_y = int(ty - tip_length_px * np.sin(angle - tip_angle))
    p2_x = int(tx - tip_length_px * np.cos(angle + tip_angle))
    p2_y = int(ty - tip_length_px * np.sin(angle + tip_angle))

    cv2.line(frame, (tx, ty), (p1_x, p1_y), color, 1, lineType=cv2.LINE_AA)
    cv2.line(frame, (tx, ty), (p2_x, p2_y), color, 1, lineType=cv2.LINE_AA)


def _draw_drone(frame: np.ndarray, drone: dict[str, Any], color: tuple[int, int, int],
                cfg: RenderConfig) -> None:
    px, py = _world_to_px(float(drone["x"]), float(drone["y"]), cfg)

    if not drone["alive"]:
        # Dead drones: small grey X
        d = DRONE_RADIUS_PX - 2
        cv2.line(frame, (px - d, py - d), (px + d, py + d), COLOR_DEAD, 1)
        cv2.line(frame, (px - d, py + d), (px + d, py - d), COLOR_DEAD, 1)
        return

    # Disable-range ring
    if cfg.draw_range_ring:
        radius_px = max(1, int(round(cfg.disable_range / cfg.arena_w * cfg.width)))
        cv2.circle(frame, (px, py), radius_px, COLOR_RANGE, 1)

    # Drone body
    cv2.circle(frame, (px, py), DRONE_RADIUS_PX, color, thickness=-1, lineType=cv2.LINE_AA)

    # Cooldown bar
    cd = int(drone.get("cooldown", 0))
    if cd > 0 and cfg.max_cooldown > 0:
        frac = min(1.0, cd / float(cfg.max_cooldown))
        bar_w = int(round(COOLDOWN_BAR_W_PX * frac))
        if bar_w >= 1:
            bar_x0 = px - COOLDOWN_BAR_W_PX // 2
            bar_y0 = py + DRONE_RADIUS_PX + 2
            cv2.rectangle(frame, (bar_x0, bar_y0), (bar_x0 + bar_w, bar_y0 + COOLDOWN_BAR_H_PX),
                          COLOR_CD_BAR, thickness=-1)


def _draw_hud(frame: np.ndarray, tick: int, a_alive: int, b_alive: int,
              outcome: str | None, cfg: RenderConfig) -> None:
    """Draw HUD with tick counter in top-left."""
    # Draw semi-transparent background box for HUD
    box_height = 80 if outcome else 60
    cv2.rectangle(frame, (0, 0), (280, box_height), (0, 0, 0), -1)

    # Top-left: Step counter (more prominent with shadow for contrast)
    step_text = f"Step {tick}"
    # Shadow
    cv2.putText(frame, step_text, (9, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 0, 0), 3, lineType=cv2.LINE_AA)
    # Main text
    cv2.putText(frame, step_text, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                COLOR_HUD, 2, lineType=cv2.LINE_AA)

    # Below step: Team status
    status_text = f"Team A: {a_alive}  Team B: {b_alive}"
    cv2.putText(frame, status_text, (8, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                COLOR_HUD, 1, lineType=cv2.LINE_AA)

    # Show outcome if present
    if outcome:
        cv2.putText(frame, outcome, (8, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    COLOR_HUD, 1, lineType=cv2.LINE_AA)


def _draw_intro_frame(cfg: RenderConfig) -> np.ndarray:
    """Create intro frame with optional metadata text."""
    frame = np.full((cfg.height, cfg.width, 3), COLOR_BG, dtype=np.uint8)

    if cfg.intro_text:
        lines = cfg.intro_text.split('\n')
        y_start = cfg.height // 2 - len(lines) * 20

        for i, line in enumerate(lines):
            (text_width, text_height), _ = cv2.getTextSize(
                line, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2
            )
            x = (cfg.width - text_width) // 2
            y = y_start + i * 40

            cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        COLOR_HUD, 2, lineType=cv2.LINE_AA)

    return frame


def _draw_outcome_frame(final_tick: int, outcome: str, a_alive: int, b_alive: int,
                        cfg: RenderConfig) -> np.ndarray:
    """Create outcome frame showing match result."""
    frame = np.full((cfg.height, cfg.width, 3), COLOR_BG, dtype=np.uint8)
    _draw_grid(frame, cfg)

    # Determine winner and color
    if "TEAM_A_WIN" in outcome or (a_alive > b_alive):
        winner_text = "TEAM A WINS!"
        winner_color = COLOR_A
    elif "TEAM_B_WIN" in outcome or (b_alive > a_alive):
        winner_text = "TEAM B WINS!"
        winner_color = COLOR_B
    else:
        winner_text = "DRAW"
        winner_color = COLOR_HUD

    # Large centered winner text
    (text_width, text_height), _ = cv2.getTextSize(
        winner_text, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3
    )
    x = (cfg.width - text_width) // 2
    y = cfg.height // 2

    cv2.putText(frame, winner_text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.5,
                winner_color, 3, lineType=cv2.LINE_AA)

    # Final stats
    stats_text = f"Final Step: {final_tick}  |  Team A: {a_alive}  Team B: {b_alive}"
    (stats_width, _), _ = cv2.getTextSize(
        stats_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1
    )
    stats_x = (cfg.width - stats_width) // 2
    stats_y = y + 50

    cv2.putText(frame, stats_text, (stats_x, stats_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                COLOR_HUD, 1, lineType=cv2.LINE_AA)

    return frame


def _render_frame(line: dict[str, Any], cfg: RenderConfig,
                  last_targets_a: dict[int, int], last_targets_b: dict[int, int]) -> np.ndarray:
    """Render one trace line into a BGR uint8 frame.

    last_targets_a/b: dict mapping drone_id -> last_known_target_id for persistence
    """
    frame = np.full((cfg.height, cfg.width, 3), COLOR_BG, dtype=np.uint8)
    _draw_grid(frame, cfg)

    team_a = line["team_a"]
    team_b = line["team_b"]

    # Draw targeting arrows first (under drones)
    # Parse v2 trace format if available
    if "actions_a" in line:
        actions_a = line["actions_a"]
        actions_b = line["actions_b"]

        # Team A targeting Team B (with persistence)
        for i, action in enumerate(actions_a):
            if not action.get("alive", True):
                continue
            target_id = action.get("target_id", -1)

            # Update last known target if we have a valid one
            if target_id >= 0:
                last_targets_a[i] = target_id

            # Draw arrow using last known target (even if current target_id == -1)
            # Keep drawing even if target is dead (shows intent/last known position)
            if i in last_targets_a and last_targets_a[i] < len(team_b):
                attacker = team_a[i]
                target = team_b[last_targets_a[i]]
                if attacker.get("alive", True):  # Only check if attacker alive
                    _draw_targeting_arrow(frame, attacker, target, COLOR_A, cfg)

        # Team B targeting Team A (with persistence)
        for i, action in enumerate(actions_b):
            if not action.get("alive", True):
                continue
            target_id = action.get("target_id", -1)

            # Update last known target if we have a valid one
            if target_id >= 0:
                last_targets_b[i] = target_id

            # Draw arrow using last known target (even if current target_id == -1)
            # Keep drawing even if target is dead (shows intent/last known position)
            if i in last_targets_b and last_targets_b[i] < len(team_a):
                attacker = team_b[i]
                target = team_a[last_targets_b[i]]
                if attacker.get("alive", True):  # Only check if attacker alive
                    _draw_targeting_arrow(frame, attacker, target, COLOR_B, cfg)

    # Draw drones on top of arrows
    for d in team_a:
        _draw_drone(frame, d, COLOR_A, cfg)
    for d in team_b:
        _draw_drone(frame, d, COLOR_B, cfg)

    a_alive = sum(1 for d in team_a if d["alive"])
    b_alive = sum(1 for d in team_b if d["alive"])
    _draw_hud(frame, int(line["tick"]), a_alive, b_alive, line.get("outcome"), cfg)
    return frame


def _iter_trace(path: Path) -> Iterable[dict[str, Any]]:
    """Yield one parsed trace line at a time. Raises on format errors."""
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            for field in ("tick", "team_a", "team_b"):
                if field not in obj:
                    raise ValueError(f"{path}:{lineno}: missing field {field!r}")
            yield obj


def render_trace(trace_path: Path, out_path: Path, cfg: RenderConfig | None = None) -> int:
    """Render ``trace_path`` into ``out_path`` (MP4). Returns frame count written.

    Uses H.264 codec via ffmpeg for browser compatibility by default.
    Falls back to mp4v if ffmpeg is unavailable.
    """
    cfg = cfg or RenderConfig()
    if not trace_path.exists():
        raise FileNotFoundError(trace_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate effective FPS for playback (divided by slowdown)
    playback_fps = int(round(cfg.fps / cfg.slowdown))

    # Collect all frames first
    frames = []

    # Optional intro frame (1 second)
    if cfg.intro_text:
        intro_frame = _draw_intro_frame(cfg)
        for _ in range(cfg.fps):  # 1 second at cfg.fps
            frames.append(intro_frame)

    # Render all trace frames
    final_tick = 0
    final_outcome = "DRAW"
    final_a_alive = 0
    final_b_alive = 0

    # Target persistence dictionaries
    last_targets_a: dict[int, int] = {}
    last_targets_b: dict[int, int] = {}

    for line in _iter_trace(trace_path):
        frame = _render_frame(line, cfg, last_targets_a, last_targets_b)
        # Repeat frame based on slowdown multiplier
        for _ in range(int(round(cfg.slowdown))):
            frames.append(frame)

        # Track final state
        final_tick = int(line["tick"])
        final_outcome = line.get("outcome", "DRAW")
        team_a = line["team_a"]
        team_b = line["team_b"]
        final_a_alive = sum(1 for d in team_a if d["alive"])
        final_b_alive = sum(1 for d in team_b if d["alive"])

    # Outcome frame (2 seconds hold)
    outcome_frame = _draw_outcome_frame(final_tick, final_outcome,
                                       final_a_alive, final_b_alive, cfg)
    for _ in range(cfg.fps * 2):  # 2 seconds at cfg.fps
        frames.append(outcome_frame)

    n_frames = len(frames)

    # Write video
    if cfg.codec == "h264":
        # Try H.264 via ffmpeg
        try:
            _write_h264_ffmpeg(frames, out_path, playback_fps, cfg)
            LOG.info("wrote %d frames to %s (H.264 via ffmpeg)", n_frames, out_path)
        except Exception as e:
            LOG.warning("ffmpeg H.264 encoding failed: %s, falling back to mp4v", e)
            _write_mp4v_cv2(frames, out_path, playback_fps, cfg)
            LOG.info("wrote %d frames to %s (mp4v via cv2)", n_frames, out_path)
    else:
        # Use cv2.VideoWriter with mp4v
        _write_mp4v_cv2(frames, out_path, playback_fps, cfg)
        LOG.info("wrote %d frames to %s (mp4v via cv2)", n_frames, out_path)

    return n_frames


def _write_h264_ffmpeg(frames: list[np.ndarray], out_path: Path, fps: int, cfg: RenderConfig) -> None:
    """Write frames to H.264 MP4 using ffmpeg subprocess."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{cfg.width}x{cfg.height}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",  # stdin
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path)
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()

    if proc.returncode != 0:
        stderr = proc.stderr.read().decode()
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}: {stderr}")


def _write_mp4v_cv2(frames: list[np.ndarray], out_path: Path, fps: int, cfg: RenderConfig) -> None:
    """Write frames to mp4v MP4 using cv2.VideoWriter (fallback)."""
    fourcc = cv2.VideoWriter.fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (cfg.width, cfg.height))
    if not writer.isOpened():
        raise RuntimeError(f"cv2.VideoWriter failed to open for {out_path}")

    for frame in frames:
        writer.write(frame)

    writer.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_resolution(s: str) -> tuple[int, int]:
    w, _, h = s.partition("x")
    if not w or not h:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {s!r}")
    return int(w), int(h)


def _parse_arena(s: str) -> tuple[float, float]:
    w, _, h = s.partition("x")
    if not w or not h:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {s!r}")
    return float(w), float(h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a SwarmEvolve JSONL trace to MP4.")
    parser.add_argument("trace", type=Path, help="input JSONL trace path")
    parser.add_argument("output", type=Path, help="output MP4 path")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--resolution", type=_parse_resolution, default=(800, 800),
                        help="output resolution WIDTHxHEIGHT (default 800x800)")
    parser.add_argument("--arena", type=_parse_arena, default=(1000.0, 1000.0),
                        help="arena dimensions WIDTHxHEIGHT in drone-space units")
    parser.add_argument("--disable-range", type=float, default=50.0,
                        help="disable_range for the range ring (default 50.0)")
    parser.add_argument("--no-range-ring", action="store_true",
                        help="skip drawing the faint disable_range ring")
    parser.add_argument("--intro-text", type=str, default=None,
                        help="optional text shown for 1 second at start (use \\n for newlines)")
    parser.add_argument("--slowdown", type=float, default=1.0,
                        help="playback slowdown multiplier (e.g., 4.0 for 4x slower, default 1.0)")
    parser.add_argument("--codec", type=str, choices=["h264", "mp4v"], default="h264",
                        help="video codec: h264 (ffmpeg, default) or mp4v (cv2)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Process intro text to handle literal \n escape sequences
    intro_text = None
    if args.intro_text:
        intro_text = args.intro_text.replace('\\n', '\n')

    cfg = RenderConfig(
        fps=args.fps,
        width=args.resolution[0],
        height=args.resolution[1],
        arena_w=args.arena[0],
        arena_h=args.arena[1],
        disable_range=args.disable_range,
        draw_range_ring=not args.no_range_ring,
        intro_text=intro_text,
        slowdown=args.slowdown,
        codec=args.codec,
    )

    try:
        n = render_trace(args.trace, args.output, cfg)
    except FileNotFoundError as exc:
        print(f"ERROR kind=io detail=trace-not-found path={exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERROR kind=format detail={exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"ERROR kind=codec detail={exc}", file=sys.stderr)
        return 4

    print(f"rendered frames={n} output={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
