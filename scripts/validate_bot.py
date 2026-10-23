#!/usr/bin/env python3
"""Validate a bot and write its report card.

Checks, in order, stopping at the first that fails:

1. **Lint**: ``lint_ai_tokens`` plus the bot rules in ``bots/RULES.md``
   (includes, namespace, entry point, no mutable globals or statics, no
   randomness or clocks), on the comment-stripped source.
2. **Guards**: ``inject_guards`` accepts the source.
3. **Build and play** in the canonical eval container (never on the host):
   the bot plays ``pursuit_v1`` and ``cluster_v1``, each on ``--seeds`` seeds
   with both side assignments (default 1,000 seeds: 2,000 matches per
   baseline). Invalid matches (guard or compile failure, crash, timeout)
   are losses for the bot.

Scores are from the bot's side: +1 win, -1 loss, 0 draw. Survivor
differential is the bot's surviving drones minus the opponent's at the end
(an invalid match counts as minus the team size). The bot qualifies as R1
(bots/generation/PROTOCOL.md) if its mean score is at least 0.8 against each
baseline.

Writes to ``--out``: ``card.json``, ``card.md`` and ``matches.jsonl`` (one
row per match, without wall-clock time, so two bots that play identically
give identical files). ``--build-only`` stops after building (it plays one
match to compile in the container and records nothing about it); its card
carries only the lint, guard and build results and the error text a repair
request would send.

Exit code: 0 valid, 1 invalid (lint, guard or build failure), 2 usage error.

    python scripts/validate_bot.py bots/template.cpp --out /tmp/card
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fitness  # noqa: E402
import inject_guards  # noqa: E402
import lint_ai_tokens  # noqa: E402
from strip_comments import StripError, strip_comments  # noqa: E402

BASELINES = {
    "pursuit_v1": REPO_ROOT / "src" / "baselines" / "pursuit_v1.cpp",
    "cluster_v1": REPO_ROOT / "src" / "baselines" / "cluster_v1.cpp",
}
# Seed block for bot validation (bots/generation/PROTOCOL.md, "Seeds").
DEFAULT_SEED_BASE = 7_100_000
DEFAULT_SEEDS = 1000
DEFAULT_DRONES = 10
R1_THRESHOLD = 0.8
ERROR_TAIL = 4000  # characters of error text kept (and sent in a repair)

ALLOWED_INCLUDES = (
    '"../ai_abi.h"',
    '"../types.h"',
    "<cmath>",
    "<cfloat>",
    "<climits>",
    "<cstdint>",
    "<cstddef>",
)
_BANNED = (
    (r"\b(?:rand|srand|rand_r|drand48|random)\s*\(", "randomness"),
    (r"\brandom_device\b|\bmt19937|\bdefault_random_engine\b", "randomness"),
    (r"\b(?:time|clock|gettimeofday|clock_gettime)\s*\(|\bchrono\b", "clock or time"),
    (r"\bgetenv\s*\(", "environment access"),
    (r"\bthread_local\b", "thread_local state"),
    (r"\bgoto\b", "goto"),
    (r"\bTeamA\b|\bTeamB\b", "names TeamA/TeamB instead of TEAM_NS_PLACEHOLDER"),
    (r"\bvirtual\b", "virtual functions (plain data only)"),
    (r"\b(?:throw|try|catch)\b", "exceptions"),
)
_STATIC_VAR = re.compile(
    r"\bstatic\s+(?!const\b|constexpr\b|inline\b)[\w:<>,\s\*&]*?\b\w+\s*(?:\[[^\]]*\]\s*)*(?:=|;|\{)"
)
_NS_OPEN = re.compile(r"(?:^|\s)(?:inline\s+)?namespace(?:\s+[\w:]+)?$")
_NS_SKIP = re.compile(r"^(?:using|typedef|struct|class|enum|union|template|static_assert)\b")


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def _namespace_scope_statements(code: str) -> list[str]:
    """Statements that sit directly in a namespace (not in a function or type)."""
    code = "\n".join(
        line for line in code.splitlines() if not line.lstrip().startswith(("#", "//"))
    )
    stack: list[bool] = []  # True for a namespace brace
    statements: list[str] = []
    buf: list[str] = []
    for c in code:
        at_ns_scope = all(stack)
        if c == "{":
            head = "".join(buf).strip()
            stack.append(bool(_NS_OPEN.search(head)))
            buf = []
        elif c == "}":
            if stack:
                stack.pop()
            buf = []
        elif c == ";":
            if at_ns_scope:
                statements.append(" ".join("".join(buf).split()))
            buf = []
        elif at_ns_scope:
            buf.append(c)
    return statements


def check_rules(source: str) -> list[str]:
    """Bot-rule violations in ``source`` (bots/RULES.md); empty when clean."""
    try:
        code = strip_comments(source)
    except StripError as exc:
        return [f"cannot parse: {exc}"]
    problems: list[str] = []
    first = source.splitlines()[0] if source else ""
    if first.strip() != "// SPDX-License-Identifier: MIT":
        problems.append("first line is not `// SPDX-License-Identifier: MIT`")
    for m in re.finditer(r"^\s*#\s*include\s*(\S+)", code, re.MULTILINE):
        if m.group(1) not in ALLOWED_INCLUDES:
            problems.append(f"include not allowed: {m.group(1)}")
    if len(re.findall(r"\bnamespace\s+TEAM_NS_PLACEHOLDER\b", code)) != 1:
        problems.append("needs exactly one `namespace TEAM_NS_PLACEHOLDER { ... }`")
    if len(re.findall(r"\bvoid\s+drone_ai\s*\(", code)) != 1:
        problems.append("needs exactly one definition of `void drone_ai(...)`")
    elif not re.search(r"#pragma\s+acc\s+routine\s+seq\s*\n\s*void\s+drone_ai\s*\(", code):
        problems.append("`#pragma acc routine seq` must directly precede drone_ai")
    for pattern, reason in _BANNED:
        for m in re.finditer(pattern, code):
            problems.append(f"{reason}: `{m.group(0).strip()}`")
    for m in _STATIC_VAR.finditer(code):
        problems.append(f"mutable static variable: `{' '.join(m.group(0).split())}`")
    for stmt in _namespace_scope_statements(code):
        if not stmt or _NS_SKIP.match(stmt) or re.search(r"\b(?:const|constexpr)\b", stmt):
            continue
        if "(" in stmt.split("=", 1)[0]:
            continue  # a function declaration
        problems.append(f"mutable global variable: `{stmt}`")
    return problems


def lint(path: Path) -> list[str]:
    """``lint_ai_tokens`` violations plus bot-rule violations."""
    problems = [
        f"line {line}: {reason} (`{text}`)" for line, text, reason in lint_ai_tokens.scan_file(path)
    ]
    return problems + check_rules(path.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------


def _bot_row(baseline: str, side: str, row: dict[str, Any], drones: int) -> dict[str, Any]:
    invalid = row["outcome"] in fitness.INVALID_OUTCOMES
    own, opp = (row["a_alive"], row["b_alive"]) if side == "A" else (row["b_alive"], row["a_alive"])
    return {
        "baseline": baseline,
        "bot_side": side,
        "seed": row["seed"],
        "outcome": row["outcome"],
        "score": row["score"] if side == "A" else -row["score"],
        "survivor_diff": -drones if invalid else own - opp,
        "ticks": row["ticks"],
        "return_code": row["return_code"],
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, mean score and mean survivor differential of bot-side rows."""
    n = len(rows)
    invalid: dict[str, int] = {}
    for r in rows:
        if r["outcome"] in fitness.INVALID_OUTCOMES:
            invalid[r["outcome"]] = invalid.get(r["outcome"], 0) + 1
    return {
        "matches": n,
        "wins": sum(1 for r in rows if r["score"] > 0),
        "losses": sum(1 for r in rows if r["score"] < 0),
        "draws": sum(1 for r in rows if r["score"] == 0),
        "invalid": invalid,
        "mean_score": sum(r["score"] for r in rows) / n if n else 0.0,
        "mean_survivor_diff": sum(r["survivor_diff"] for r in rows) / n if n else 0.0,
    }


def _play(
    bot: Path,
    baseline: Path,
    side: str,
    *,
    seeds: int,
    seed_base: int,
    drones: int,
    image: str | None,
    workers: int | None,
) -> fitness.FitnessResult:
    a, b = (bot, baseline) if side == "A" else (baseline, bot)
    return fitness.evaluate_fitness(
        a,
        b,
        n_matches=seeds,
        seed_base=seed_base,
        under_test=side,
        backend="sandbox",
        image=image,
        workers=workers,
        drones_a=drones,
        drones_b=drones,
    )


def validate(
    bot: Path,
    out: Path,
    *,
    seeds: int = DEFAULT_SEEDS,
    seed_base: int = DEFAULT_SEED_BASE,
    drones: int = DEFAULT_DRONES,
    build_only: bool = False,
    image: str | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """Run the checks and write the card; return the card."""
    out.mkdir(parents=True, exist_ok=True)
    source = bot.read_text(encoding="utf-8", errors="replace")
    card: dict[str, Any] = {
        "bot": str(bot),
        "sha256": hashlib.sha256(bot.read_bytes()).hexdigest(),
        "validated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_only": build_only,
        "lint": {"ok": True, "problems": []},
        "guards": {"ok": None, "error": None},
        "build": {"ok": None, "error": None},
        "valid": False,
        "error_text": None,
    }

    problems = lint(bot)
    if problems:
        card["lint"] = {"ok": False, "problems": problems}
        card["error_text"] = "\n".join(problems)[-ERROR_TAIL:]
        return _write(out, card, [])

    try:
        inject_guards.inject(source)
        card["guards"]["ok"] = True
    except (inject_guards.InjectorError, ValueError) as exc:  # ValueError: unparseable loop
        card["guards"] = {"ok": False, "error": str(exc)[-ERROR_TAIL:]}
        card["error_text"] = card["guards"]["error"]
        return _write(out, card, [])

    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    platform: dict[str, Any] = {}
    try:
        if build_only:
            _play(bot, BASELINES["pursuit_v1"], "A", seeds=1, seed_base=seed_base,
                  drones=drones, image=image, workers=1)  # fmt: skip
        else:
            for name, path in BASELINES.items():
                by_side: dict[str, list[dict[str, Any]]] = {}
                for side in ("A", "B"):
                    res = _play(bot, path, side, seeds=seeds, seed_base=seed_base,
                                drones=drones, image=image, workers=workers)  # fmt: skip
                    platform = res.platform
                    by_side[side] = [_bot_row(name, side, r, drones) for r in res.per_match]
                    rows.extend(by_side[side])
                summary = summarise(by_side["A"] + by_side["B"])
                summary["by_side"] = {s: summarise(v) for s, v in by_side.items()}
                results[name] = summary
    except fitness.CompileError as exc:
        kind = "guards" if isinstance(exc, fitness.GuardError) else "build"
        card[kind] = {"ok": False, "error": str(exc)[-ERROR_TAIL:]}
        card["error_text"] = card[kind]["error"]
        return _write(out, card, [])

    card["build"]["ok"] = True
    card["valid"] = True
    if not build_only:
        card["seeds"] = {"base": seed_base, "count": seeds, "both_sides": True}
        card["drones"] = drones
        card["baselines"] = results
        card["qualifies_r1"] = all(r["mean_score"] >= R1_THRESHOLD for r in results.values())
        card["platform"] = platform
    return _write(out, card, rows)


def _write(out: Path, card: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    (out / "card.json").write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")
    with (out / "matches.jsonl").open("w") as fh:
        for r in sorted(rows, key=lambda r: (r["baseline"], r["bot_side"], r["seed"])):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    (out / "card.md").write_text(_markdown(card))
    return card


def _markdown(card: dict[str, Any]) -> str:
    lines = [
        f"# Report card: `{Path(card['bot']).name}`",
        "",
        f"- SHA-256: `{card['sha256']}`",
        f"- Lint: {'ok' if card['lint']['ok'] else 'FAILED'}",
        f"- Guards: {_status(card['guards']['ok'])}",
        f"- Build: {_status(card['build']['ok'])}",
        f"- Valid: {'yes' if card['valid'] else 'no'}",
    ]
    if card["error_text"]:
        lines += ["", "## Errors", "", "```", card["error_text"], "```"]
    if card.get("baselines"):
        s = card["seeds"]
        lines += [
            "",
            f"## Matches ({s['count']} seeds from {s['base']}, both sides, "
            f"{card['drones']} drones a side)",
            "",
            "| Baseline | Matches | W | L | D | Invalid | Mean score | Mean survivor diff |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name, r in card["baselines"].items():
            inv = ", ".join(f"{k} {v}" for k, v in sorted(r["invalid"].items())) or "0"
            lines.append(
                f"| {name} | {r['matches']} | {r['wins']} | {r['losses']} | {r['draws']} | "
                f"{inv} | {r['mean_score']:.3f} | {r['mean_survivor_diff']:.2f} |"
            )
        lines += [
            "",
            f"Qualifies as R1 (mean score >= {R1_THRESHOLD} against each): "
            f"{'yes' if card['qualifies_r1'] else 'no'}",
            f"Platform: {card['platform'].get('label', 'unknown')}",
        ]
    return "\n".join(lines) + "\n"


def _status(ok: bool | None) -> str:
    return "not run" if ok is None else ("ok" if ok else "FAILED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bot", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="directory for the card")
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument("--drones", type=int, default=DEFAULT_DRONES)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--image", default=None, help="eval image (default: the canonical one)")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    if not args.bot.is_file() or args.seeds < 1:
        parser.error("bot must be a file and --seeds at least 1")
    card = validate(
        args.bot,
        args.out,
        seeds=args.seeds,
        seed_base=args.seed_base,
        drones=args.drones,
        build_only=args.build_only,
        image=args.image,
        workers=args.workers,
    )
    print((args.out / "card.md").read_text(), end="")
    return 0 if card["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
