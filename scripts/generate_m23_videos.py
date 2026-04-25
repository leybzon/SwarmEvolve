#!/usr/bin/env python3
"""Generate video visualizations for M23 experiment key generations."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

# Make sibling scripts importable
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import fitness as fitness_mod

REPO_ROOT = _HERE.parent
M23_DIR = REPO_ROOT / "data" / "runs" / "m23_sustained_50gen"
VIZ_DIR = M23_DIR / "visualizations"
OPPONENT = REPO_ROOT / "src" / "baselines" / "pursuit_v1.cpp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOG = logging.getLogger("generate_m23_videos")


def load_journal_entry(generation: int) -> dict:
    """Load journal entry for a specific generation."""
    journal_path = M23_DIR / "journal.jsonl"
    with journal_path.open("r") as f:
        for line in f:
            entry = json.loads(line)
            if entry["generation"] == generation:
                return entry
    raise ValueError(f"Generation {generation} not found in journal")


def _render_team_source(src_path: Path, namespace: str, dest: Path) -> None:
    """Render team source with namespace replacement (same as fitness module)."""
    text = src_path.read_text()
    # Replace placeholder namespace
    if "TEAM_NS_PLACEHOLDER" in text:
        text = text.replace("TEAM_NS_PLACEHOLDER", namespace)
    # Fix include paths
    text = text.replace('#include "../ai_abi.h"', '#include "ai_abi.h"')
    text = text.replace('#include "../types.h"', '#include "types.h"')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def generate_trace(candidate_path: Path, opponent_path: Path,
                   trace_path: Path, seed: int) -> None:
    """Generate a single match trace file by copying to src/ and compiling."""
    LOG.info("Generating trace for %s vs %s", candidate_path.stem, opponent_path.stem)

    # Create temp directories for team sources
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
        # Render team sources with namespace wrapping
        _render_team_source(candidate_path, "TeamA", team_a_dest)
        _render_team_source(opponent_path, "TeamB", team_b_dest)

        # Compile
        LOG.info("Compiling...")
        binary = REPO_ROOT / "swarmevolve"
        if binary.exists():
            binary.unlink()

        # Use Homebrew LLVM clang++ (same as fitness module)
        compiler = "/opt/homebrew/opt/llvm/bin/clang++"
        compile_cmd = [
            compiler, "-std=c++17", "-O3",
            f"-I{REPO_ROOT / 'src'}",  # Include path for headers
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
        LOG.info("Running match...")
        run_cmd = [
            str(binary),
            "--record", str(trace_path),
            "--seed", str(seed),
        ]
        result = subprocess.run(run_cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        # Note: binary returns rc=0 for Team A win, rc=1 for Team B win, rc=2 for DRAW
        # All are valid outcomes, not errors
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
    VIZ_DIR.mkdir(parents=True, exist_ok=True)

    # Key generations based on journal analysis
    key_generations = [0, 1, 4, 6, 10]

    LOG.info("Generating M23 visualizations for %d key generations", len(key_generations))

    for gen in key_generations:
        gen_dir = M23_DIR / f"gen_{gen:04d}"
        candidate = gen_dir / "candidate.injected.cpp"

        if not candidate.exists():
            LOG.warning("Skipping gen %d: no candidate.injected.cpp", gen)
            continue

        LOG.info("=== Generation %d ===", gen)

        # Load journal entry
        try:
            entry = load_journal_entry(gen)
            fitness = entry["fitness"]
            hypothesis = entry["hypothesis_tested"][:80]
        except (ValueError, KeyError) as e:
            LOG.warning("Skipping gen %d: failed to load journal entry: %s", gen, e)
            continue

        LOG.info("Fitness: %.3f", fitness)
        LOG.info("Hypothesis: %s", hypothesis)

        trace_path = VIZ_DIR / f"gen_{gen:04d}_trace.jsonl"
        video_path = VIZ_DIR / f"gen_{gen:04d}.mp4"

        # Generate trace
        try:
            generate_trace(candidate, OPPONENT, trace_path, seed=42 + gen)
        except Exception as e:
            LOG.error("Failed to generate trace for gen %d: %s", gen, e)
            continue

        # Create intro text
        intro_text = f"M23: Sustained Improvement Experiment\\nGeneration {gen}\\nFitness: {fitness:.3f}"

        # Generate video
        try:
            generate_video(trace_path, video_path, intro_text)
            LOG.info("✅ Created: %s", video_path)
        except Exception as e:
            LOG.error("Failed to generate video for gen %d: %s", gen, e)
            continue

    # Summary
    videos = list(VIZ_DIR.glob("*.mp4"))
    LOG.info("=== Summary ===")
    LOG.info("Created %d videos in %s", len(videos), VIZ_DIR)
    for video in sorted(videos):
        size_kb = video.stat().st_size // 1024
        LOG.info("  %s (%d KB)", video.name, size_kb)

    return 0


if __name__ == "__main__":
    sys.exit(main())
