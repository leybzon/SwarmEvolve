"""Determinism tests for the engine (M4).

Per SPECIFICATION §7.6 the engine must produce **per-platform byte-exact**
traces for identical inputs. These tests enforce that contract on the host
that runs them:

  1. 10 runs of ``pursuit_v1`` vs ``cluster_v1`` at seed=42 produce
     byte-identical traces (stronger than the M3 3-run smoke test).
  2. seed=42 and seed=43 produce different traces (sanity: ``--seed`` is
     wired through to the spawn PRNG, not ignored).
  3. The checked-in golden trace (``tests/fixtures/golden/...``) matches
     the pinned SHA-256. This is the canonical "platform baseline" — see
     ``IMPLEMENTATION_PLAN.md`` §6 for the "bless" policy when an engine
     change intentionally shifts the hash.

A mismatch on #3 means *either* the engine's observable behavior changed
(requires an explicit ``trace: update golden`` commit) *or* the host's
libc++ / rand distribution diverges from the reference build (macOS +
Homebrew LLVM clang 20). The 10x-stability and differ-on-seed tests
handle the latter case independently.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._build_helper import CXX, build_matchup, run_match

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++17 compiler available")

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "seed42_pursuit_vs_cluster.jsonl"

# Pin the reference SHA. If an engine change intentionally changes the
# trace, regenerate with:
#     ./build/golden/swarmevolve --seed 42 \
#         --record tests/fixtures/golden/seed42_pursuit_vs_cluster.jsonl
# and update this constant in a commit whose subject begins with
# "trace: update golden ...".
GOLDEN_SHA256 = "49584a04c6d4ea7211ec786ea8ecdd7261564a0280ff284093bea0cf32f88ea8"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ten_runs_identical_trace(tmp_path: Path) -> None:
    """10 runs at seed=42 produce byte-identical traces."""
    binary = build_matchup(tmp_path, "pursuit_v1.cpp", "cluster_v1.cpp")
    hashes: list[str] = []
    for i in range(10):
        trace = tmp_path / f"trace_{i}.jsonl"
        run_match(binary, seed=42, record=trace)
        hashes.append(_sha(trace))
    assert len(set(hashes)) == 1, f"non-deterministic traces: {hashes}"


def test_different_seeds_different_traces(tmp_path: Path) -> None:
    """seed=42 vs seed=43 produce different traces (--seed is wired up)."""
    binary = build_matchup(tmp_path, "pursuit_v1.cpp", "cluster_v1.cpp")
    t42 = tmp_path / "t42.jsonl"
    t43 = tmp_path / "t43.jsonl"
    run_match(binary, seed=42, record=t42)
    run_match(binary, seed=43, record=t43)
    assert _sha(t42) != _sha(t43), "different seeds produced identical traces — --seed not wired?"


def test_golden_trace_matches_pinned_sha() -> None:
    """The checked-in golden trace matches ``GOLDEN_SHA256``.

    This test does NOT require a compiler — it only re-hashes a file on disk.
    """
    assert GOLDEN_PATH.exists(), f"golden trace missing: {GOLDEN_PATH}"
    actual = _sha(GOLDEN_PATH)
    assert actual == GOLDEN_SHA256, (
        f"golden trace hash drift.\n"
        f"  expected: {GOLDEN_SHA256}\n"
        f"  actual:   {actual}\n"
        "If this is intentional, regenerate the golden trace and update\n"
        "GOLDEN_SHA256 in this file with a 'trace: update golden ...' commit."
    )


def test_engine_reproduces_golden_trace(tmp_path: Path) -> None:
    """A fresh build at seed=42 reproduces the checked-in golden byte-for-byte.

    This is the cross-check for ``test_golden_trace_matches_pinned_sha``:
    together they verify that (a) the pinned hash matches the stored file
    AND (b) the current engine+baselines produce the same bytes. If only
    (a) fires, the pin is stale against reality; if only (b) fires, the
    file was tampered with.
    """
    binary = build_matchup(tmp_path, "pursuit_v1.cpp", "cluster_v1.cpp")
    trace = tmp_path / "reproduce.jsonl"
    run_match(binary, seed=42, record=trace)
    assert _sha(trace) == GOLDEN_SHA256, (
        "fresh run did not reproduce the golden trace. Either the engine "
        "changed (bless with 'trace: update golden ...') or the host's libc++ "
        "distribution diverges from the reference (Homebrew LLVM clang 20 on "
        "macOS)."
    )
