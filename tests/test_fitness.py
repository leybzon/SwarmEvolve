"""Tests for :mod:`scripts.fitness` (M9).

The evaluator is compile-heavy; we run it at the smallest
``n_matches`` that still exercises each of its invariants (seed
determinism, score aggregation, bootstrap CI reproducibility,
worker partitioning). A single ``pursuit vs cluster`` smoke test
pins the stability guarantee: same inputs → byte-identical result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fitness
from _build_helper import CXX

BASELINES = REPO_ROOT / "src" / "baselines"

# Every integration test in this file needs a working compiler. Skip
# at collection time on CI images that don't have one.
pytestmark = pytest.mark.skipif(
    CXX is None,
    reason="no C++ compiler found on PATH",
)


# ---------------------------------------------------------------------------
# Unit tests for the bootstrap CI helper (no compile required, cheap).
# ---------------------------------------------------------------------------


def test_bootstrap_ci_is_deterministic_given_seed():
    """Same input + same ci_seed → identical CI. Reproducibility is a
    stronger guarantee than statistical validity here."""
    scores = [1.0, 1.0, 0.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 0.0]
    lo1, hi1 = fitness._bootstrap_ci(scores, iterations=500, ci_seed=42)
    lo2, hi2 = fitness._bootstrap_ci(scores, iterations=500, ci_seed=42)
    assert (lo1, hi1) == (lo2, hi2)


def test_bootstrap_ci_different_seeds_can_differ():
    """Sanity: changing ci_seed does something."""
    scores = [1.0, 1.0, 0.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 0.0]
    a = fitness._bootstrap_ci(scores, iterations=500, ci_seed=1)
    b = fitness._bootstrap_ci(scores, iterations=500, ci_seed=2)
    # They should at least sometimes differ — degenerate case is when
    # all scores are identical; guard that pathologically.
    assert a != b or all(s == scores[0] for s in scores)


def test_bootstrap_ci_rejects_single_sample():
    with pytest.raises(ValueError):
        fitness._bootstrap_ci([0.5], iterations=10, ci_seed=0)


def test_bootstrap_ci_brackets_mean():
    scores = [1.0] * 10
    lo, hi = fitness._bootstrap_ci(scores, iterations=200, ci_seed=0)
    # All-identical scores collapse the CI onto the mean.
    assert lo == 1.0
    assert hi == 1.0


# ---------------------------------------------------------------------------
# evaluate_fitness argument validation
# ---------------------------------------------------------------------------


def test_evaluate_rejects_missing_team_a(tmp_path):
    b = tmp_path / "b.cpp"
    b.write_text("//")
    with pytest.raises(FileNotFoundError):
        fitness.evaluate_fitness(
            tmp_path / "does_not_exist.cpp",
            b,
            n_matches=1,
            workers=1,
        )


def test_evaluate_rejects_zero_matches(tmp_path):
    a = tmp_path / "a.cpp"
    b = tmp_path / "b.cpp"
    a.write_text("//")
    b.write_text("//")
    with pytest.raises(ValueError):
        fitness.evaluate_fitness(a, b, n_matches=0, workers=1)


# ---------------------------------------------------------------------------
# Integration: short pursuit-vs-cluster run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def short_run(tmp_path_factory):
    """One reference evaluator result shared across stability tests.

    Module-scoped so we only pay the compile cost once.
    """
    scratch = tmp_path_factory.mktemp("fit_scratch")
    return fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=4,
        seed_base=0,
        workers=1,
        scratch_root=scratch,
    )


def test_short_run_populates_all_fields(short_run):
    r = short_run
    assert r.n_matches == 4
    assert r.seed_base == 0
    assert len(r.per_match) == 4
    # Outcomes sum must equal n_matches: every match classified.
    assert r.wins_a + r.wins_b + r.draws + r.invalid == 4
    # Sorted by seed.
    assert [m["seed"] for m in r.per_match] == [0, 1, 2, 3]
    for m in r.per_match:
        assert m["outcome"] in {"A_WIN", "B_WIN", "DRAW", "TIMEOUT", "CRASH"}
    # For the frozen baselines these should all be valid matches.
    assert r.invalid == 0


def test_short_run_ci_is_present_and_bracketing(short_run):
    # With 4 matches CI should exist and contain / equal the mean.
    assert short_run.ci_low is not None
    assert short_run.ci_high is not None
    assert short_run.ci_low <= short_run.mean <= short_run.ci_high


def test_same_inputs_produce_identical_result(tmp_path_factory):
    """Stability: the contract the evolutionary loop depends on."""
    s1 = tmp_path_factory.mktemp("fit_a")
    s2 = tmp_path_factory.mktemp("fit_b")
    r1 = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=3,
        seed_base=7,
        workers=1,
        scratch_root=s1,
    )
    r2 = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=3,
        seed_base=7,
        workers=1,
        scratch_root=s2,
    )
    # wall_seconds varies run-to-run; everything else must match.
    for field in ("wins_a", "wins_b", "draws", "invalid", "mean", "stdev", "ci_low", "ci_high"):
        assert getattr(r1, field) == getattr(r2, field), field
    # per_match is the strongest check: byte-identical except wall_ms.
    for m1, m2 in zip(r1.per_match, r2.per_match, strict=False):
        for key in ("seed", "outcome", "score", "ticks", "a_alive", "b_alive", "return_code"):
            assert m1[key] == m2[key], key


def test_workers_gt_1_produces_same_aggregates(tmp_path_factory):
    """Parallel worker path must not change the aggregate result."""
    s1 = tmp_path_factory.mktemp("fit_seq")
    s2 = tmp_path_factory.mktemp("fit_par")
    r_seq = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=4,
        seed_base=0,
        workers=1,
        scratch_root=s1,
    )
    r_par = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=4,
        seed_base=0,
        workers=2,
        scratch_root=s2,
    )
    for field in ("wins_a", "wins_b", "draws", "invalid", "mean", "stdev", "ci_low", "ci_high"):
        assert getattr(r_seq, field) == getattr(r_par, field), field


def test_self_play_mean_is_near_zero(tmp_path_factory):
    """pursuit vs pursuit should be (near-)balanced. The bound is
    deliberately loose because 4 matches is too small for a strict
    bound; 0.5 catches a regression that flips every outcome."""
    scratch = tmp_path_factory.mktemp("fit_self")
    r = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "pursuit_v1.cpp",
        n_matches=4,
        seed_base=0,
        workers=1,
        scratch_root=scratch,
    )
    assert abs(r.mean) <= 0.5, r
    # A healthy self-play pairing should register at least one draw
    # OR two-sided outcome splits.
    assert r.draws + min(r.wins_a, r.wins_b) >= 1


def test_to_dict_is_json_serialisable(short_run):
    import json

    s = json.dumps(short_run.to_dict(), sort_keys=True)
    # Round-trip for structural equality.
    round_tripped = json.loads(s)
    assert round_tripped["n_matches"] == short_run.n_matches
    assert len(round_tripped["per_match"]) == 4


def test_compile_error_propagates(tmp_path):
    """A broken AI must raise CompileError, not a silent zero fitness."""
    # Render a valid opponent ...
    b_src = (BASELINES / "cluster_v1.cpp").read_text().replace("TEAM_NS_PLACEHOLDER", "TeamB")
    b = tmp_path / "b.cpp"
    b.write_text(b_src)
    # ... and a candidate with a missing closing brace.
    a = tmp_path / "a.cpp"
    a.write_text(
        '#include "ai_abi.h"\n'
        '#include "types.h"\n'
        "namespace TeamA {\n"
        "// missing close brace on purpose\n"
        "void drone_ai(int, const GameParams*, const AllyState*,\n"
        "              const EnemyState*, const float[][MSG_SIZE],\n"
        "              float*, Action* out) { out->target_id = -1; }\n"
    )
    with pytest.raises(fitness.CompileError):
        fitness.evaluate_fitness(
            a,
            b,
            n_matches=1,
            workers=1,
            scratch_root=tmp_path / "scratch",
        )
