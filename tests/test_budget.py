"""Tests for scripts/tracks/_budget.py (M20).

Pure-Python unit tests: materialise synthetic ``state.json`` payloads
under a scratch tree and assert the cap enforcement behaviour
(iteration order, summation, trip, no-op at cap=0).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tracks._budget import BudgetExceeded, TokenBudget


def _write_state(path: Path, tokens_in: int, tokens_out: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": path.parent.name,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "history": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_empty_track_root_zero(tmp_path: Path) -> None:
    budget = TokenBudget(max_tokens=1000)
    assert budget.total(tmp_path) == 0
    # No state files => check returns 0 and does not raise.
    assert budget.check(tmp_path) == 0


def test_sum_spans_nested_lineages(tmp_path: Path) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 100, 200)
    _write_state(tmp_path / "seed2" / "gen0000" / "state.json", 50, 75)
    _write_state(tmp_path / "seed3" / "coevo" / "a" / "state.json", 10, 5)
    budget = TokenBudget(max_tokens=0)
    # 100+200 + 50+75 + 10+5 = 440
    assert budget.total(tmp_path) == 440


def test_cap_zero_disables_enforcement(tmp_path: Path) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 10_000_000, 0)
    budget = TokenBudget(max_tokens=0)
    # Even 10M tokens is fine when cap=0.
    assert budget.check(tmp_path) == 10_000_000


def test_cap_not_exceeded(tmp_path: Path) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 300, 200)
    budget = TokenBudget(max_tokens=1000)
    assert budget.check(tmp_path) == 500


def test_cap_exceeded_raises(tmp_path: Path) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 700, 400)
    budget = TokenBudget(max_tokens=1000)
    with pytest.raises(BudgetExceeded) as exc:
        budget.check(tmp_path)
    assert exc.value.max_tokens == 1000
    assert exc.value.total_tokens == 1100
    assert exc.value.track_root == tmp_path


def test_enforce_logs_utilisation(tmp_path: Path, caplog) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 200, 300)
    budget = TokenBudget(max_tokens=1000)
    logger = logging.getLogger("test.budget.enforce")
    with caplog.at_level(logging.INFO, logger=logger.name):
        total = budget.enforce(tmp_path, logger=logger)
    assert total == 500
    msgs = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert any("500" in m and "1000" in m for m in msgs)


def test_enforce_cap_zero_does_not_log(tmp_path: Path, caplog) -> None:
    _write_state(tmp_path / "seed1" / "state.json", 100, 50)
    budget = TokenBudget(max_tokens=0)
    logger = logging.getLogger("test.budget.zero")
    with caplog.at_level(logging.INFO, logger=logger.name):
        total = budget.enforce(tmp_path, logger=logger)
    assert total == 150
    # With cap=0 the helper must stay silent about utilisation.
    msgs = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert not msgs


def test_iter_states_is_sorted(tmp_path: Path) -> None:
    # Create in intentionally-non-sorted insertion order.
    _write_state(tmp_path / "zeta" / "state.json", 1, 0)
    _write_state(tmp_path / "alpha" / "state.json", 2, 0)
    _write_state(tmp_path / "mu" / "state.json", 3, 0)
    budget = TokenBudget(max_tokens=0)
    # Confirm the iteration yields tokens in lex order: alpha, mu, zeta.
    vals = [st["tokens_input"] for st in budget.iter_states(tmp_path)]
    assert vals == [2, 3, 1]


def test_tokens_missing_keys_treated_as_zero(tmp_path: Path) -> None:
    path = tmp_path / "seed1" / "state.json"
    path.parent.mkdir(parents=True)
    # No tokens_input/tokens_output keys at all.
    path.write_text(json.dumps({"run_id": "x", "history": []}), encoding="utf-8")
    budget = TokenBudget(max_tokens=1000)
    assert budget.check(tmp_path) == 0


def test_tokens_null_treated_as_zero(tmp_path: Path) -> None:
    path = tmp_path / "seed1" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "tokens_input": None,
                "tokens_output": None,
            }
        ),
        encoding="utf-8",
    )
    budget = TokenBudget(max_tokens=1000)
    assert budget.check(tmp_path) == 0


def test_missing_track_root_returns_zero(tmp_path: Path) -> None:
    budget = TokenBudget(max_tokens=100)
    missing = tmp_path / "does_not_exist"
    # Should not raise; just report zero.
    assert budget.total(missing) == 0
    assert budget.check(missing) == 0
