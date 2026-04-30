#!/usr/bin/env python3
"""Generate video visualizations for M25 co-evolution experiment key rounds."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPO_ROOT = _HERE.parent
M25_DIR = REPO_ROOT / "data" / "runs" / "m25_coevolve_100r"
VID_DIR = M25_DIR / "videos"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOG = logging.getLogger("generate_m25_videos")


def load_journal_entry(round_num: int) -> dict:
    """Load journal entry for a specific round (by generation number)."""
    journal_path = M25_DIR / "journal.jsonl"
    with journal_path.open("r") as f:
        for line in f:
            entry = json.loads(line)
            if entry["generation"] == round_num:
                return entry
    raise ValueError(f"Round {round_num} not found in journal")


def _render_team_source(src_path: Path, namespace: str, dest: Path) -> None:
    """Render team source with namespace replacement."""
    text = src_path.read_text()
    if "TEAM_NS_PLACEHOLDER" in text:
        text = text.replace("TEAM_NS_PLACEHOLDER", namespace)
    text = text.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    text = text.replace('#include "../types.h"', '#include "types.h"')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def generate_trace(team_a_path: Path, team_b_path: Path,
                   trace_path: Path, seed: int) -> None:
    """Generate a single match trace file."""
    LOG.info("Generating trace: %s (A) vs %s (B)", team_a_path.stem, team_b_path.stem)

    team_a_dest = REPO_ROOT / "src" / "a" / "team_a_ai.cpp"
    team_b_dest = REPO_ROOT / "src" / "b" / "team_b_ai.cpp"

    # Backup existing files
    team_a_backup = None
    team_b_backup = None
    if team_a_dest.exists():
        team_a_backup = team_a_dest.with_suffix(".cpp.bak")
        team_a_dest.rename(team_a_backup)
    if team_b_dest.exists():
        team_b_backup = team_b_dest.with_suffix(".cpp.bak")
        team_b_dest.rename(team_b_backup)

    try:
        # Render team sources
        _render_team_source(team_a_path, "TeamA", team_a_dest)
        _render_team_source(team_b_path, "TeamB", team_b_dest)

        # Compile
        LOG.info("Compiling...")
        binary = REPO_ROOT / "swarmevolve"
        if binary.exists():
            binary.unlink()

        compiler = "/opt/homebrew/opt/llvm/bin/clang++"
        compile_cmd = [
            compiler, "-std=c++17", "-O3",
            f"-I{REPO_ROOT / 'src'}",
            "-o", str(binary),
            "src/engine.cpp",
            "src/a/team_a_ai.cpp",
            "src/b/team_b_ai.cpp",
        ]
        result = subprocess.run(
            compile_cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Compilation failed: {result.stderr}")

        # Run match with trace
        LOG.info("Running match with seed %d...", seed)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        run_cmd = [
            str(binary),
            "--record", str(trace_path),
            "--seed", str(seed),
        ]
        result = subprocess.run(run_cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        LOG.info("Match result: %s", result.stdout.strip())

    finally:
        # Restore backups
        if team_a_backup and team_a_backup.exists():
            if team_a_dest.exists():
                team_a_dest.unlink()
            team_a_backup.rename(team_a_dest)
        if team_b_backup and team_b_backup.exists():
            if team_b_dest.exists():
                team_b_dest.unlink()
            team_b_backup.rename(team_b_dest)


def generate_video(trace_path: Path, video_path: Path, intro_text: str) -> None:
    """Generate video from trace file."""
    LOG.info("Rendering video to %s", video_path)

    cmd = [
        "python3",
        str(_HERE / "visualizer.py"),
        str(trace_path),
        str(video_path),
        "--intro-text", intro_text,
        "-v",
    ]

    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    VID_DIR.mkdir(parents=True, exist_ok=True)

    # Key rounds from M25 co-evolution experiment
    # Round 1: Team B baseline (-0.8)
    # Round 13: Team B reaches parity (0.0)
    # Round 31: Team B BREAKTHROUGH (+0.9)
    # Round 41: Team B final champion (+0.9)
    key_rounds = [
        (1, "M25 Round 1: Baseline\\nTeam B (pursuit_v1) vs Team A (M22 gen 33 champion)\\nFitness: -0.8 (Team B losing)"),
        (13, "M25 Round 13: Reaching Parity\\nTeam B (Predictive Intercept Swarm) vs Team A\\nFitness: 0.0 (competitive balance achieved)"),
        (31, "M25 Round 31: BREAKTHROUGH\\nTeam B (Formation Spread) vs Team A\\nFitness: +0.9 (Team B dominant)"),
        (41, "M25 Round 41: Final Champion\\nTeam B (Zone Control with Baiting) vs Team A\\nFitness: +0.9 (sustained dominance)"),
    ]

    LOG.info("Generating M25 visualizations for %d key rounds", len(key_rounds))

    for round_num, intro_text in key_rounds:
        LOG.info("=" * 60)
        LOG.info("Processing Round %d", round_num)

        round_dir = M25_DIR / f"round_{round_num:04d}"
        if not round_dir.exists():
            LOG.warning("Skipping round %d: directory not found", round_num)
            continue

        # In co-evolution, candidate is Team B, opponent_A is Team A
        team_b_candidate = round_dir / "candidate.injected.cpp"
        team_a_opponent = round_dir / "opponent_A.cpp"

        if not team_b_candidate.exists() or not team_a_opponent.exists():
            LOG.warning("Skipping round %d: missing source files", round_num)
            continue

        # Load journal entry to get tactical details
        try:
            entry = load_journal_entry(round_num)
            fitness = entry.get("fitness", 0.0)
            tactic = entry.get("hypothesis_tested", "Unknown tactic")[:60]
            LOG.info("Round %d: fitness=%.3f, tactic=%s", round_num, fitness, tactic)
        except Exception as e:
            LOG.warning("Could not load journal entry for round %d: %s", round_num, e)

        # Generate trace and video
        trace_path = VID_DIR / f"trace_round{round_num:02d}.jsonl"
        video_path = VID_DIR / f"m25_round{round_num:02d}_{round_num}.mp4"

        if not trace_path.exists():
            generate_trace(
                team_a_path=team_a_opponent,
                team_b_path=team_b_candidate,
                trace_path=trace_path,
                seed=42,
            )
            LOG.info("✓ Trace generated: %s", trace_path)
        else:
            LOG.info("✓ Trace already exists: %s", trace_path)

        if not video_path.exists():
            generate_video(trace_path, video_path, intro_text)
            LOG.info("✓ Video generated: %s", video_path)
        else:
            LOG.info("✓ Video already exists: %s", video_path)

    LOG.info("=" * 60)
    LOG.info("✓ All M25 videos generated successfully")
    LOG.info("Output directory: %s", VID_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
