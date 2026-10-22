"""reports/legacy/LEGACY_LOOP.md must match a fresh render from the golden files."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import legacy_loop_report


def test_report_is_current() -> None:
    assert legacy_loop_report.main(["--check"]) == 0, (
        "run `python scripts/legacy_loop_report.py` to re-render the report"
    )
