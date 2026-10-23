"""The bot-generation protocol and design prompt are fixed and hint-free."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_design_prompt

GENERATION = REPO_ROOT / "bots" / "generation"
BRIEF = REPO_ROOT / "docs" / "paper" / "RESEARCH_BRIEF.md"
PROMPT = (GENERATION / "design_prompt.md").read_text()

# Tactic vocabulary that must not appear in the prompt's own text (the
# example programs' code is exempt: it is shown as written).
HINT_WORDS = (
    "flank", "kite", "kiting", "bait", "striker", "formation", "focus", "spread",
    "coordinat", "retreat", "surround", "estimat", "predict", "strateg", "tactic",
    "chase", "hunt", "ambush", "swarm behavio", "cohes",
)  # fmt: skip


def test_prompt_is_built_from_its_sources() -> None:
    assert build_design_prompt.main(["--check"]) == 0


def test_prompt_prose_has_no_tactic_hints() -> None:
    prose = re.sub(r"```\w*\n.*?\n```", "", PROMPT, flags=re.DOTALL)
    found = [w for w in HINT_WORDS if re.search(w, prose, re.IGNORECASE)]
    assert found == []
    assert "claude" not in PROMPT.lower()


@pytest.mark.parametrize("name", ["bots/RULES.md", "bots/template.cpp"])
def test_kit_files_have_no_tactic_hints(name: str) -> None:
    text = (REPO_ROOT / name).read_text()
    assert [w for w in HINT_WORDS if re.search(w, text, re.IGNORECASE)] == []


@pytest.mark.parametrize("name", ["PROTOCOL.md", "design_prompt.md"])
def test_brief_records_the_frozen_hash(name: str) -> None:
    digest = hashlib.sha256((GENERATION / name).read_bytes()).hexdigest()
    assert f"bots/generation/{name} sha256 {digest}" in BRIEF.read_text()


def test_protocol_authors_exclude_claude() -> None:
    text = (GENERATION / "PROTOCOL.md").read_text()
    assert "`gemini-4-argon`" in text and "`gpt-6.1-sol`" in text
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
    assert rows and not any("claude" in r.lower() for r in rows)
