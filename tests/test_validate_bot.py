"""scripts/validate_bot.py: bot rules, scoring from the bot's side, report cards.

The ``docker`` tests (skipped without a runtime or the eval image) build and
play bots in the eval container.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sandbox
import validate_bot

TEMPLATE = (REPO_ROOT / "bots" / "template.cpp").read_text()


def _with(extra: str, where: str = "namespace {\n") -> str:
    return TEMPLATE.replace(where, where + extra + "\n", 1)


@pytest.mark.parametrize(
    "path", ["bots/template.cpp", "src/baselines/pursuit_v1.cpp", "src/baselines/cluster_v1.cpp"]
)
def test_template_and_baselines_follow_the_rules(path: str) -> None:
    assert validate_bot.lint(REPO_ROOT / path) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (TEMPLATE.replace("// SPDX-License-Identifier: MIT\n", ""), "SPDX"),
        (TEMPLATE.replace("#include <cmath>", "#include <vector>"), "include not allowed"),
        (TEMPLATE.replace("TEAM_NS_PLACEHOLDER", "TeamA"), "TEAM_NS_PLACEHOLDER"),
        (TEMPLATE.replace("#pragma acc routine seq\n", ""), "#pragma acc routine seq"),
        (_with("float g_state[4];"), "mutable global variable"),
        (_with("struct S { int a; } g_s;"), "mutable global variable"),
        (_with("static int counter = 0;"), "mutable static variable"),
        (_with("int r = rand();", "(void)n;\n"), "randomness"),
        (_with("long t = time(nullptr);", "(void)n;\n"), "clock or time"),
        (_with("thread_local int x = 0;"), "thread_local"),
        (TEMPLATE.replace("#pragma acc", "void drone_ai_unused();\n#pragma acc", 1), None),
    ],
)
def test_rule_violations(source: str, expected: str | None) -> None:
    problems = validate_bot.check_rules(source)
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


@pytest.mark.parametrize(
    "allowed", ["const float k = 1.0f;", "constexpr int K = 3;", "float helper(float x);"]
)
def test_constants_and_declarations_are_allowed(allowed: str) -> None:
    assert validate_bot.check_rules(_with(allowed)) == []


def test_rule_check_ignores_comments() -> None:
    assert validate_bot.check_rules(_with("// rand() and std::vector are fine here")) == []


def _row(outcome: str, score: float, a: int, b: int) -> dict[str, Any]:
    return {"seed": 1, "outcome": outcome, "score": score, "a_alive": a, "b_alive": b,
            "ticks": 9, "return_code": 0}  # fmt: skip


def test_rows_are_scored_from_the_bot_side() -> None:
    win_as_b = validate_bot._bot_row("x", "B", _row("B_WIN", -1.0, 0, 3), 10)
    assert (win_as_b["score"], win_as_b["survivor_diff"]) == (1.0, 3)
    crash_as_a = validate_bot._bot_row("x", "A", _row("CRASH", -1.0, 0, 0), 10)
    assert (crash_as_a["score"], crash_as_a["survivor_diff"]) == (-1.0, -10)
    s = validate_bot.summarise([win_as_b, crash_as_a])
    assert (s["wins"], s["losses"], s["invalid"], s["mean_score"]) == (1, 1, {"CRASH": 1}, 0.0)


def test_lint_failure_writes_a_card(tmp_path: Path) -> None:
    bot = tmp_path / "bot.cpp"
    bot.write_text(TEMPLATE.replace("#include <cmath>", "#include <vector>"))
    card = validate_bot.validate(bot, tmp_path / "card")
    assert not card["valid"] and not card["lint"]["ok"]
    assert "<vector>" in card["error_text"]
    assert json.loads((tmp_path / "card" / "card.json").read_text())["valid"] is False


def test_guard_failure_writes_a_card(tmp_path: Path) -> None:
    bot = tmp_path / "bot.cpp"
    bot.write_text(TEMPLATE.replace("(void)n;", "(void)n;\n    while (n < 0) n++;"))
    card = validate_bot.validate(bot, tmp_path / "card")
    assert card["lint"]["ok"] and card["guards"]["ok"] is False and not card["valid"]
    assert "single-statement bodies" in card["error_text"]


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------


def _sandbox_or_skip() -> None:
    try:
        rt = sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")
    if not sandbox.image_present(sandbox.DEFAULT_IMAGE, runtime=rt):
        pytest.skip(f"eval image '{sandbox.DEFAULT_IMAGE}' not found; run `make docker-build`")


@pytest.fixture()
def home_tmp() -> Iterator[Path]:
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="validate_bot_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.docker
def test_template_card_in_the_container(home_tmp: Path) -> None:
    _sandbox_or_skip()
    card = validate_bot.validate(REPO_ROOT / "bots" / "template.cpp", home_tmp / "card", seeds=20)
    assert card["valid"] and card["qualifies_r1"] is False
    for name in ("pursuit_v1", "cluster_v1"):
        r = card["baselines"][name]
        assert r["matches"] == 40 and r["by_side"]["A"]["matches"] == 20
        assert r["mean_score"] == -1.0  # a bot that never attacks loses every match
    assert card["platform"]["image_id"].startswith("sha256:")
    rows = (home_tmp / "card" / "matches.jsonl").read_text().splitlines()
    assert len(rows) == 80 and "wall_ms" not in rows[0]


@pytest.mark.docker
def test_mirror_is_symmetric_in_the_container(home_tmp: Path) -> None:
    _sandbox_or_skip()
    bot = REPO_ROOT / "src" / "baselines" / "cluster_v1.cpp"
    card = validate_bot.validate(bot, home_tmp / "card", seeds=50)
    mirror = card["baselines"]["cluster_v1"]
    assert mirror["mean_score"] == 0.0 and mirror["wins"] == mirror["losses"]


@pytest.mark.docker
def test_compile_error_text_is_returned(home_tmp: Path) -> None:
    _sandbox_or_skip()
    bot = home_tmp / "broken.cpp"
    bot.write_text(TEMPLATE.replace("out_action->target_id = -1;", "out_action->target_id = nope;"))
    card = validate_bot.validate(bot, home_tmp / "card", build_only=True)
    assert not card["valid"] and card["build"]["ok"] is False
    assert "nope" in card["error_text"] and "was not declared" in card["error_text"]
    assert "build_only" in card and "baselines" not in card
