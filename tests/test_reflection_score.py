"""Unit tests for the M17 reflection rubric scorer (``scripts/reflection_score.py``).

Coverage:

* Rule-based scorer heuristics on handcrafted journal entries
  (each axis at low/mid/high).
* Cohen's kappa utility: identity, perfect disagreement, known pairs.
* LLM judge with ``MockClient``: happy path + retry on malformed JSON
  (rewrite-budget behaviour) + fallback to rule scorer on budget
  exhaustion.
* CSV round-trip: schema matches ``RubricScore.csv_fieldnames()`` and
  the calibration sub-command computes kappa when matched by
  (track, model, seed, generation).
* CLI integration via ``main([...])``.

No network access and no C++ compiler are required.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import llm_client  # noqa: E402
import reflection_score as rs  # noqa: E402
import journal as journal_mod  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


_AAR_METRICS_OK = {
    "focus_fire_redundancy": 0.18,
    "mean_pairwise_distance_us": 42.0,
    "cooldown_uptime_us": 0.65,
    "win_margin": 0.25,
}


def _entry(
    generation: int = 0, *,
    status: str = "ok",
    verdict: str = "confirmed",
    hypothesis: str = "kite the opponent past 60 units",
    mech_exp: str | None = "holding range should keep our cooldown up",
    mech_obs: str | None = (
        "focus_fire_redundancy fell to 0.18 and cooldown uptime stayed "
        "near 0.65 while mean pairwise distance held around 42"
    ),
    advice: str | None = "if enemy within 40 units retreat 20; else engage",
    tags: list[str] | None = None,
    cited: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "generation": generation,
        "timestamp_utc": "2026-04-23T00:00:00Z",
        "parent_generation": max(generation - 1, 0) if generation else None,
        "track": "A",
        "model": "claude-opus-4-7",
        "seed": 42,
        "status": status,
        "fitness": 0.12 if status == "ok" else None,
        "fitness_delta": 0.04 if status == "ok" else None,
        "outcome_summary": "won by 3 drones",
        "hypothesis_tested": hypothesis,
        "mechanism_expected": mech_exp,
        "mechanism_observed": mech_obs,
        "verdict": verdict,
        "tactic_tags": tags if tags is not None else ["kite", "focus_fire"],
        "advice_to_future_self": advice,
        "aar_metrics_cited": cited if cited is not None else dict(_AAR_METRICS_OK),
        "validation": {
            "schema_valid": True, "metrics_match_aar": True, "rewrites": 0,
        },
    }


# --------------------------------------------------------------------------
# Rule-based scorer heuristics
# --------------------------------------------------------------------------


def test_rule_high_marks_on_rich_entry():
    e = _entry()
    score = rs.RuleJudge().score(e)
    assert score.causal_diagnosis == 5    # >= 2 AAR metrics cited
    assert score.counter_tactic_specificity == 5  # 2 tags + numeric advice
    assert score.abi_feasibility == 3     # no my_memory / MEM_SIZE refs
    assert score.judge_kind == "rule"
    assert score.judge_model == "rule-v1"
    assert score.prompt_tokens == 0 and score.completion_tokens == 0


def test_rule_low_marks_on_empty_entry():
    e = _entry(
        mech_exp="", mech_obs="",
        advice="", tags=[], cited={},
    )
    score = rs.RuleJudge().score(e)
    assert score.causal_diagnosis == 1
    assert score.counter_tactic_specificity == 1
    # No forbidden tokens, no enemy-cooldown → default level-3.
    assert score.abi_feasibility == 3


def test_rule_causal_diagnosis_partial_tiers():
    # Short prose, no citations → 2.
    e = _entry(mech_exp="lost", mech_obs="", cited={})
    assert rs.RuleJudge().score(e).causal_diagnosis == 2
    # Longer prose, no citations → 3.
    e = _entry(
        mech_exp="the enemy closed faster than anticipated, our drones did not spread",
        mech_obs="",
        cited={},
    )
    assert rs.RuleJudge().score(e).causal_diagnosis == 3
    # One citation → 4.
    e = _entry(cited={"focus_fire_redundancy": 0.4})
    assert rs.RuleJudge().score(e).causal_diagnosis == 4


def test_rule_abi_feasibility_forbidden_construct_is_1():
    e = _entry(
        advice="allocate a std::vector<float> of enemy ranges and sort it",
    )
    assert rs.RuleJudge().score(e).abi_feasibility == 1


def test_rule_abi_feasibility_enemy_cooldown_is_2():
    e = _entry(
        advice="wait until enemy cooldown hits zero then attack",
    )
    assert rs.RuleJudge().score(e).abi_feasibility == 2


def test_rule_abi_feasibility_concrete_plus_bounded_is_5():
    e = _entry(
        advice=(
            "store last target in my_memory[0]; iterate for (int i = 0; "
            "i < MAX_DRONES; ++i) to pick the nearest ally."
        ),
    )
    assert rs.RuleJudge().score(e).abi_feasibility == 5


def test_rule_counter_tactic_no_advice_is_1():
    e = _entry(advice=None)
    assert rs.RuleJudge().score(e).counter_tactic_specificity == 1


# --------------------------------------------------------------------------
# Cohen's kappa utility
# --------------------------------------------------------------------------


def test_kappa_identity_is_one():
    a = [1, 2, 3, 4, 5, 3, 2]
    assert rs.cohens_kappa(a, a) == pytest.approx(1.0)


def test_kappa_inverted_ordinal_is_negative():
    a = [1, 2, 3, 4, 5]
    b = [5, 4, 3, 2, 1]
    k = rs.cohens_kappa(a, b, weights="linear")
    # Perfectly reversed ordinals on a 1..5 scale under linear weights
    # is a well-known negative value.
    assert k < -0.4
    assert k > -0.6


def test_kappa_unweighted_exact_match_only():
    # 4/5 match, 1 disagreement.
    a = [1, 2, 3, 4, 5]
    b = [1, 2, 3, 4, 1]
    k = rs.cohens_kappa(a, b, weights="unweighted")
    # Observed agreement 0.8; expected random agreement << 0.8, so
    # kappa should be comfortably positive.
    assert 0.6 < k < 0.9


def test_kappa_rejects_length_mismatch():
    with pytest.raises(ValueError):
        rs.cohens_kappa([1, 2], [1])


def test_kappa_rejects_empty():
    with pytest.raises(ValueError):
        rs.cohens_kappa([], [])


def test_kappa_all_same_category_returns_one_on_agreement():
    assert rs.cohens_kappa([3, 3, 3], [3, 3, 3]) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# LLM judge with MockClient
# --------------------------------------------------------------------------


_RUBRIC_TEMPLATE = (
    "You are a judge.\n\nENTRY:\n```json\n{ENTRY_JSON}\n```\n"
    "ABI:\n```cpp\n{ABI_HEADER}\n```\n"
    "GEN: {GENERATION}  MODEL: {MODEL}  TRACK: {TRACK}\n"
)
_ABI_HEADER = "// ai_abi.h mock\nvoid drone_ai(...);"


def _make_mock_judge(*responses: str, model: str = "mock-judge-1"):
    client = llm_client.MockClient(
        responses=[
            llm_client.LLMResponse(text=r, model=model,
                                    prompt_tokens=7, completion_tokens=11)
            for r in responses
        ],
        model=model,
    )
    return rs.LLMJudge(
        client=client, rubric_template=_RUBRIC_TEMPLATE,
        abi_header=_ABI_HEADER,
    ), client


def test_llm_judge_happy_path():
    judge, client = _make_mock_judge(
        json.dumps({
            "causal_diagnosis": 4,
            "counter_tactic_specificity": 3,
            "abi_feasibility": 5,
            "justification": "mock happy",
        })
    )
    out = judge.score(_entry(generation=7))
    assert out.causal_diagnosis == 4
    assert out.counter_tactic_specificity == 3
    assert out.abi_feasibility == 5
    assert out.judge_kind == "mock"
    assert out.judge_model == "mock-judge-1"
    assert out.justification == "mock happy"
    assert out.prompt_tokens == 7
    assert out.completion_tokens == 11
    # Exactly one call consumed.
    assert len(client.calls) == 1


def test_llm_judge_strips_markdown_fence():
    judge, _ = _make_mock_judge(
        "```json\n"
        + json.dumps({
            "causal_diagnosis": 2, "counter_tactic_specificity": 2,
            "abi_feasibility": 3, "justification": "fenced",
        })
        + "\n```\n"
    )
    out = judge.score(_entry())
    assert out.causal_diagnosis == 2
    assert out.justification == "fenced"


def test_llm_judge_retries_on_malformed_then_succeeds():
    judge, client = _make_mock_judge(
        "not json at all",  # attempt 1
        json.dumps({
            "causal_diagnosis": 3, "counter_tactic_specificity": 4,
            "abi_feasibility": 4, "justification": "second try",
        }),  # attempt 2
    )
    out = judge.score(_entry())
    assert out.counter_tactic_specificity == 4
    # Two attempts consumed.
    assert len(client.calls) == 2


def test_llm_judge_out_of_range_triggers_retry():
    judge, client = _make_mock_judge(
        json.dumps({
            "causal_diagnosis": 7,  # out of [1,5]
            "counter_tactic_specificity": 3,
            "abi_feasibility": 3, "justification": "bad",
        }),
        json.dumps({
            "causal_diagnosis": 3, "counter_tactic_specificity": 3,
            "abi_feasibility": 3, "justification": "good",
        }),
    )
    out = judge.score(_entry())
    assert out.causal_diagnosis == 3
    assert len(client.calls) == 2


def test_llm_judge_exhausts_budget_raises():
    judge, _ = _make_mock_judge("garbage", "still garbage", "also garbage")
    with pytest.raises(rs.JudgeParseError):
        judge.score(_entry())


def test_score_journal_fallback_on_parse_failure():
    # Judge keeps emitting garbage → fallback rule scorer takes over.
    # Each entry consumes MAX_JUDGE_RETRIES + 1 = 3 responses before it
    # gives up and the fallback runs; need 3 * N_entries queued.
    judge, _ = _make_mock_judge(
        "x1", "x2", "x3",
        "y1", "y2", "y3",
    )
    fallback = rs.RuleJudge()
    entries = [_entry(generation=0), _entry(generation=1)]
    scores = rs.score_journal(
        entries=entries, judge=judge, fallback=fallback,
    )
    assert len(scores) == 2
    assert all(s.judge_kind == "rule" for s in scores)


def test_score_journal_no_fallback_raises():
    judge, _ = _make_mock_judge("garbage", "x", "y")
    with pytest.raises(rs.JudgeParseError):
        rs.score_journal(entries=[_entry()], judge=judge, fallback=None)


# --------------------------------------------------------------------------
# CSV round-trip
# --------------------------------------------------------------------------


def test_write_csv_matches_fieldnames(tmp_path: Path):
    scores = [
        rs.RuleJudge().score(_entry(generation=0)),
        rs.RuleJudge().score(_entry(generation=1)),
    ]
    out = tmp_path / "scores.csv"
    rs.write_csv(scores, out)

    with out.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == rs.RubricScore.csv_fieldnames()
        rows = list(reader)
    assert len(rows) == 2
    for row in rows:
        # All three axes present and in range.
        for ax in rs.AXES:
            v = int(row[ax])
            assert rs.SCORE_MIN <= v <= rs.SCORE_MAX
        assert row["judge_kind"] == "rule"
        assert row["judge_model"] == "rule-v1"


def test_write_csv_excludes_tokens(tmp_path: Path):
    scores = [rs.RuleJudge().score(_entry())]
    out = tmp_path / "s.csv"
    rs.write_csv(scores, out)
    text = out.read_text(encoding="utf-8")
    assert "prompt_tokens" not in text
    assert "completion_tokens" not in text


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def _write_score_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=rs.RubricScore.csv_fieldnames())
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _calib_row(gen: int, cd: int, ct: int, af: int,
               *, track="A", model="m", seed=1, kind="rule") -> dict:
    return {
        "generation": gen, "track": track, "model": model, "seed": seed,
        "causal_diagnosis": cd,
        "counter_tactic_specificity": ct,
        "abi_feasibility": af,
        "judge_kind": kind, "judge_model": "v1",
        "justification": "",
    }


def test_calibrate_csv_pair_computes_per_axis_kappa(tmp_path: Path):
    rows_h = [_calib_row(g, cd=(g % 5) + 1, ct=((g + 1) % 5) + 1,
                          af=((g + 2) % 5) + 1) for g in range(10)]
    rows_j = [dict(r) for r in rows_h]  # perfect agreement
    hpath = tmp_path / "human.csv"
    jpath = tmp_path / "judge.csv"
    _write_score_csv(hpath, rows_h)
    _write_score_csv(jpath, rows_j)

    out = rs.calibrate_csv_pair(hpath, jpath)
    assert out["_n_pairs"] == 10
    for ax in rs.AXES:
        assert out[ax] == pytest.approx(1.0)


def test_calibrate_rejects_too_few_matches(tmp_path: Path):
    hpath = tmp_path / "h.csv"
    jpath = tmp_path / "j.csv"
    # Only 2 rows → below the 5-pair minimum.
    _write_score_csv(hpath, [_calib_row(0, 3, 3, 3),
                              _calib_row(1, 3, 3, 3)])
    _write_score_csv(jpath, [_calib_row(0, 3, 3, 3),
                              _calib_row(1, 3, 3, 3)])
    with pytest.raises(ValueError):
        rs.calibrate_csv_pair(hpath, jpath)


def test_calibrate_matches_only_by_key(tmp_path: Path):
    # Human has gens 0..9 for seed=1; judge has same gens for seed=2.
    # Zero matches → raises.
    rows_h = [_calib_row(g, 3, 3, 3, seed=1) for g in range(10)]
    rows_j = [_calib_row(g, 3, 3, 3, seed=2) for g in range(10)]
    hpath = tmp_path / "h.csv"
    jpath = tmp_path / "j.csv"
    _write_score_csv(hpath, rows_h)
    _write_score_csv(jpath, rows_j)
    with pytest.raises(ValueError):
        rs.calibrate_csv_pair(hpath, jpath)


# --------------------------------------------------------------------------
# CLI integration (via main([...]))
# --------------------------------------------------------------------------


def _write_journal(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def test_cli_score_rule_writes_csv(tmp_path: Path):
    jp = tmp_path / "journal.jsonl"
    out = tmp_path / "scores.csv"
    _write_journal(jp, [_entry(generation=i) for i in range(3)])
    rc = rs.main([
        "score", str(jp), str(out), "--judge", "rule",
    ])
    assert rc == 0
    rows = rs.read_csv(out)
    assert len(rows) == 3
    assert rows[0]["judge_kind"] == "rule"


def test_cli_score_mock_judge_uses_response_dir(tmp_path: Path):
    jp = tmp_path / "journal.jsonl"
    _write_journal(jp, [_entry(generation=0), _entry(generation=1)])
    mr = tmp_path / "mocks"
    mr.mkdir()
    good = json.dumps({
        "causal_diagnosis": 4, "counter_tactic_specificity": 4,
        "abi_feasibility": 4, "justification": "cli mock",
    })
    (mr / "00.md").write_text(good)
    (mr / "01.md").write_text(good)
    out = tmp_path / "scores.csv"
    (tmp_path / "rubric.md").write_text(_RUBRIC_TEMPLATE)
    (tmp_path / "abi.h").write_text(_ABI_HEADER)
    rc = rs.main([
        "score", str(jp), str(out),
        "--judge", "mock",
        "--judge-model", "mock-cli-judge",
        "--mock-response-dir", str(mr),
        "--rubric-path", str(tmp_path / "rubric.md"),
        "--abi-path", str(tmp_path / "abi.h"),
    ])
    assert rc == 0
    rows = rs.read_csv(out)
    assert len(rows) == 2
    assert rows[0]["judge_kind"] == "mock"
    assert rows[0]["judge_model"] == "mock-cli-judge"
    for r in rows:
        for ax in rs.AXES:
            assert int(r[ax]) == 4


def test_cli_score_missing_rubric_raises(tmp_path: Path):
    """A missing --rubric-path should surface as FileNotFoundError."""
    jp = tmp_path / "journal.jsonl"
    _write_journal(jp, [_entry()])
    out = tmp_path / "scores.csv"
    with pytest.raises(FileNotFoundError):
        rs.main([
            "score", str(jp), str(out), "--judge", "rule",
            "--rubric-path", str(tmp_path / "nonexistent.md"),
        ])


def test_cli_calibrate_reports_kappa(tmp_path: Path, capsys: pytest.CaptureFixture):
    hpath = tmp_path / "h.csv"
    jpath = tmp_path / "j.csv"
    rows = [_calib_row(g, 3, 3, 3) for g in range(6)]
    _write_score_csv(hpath, rows)
    _write_score_csv(jpath, rows)
    rc = rs.main(["calibrate", str(hpath), str(jpath)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "n_pairs=6" in out
    for ax in rs.AXES:
        assert ax in out
    assert "[OK]" in out


def test_cli_calibrate_returns_10_on_low_kappa(tmp_path: Path):
    # Perfect disagreement on causal_diagnosis → negative kappa.
    rows_h = [_calib_row(g, cd=1, ct=3, af=3) for g in range(6)]
    rows_j = [_calib_row(g, cd=5, ct=3, af=3) for g in range(6)]
    hpath = tmp_path / "h.csv"
    jpath = tmp_path / "j.csv"
    _write_score_csv(hpath, rows_h)
    _write_score_csv(jpath, rows_j)
    rc = rs.main(["calibrate", str(hpath), str(jpath)])
    assert rc == 10


# --------------------------------------------------------------------------
# Compatibility with journal.read_entries (trailing partial line)
# --------------------------------------------------------------------------


def test_load_journal_tolerates_trailing_partial(tmp_path: Path):
    jp = tmp_path / "journal.jsonl"
    entries = [_entry(generation=i) for i in range(3)]
    _write_journal(jp, entries)
    # Simulate a kill-9 mid-write: append an unterminated object.
    with jp.open("a", encoding="utf-8") as fh:
        fh.write('{"schema_version":1,"generation":3')
    loaded = rs.load_journal(jp)
    assert len(loaded) == 3
    assert [e["generation"] for e in loaded] == [0, 1, 2]


def test_cli_sample_emits_template_and_entries(tmp_path: Path):
    """`sample` picks up to --size rows, stratified by (status, verdict),
    and writes a CSV template + per-row JSON sidecars."""
    # Build a journal with 20 entries across 4 (status, verdict) strata.
    jp = tmp_path / "journal.jsonl"
    rows = []
    for i in range(5):
        rows.append(_entry(generation=i, status="ok", verdict="confirmed"))
    for i in range(5, 10):
        rows.append(_entry(generation=i, status="ok", verdict="rejected"))
    for i in range(10, 15):
        rows.append(_entry(generation=i, status="compile_failed",
                           verdict="stalled"))
    for i in range(15, 20):
        rows.append(_entry(generation=i, status="ok", verdict="partial"))
    _write_journal(jp, rows)

    out = tmp_path / "calib"
    rc = rs.main([
        "sample", str(jp),
        "--out-dir", str(out),
        "--size", "8",
    ])
    assert rc == 0
    sample_csv = out / "sample.csv"
    assert sample_csv.is_file()
    sample_rows = list(csv.DictReader(sample_csv.open(encoding="utf-8")))
    assert len(sample_rows) == 8
    # Every axis column is blank (the human fills them in).
    for r in sample_rows:
        for ax in rs.AXES:
            assert r[ax] == ""
        assert r["judge_kind"] == "human"
        assert r["judge_model"] == "human-v1"
    # Per-row entry sidecars exist.
    entries_dir = out / "entries"
    sidecars = sorted(entries_dir.glob("*.json"))
    assert len(sidecars) == 8
    # One JSON is valid and carries the full entry.
    payload = json.loads(sidecars[0].read_text())
    assert payload["schema_version"] == 1


def test_cli_sample_is_deterministic(tmp_path: Path):
    """Running sample twice on the same journal yields the same rows."""
    jp = tmp_path / "journal.jsonl"
    rows = [_entry(generation=i, status="ok",
                   verdict="confirmed" if i % 2 == 0 else "rejected")
            for i in range(12)]
    _write_journal(jp, rows)

    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    assert rs.main(["sample", str(jp), "--out-dir", str(out1),
                     "--size", "6"]) == 0
    assert rs.main(["sample", str(jp), "--out-dir", str(out2),
                     "--size", "6"]) == 0
    s1 = (out1 / "sample.csv").read_text()
    s2 = (out2 / "sample.csv").read_text()
    assert s1 == s2


def test_cli_sample_handles_small_journal(tmp_path: Path):
    """If the journal has fewer rows than --size, sample returns all of
    them without crashing."""
    jp = tmp_path / "j.jsonl"
    _write_journal(jp, [_entry(generation=i) for i in range(3)])
    out = tmp_path / "c"
    rc = rs.main(["sample", str(jp), "--out-dir", str(out), "--size", "50"])
    assert rc == 0
    sample_rows = list(csv.DictReader(
        (out / "sample.csv").open(encoding="utf-8")))
    assert len(sample_rows) == 3


def test_cli_sample_missing_journal_errors(tmp_path: Path):
    out = tmp_path / "c"
    rc = rs.main([
        "sample", str(tmp_path / "nope.jsonl"),
        "--out-dir", str(out), "--size", "5",
    ])
    assert rc == 2


def test_constants_are_frozen():
    assert rs.SCHEMA_VERSION == 1
    assert rs.AXES == (
        "causal_diagnosis",
        "counter_tactic_specificity",
        "abi_feasibility",
    )
    assert rs.SCORE_MIN == 1 and rs.SCORE_MAX == 5
    assert rs.MAX_JUDGE_RETRIES == 2
