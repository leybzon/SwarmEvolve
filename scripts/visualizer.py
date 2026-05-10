"""Render a SwarmEvolve JSONL trace to an MP4 video.

Usage:
    python3 scripts/visualizer.py <trace.jsonl> <out.mp4>
        [--fps 30] [--resolution WIDTHxHEIGHT] [--arena WxH]
        [--disable-range FLOAT] [--no-range-ring]

Design notes
------------
* Uses ``cv2.VideoWriter`` with the mp4v codec so the tool has no hard
  dependency on an external ``ffmpeg`` binary. This is the choice
  [IMPLEMENTATION_PLAN.md §7] prescribes to keep CI portable.
* The engine's coordinate system is Y-down (SPECIFICATION §1.2). OpenCV
  image coordinates are also Y-down, so we do **not** flip the Y axis
  — drone-space (x, y) maps directly to pixel-space (px, py) via a simple
  scale. A matplotlib backend would need ``ax.invert_yaxis()``.
* Arena dimensions default to the engine's 1000×1000 (SPECIFICATION §1.3);
  override with ``--arena WxH`` if a non-default was used.
* Render strategy is frame-by-frame: for each trace line we paint a fresh
  arena background, then each drone as a filled circle plus optional
  faint disable_range ring and a cooldown bar. Dead drones render as
  small grey X markers so retreat / mutual-destruction is visible.
* ``ValidationError``-style inputs (missing ``tick``/``team_a``/``team_b``)
  are rejected with a non-zero exit code and a structured stderr message,
  so this doubles as a soft format check.

This module is importable: ``render_trace(trace_path, out_path, config)``
is the callable entry point for tests.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

LOG = logging.getLogger("visualizer")

# BGR (OpenCV byte order). Keep the palette in one place so tests can
# reference it symbolically and so a future dark-theme flag is a one-line
# change.
COLOR_BG = (32, 32, 32)  # near-black arena background
COLOR_GRID = (48, 48, 48)  # subtle grid lines
COLOR_A = (255, 128, 32)  # blue-ish (Team A)
COLOR_B = (32, 32, 255)  # red (Team B)
COLOR_DEAD = (96, 96, 96)  # grey for deceased drones
COLOR_RANGE = (72, 72, 72)  # faint disable_range ring
COLOR_CD_BAR = (0, 220, 220)  # cyan cooldown indicator
COLOR_HUD = (220, 220, 220)  # HUD text

DRONE_RADIUS_PX = 6
COOLDOWN_BAR_W_PX = 14
COOLDOWN_BAR_H_PX = 2
GRID_STEP_UNITS = 200.0


@dataclass(frozen=True)
class RenderConfig:
    """Knobs that don't live in the trace itself."""

    fps: int = 30
    width: int = 800
    height: int = 800
    arena_w: float = 1000.0
    arena_h: float = 1000.0
    disable_range: float = 50.0  # engine default per SPECIFICATION §1.3
    draw_range_ring: bool = True
    max_cooldown: int = 10  # engine default; used to normalize CD bar
    intro_text: str | None = None  # optional intro text shown for 1 second


def _world_to_px(x: float, y: float, cfg: RenderConfig) -> tuple[int, int]:
    """Map drone-space (x, y) → pixel-space (px, py). Both are Y-down."""
    px = round(x / cfg.arena_w * cfg.width)
    py = round(y / cfg.arena_h * cfg.height)
    # Clamp to the visible frame in case the AI wanders fractionally out
    # of bounds (the engine clamps pre-render, but be defensive).
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


def _draw_drone(
    frame: np.ndarray, drone: dict[str, Any], color: tuple[int, int, int], cfg: RenderConfig
) -> None:
    px, py = _world_to_px(float(drone["x"]), float(drone["y"]), cfg)

    if not drone["alive"]:
        # Dead drones: small grey X, no range ring, no cooldown bar.
        d = DRONE_RADIUS_PX - 2
        cv2.line(frame, (px - d, py - d), (px + d, py + d), COLOR_DEAD, 1)
        cv2.line(frame, (px - d, py + d), (px + d, py - d), COLOR_DEAD, 1)
        return

    # Faint disable-range ring. Drawn without LINE_AA on purpose so the
    # stroke uses exactly COLOR_RANGE — unit tests rely on counting pixels
    # with that exact color to assert "dead drones don't advertise range".
    if cfg.draw_range_ring:
        radius_px = max(1, round(cfg.disable_range / cfg.arena_w * cfg.width))
        cv2.circle(frame, (px, py), radius_px, COLOR_RANGE, 1)

    # Drone body.
    cv2.circle(frame, (px, py), DRONE_RADIUS_PX, color, thickness=-1, lineType=cv2.LINE_AA)

    # Cooldown bar beneath the drone. Width shrinks as cooldown drops to 0.
    cd = int(drone.get("cooldown", 0))
    if cd > 0 and cfg.max_cooldown > 0:
        frac = min(1.0, cd / float(cfg.max_cooldown))
        bar_w = round(COOLDOWN_BAR_W_PX * frac)
        if bar_w >= 1:
            bar_x0 = px - COOLDOWN_BAR_W_PX // 2
            bar_y0 = py + DRONE_RADIUS_PX + 2
            cv2.rectangle(
                frame,
                (bar_x0, bar_y0),
                (bar_x0 + bar_w, bar_y0 + COOLDOWN_BAR_H_PX),
                COLOR_CD_BAR,
                thickness=-1,
            )


def _draw_hud(
    frame: np.ndarray, tick: int, a_alive: int, b_alive: int, outcome: str | None, cfg: RenderConfig
) -> None:
    """Draw heads-up display with tick counter and alive counts."""
    # Top-left: Step counter (more prominent)
    step_text = f"Step {tick}"
    cv2.putText(
        frame,
        step_text,
        (8, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        COLOR_HUD,
        2,
        lineType=cv2.LINE_AA,
    )

    # Below step: Team status
    status_text = f"Team A: {a_alive}  Team B: {b_alive}"
    cv2.putText(
        frame,
        status_text,
        (8, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        COLOR_HUD,
        1,
        lineType=cv2.LINE_AA,
    )

    # Show outcome if present (used in final frames)
    if outcome:
        cv2.putText(
            frame,
            outcome,
            (8, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            COLOR_HUD,
            1,
            lineType=cv2.LINE_AA,
        )


def _draw_intro_frame(cfg: RenderConfig) -> np.ndarray:
    """Create intro frame with optional metadata text."""
    frame = np.full((cfg.height, cfg.width, 3), COLOR_BG, dtype=np.uint8)

    if cfg.intro_text:
        # Center the intro text
        lines = cfg.intro_text.split("\n")
        y_start = cfg.height // 2 - len(lines) * 20

        for i, line in enumerate(lines):
            # Calculate text size to center it
            (text_width, _text_height), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            x = (cfg.width - text_width) // 2
            y = y_start + i * 40

            cv2.putText(
                frame,
                line,
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                COLOR_HUD,
                2,
                lineType=cv2.LINE_AA,
            )

    return frame


def _draw_outcome_frame(
    final_tick: int, outcome: str, a_alive: int, b_alive: int, cfg: RenderConfig
) -> np.ndarray:
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
    (text_width, _text_height), _ = cv2.getTextSize(winner_text, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
    x = (cfg.width - text_width) // 2
    y = cfg.height // 2

    cv2.putText(
        frame,
        winner_text,
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.5,
        winner_color,
        3,
        lineType=cv2.LINE_AA,
    )

    # Final stats below winner text
    stats_text = f"Final Step: {final_tick}  |  Team A: {a_alive}  Team B: {b_alive}"
    (stats_width, _), _ = cv2.getTextSize(stats_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
    stats_x = (cfg.width - stats_width) // 2
    stats_y = y + 50

    cv2.putText(
        frame,
        stats_text,
        (stats_x, stats_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        COLOR_HUD,
        1,
        lineType=cv2.LINE_AA,
    )

    return frame


def _render_frame(line: dict[str, Any], cfg: RenderConfig) -> np.ndarray:
    """Render one trace line into a BGR uint8 frame."""
    frame = np.full((cfg.height, cfg.width, 3), COLOR_BG, dtype=np.uint8)
    _draw_grid(frame, cfg)

    team_a = line["team_a"]
    team_b = line["team_b"]
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

    Renders intro frame (1 sec if intro_text provided), all trace frames, and
    outcome frame (2 sec hold at end showing winner and final step).

    Raises:
        FileNotFoundError: if ``trace_path`` does not exist.
        ValueError: on malformed trace lines.
        RuntimeError: if the VideoWriter fails to open (codec missing).
    """
    cfg = cfg or RenderConfig()
    if not trace_path.exists():
        raise FileNotFoundError(trace_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # mp4v is the portable fallback codec for MP4 that ships with every
    # opencv-python wheel. H.264 would need ffmpeg/x264 in the image.
    fourcc = cv2.VideoWriter.fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, cfg.fps, (cfg.width, cfg.height))
    if not writer.isOpened():
        raise RuntimeError(f"cv2.VideoWriter failed to open for {out_path}")

    n_frames = 0
    try:
        # Optional intro frame (1 second)
        if cfg.intro_text:
            intro_frame = _draw_intro_frame(cfg)
            for _ in range(cfg.fps):  # 1 second at cfg.fps
                writer.write(intro_frame)
                n_frames += 1

        # Render all trace frames and track final state
        final_tick = 0
        final_outcome = "DRAW"
        final_a_alive = 0
        final_b_alive = 0

        for line in _iter_trace(trace_path):
            frame = _render_frame(line, cfg)
            writer.write(frame)
            n_frames += 1

            # Track final state for outcome frame
            final_tick = int(line["tick"])
            final_outcome = line.get("outcome", "DRAW")
            team_a = line["team_a"]
            team_b = line["team_b"]
            final_a_alive = sum(1 for d in team_a if d["alive"])
            final_b_alive = sum(1 for d in team_b if d["alive"])

        # Outcome frame (2 seconds hold)
        outcome_frame = _draw_outcome_frame(
            final_tick, final_outcome, final_a_alive, final_b_alive, cfg
        )
        for _ in range(cfg.fps * 2):  # 2 seconds at cfg.fps
            writer.write(outcome_frame)
            n_frames += 1

    finally:
        writer.release()

    LOG.info("wrote %d frames to %s", n_frames, out_path)
    return n_frames


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
    parser.add_argument(
        "--resolution",
        type=_parse_resolution,
        default=(800, 800),
        help="output resolution WIDTHxHEIGHT (default 800x800)",
    )
    parser.add_argument(
        "--arena",
        type=_parse_arena,
        default=(1000.0, 1000.0),
        help="arena dimensions WIDTHxHEIGHT in drone-space units",
    )
    parser.add_argument(
        "--disable-range",
        type=float,
        default=50.0,
        help="disable_range for the range ring (default 50.0)",
    )
    parser.add_argument(
        "--no-range-ring", action="store_true", help="skip drawing the faint disable_range ring"
    )
    parser.add_argument(
        "--intro-text",
        type=str,
        default=None,
        help="optional text shown for 1 second at start (use \\n for newlines)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Process intro text to handle literal \n escape sequences
    intro_text = None
    if args.intro_text:
        intro_text = args.intro_text.replace("\\n", "\n")

    cfg = RenderConfig(
        fps=args.fps,
        width=args.resolution[0],
        height=args.resolution[1],
        arena_w=args.arena[0],
        arena_h=args.arena[1],
        disable_range=args.disable_range,
        draw_range_ring=not args.no_range_ring,
        intro_text=intro_text,
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
