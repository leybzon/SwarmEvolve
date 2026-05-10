"""Tests for scripts/reproduce.py (M20).

Pure-Python unit tests cover the fingerprint normalisation (unstable
keys are scrubbed, stable keys are kept). The full byte-identical
integration test that invokes ``track_a.main`` against the MockClient
is gated behind ``needs_cxx`` because it compiles C++ candidates.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_TESTS = _REPO_ROOT / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from _build_helper import CXX  # type: ignore

# ``scripts/reproduce.py`` isn't a package member; import by path.
_REPRO = _SCRIPTS / "reproduce.py"
_spec = importlib.util.spec_from_file_location("reproduce", _REPRO)
reproduce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reproduce)  # type: ignore[union-attr]


needs_cxx = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


# ---------------------------------------------------------------------------
# Fingerprint normalisation
# ---------------------------------------------------------------------------


def test_normalise_history_row_drops_wall_seconds():
    row = {"generation": 0, "wall_seconds": 3.14, "accepted": True}
    out = reproduce._normalise_history_row(row)
    assert "wall_seconds" not in out
    assert out == {"generation": 0, "accepted": True}


def test_normalise_fitness_drops_unstable_fields():
    fit = {
        "mean": 0.5,
        "stdev": 0.1,
        "wall_seconds": 2.7,
        "team_a_path": "/tmp/xyz/a.cpp",
        "team_b_path": "/tmp/xyz/b.cpp",
        "compiler": "clang++-17@deadbeef",
        "per_match": [
            {"seed": 1, "score": 0.5, "wall_ms": 12},
            {"seed": 2, "score": 0.7, "wall_ms": 34},
        ],
    }
    out = reproduce._normalise_fitness(fit)
    assert out is not None
    assert "wall_seconds" not in out
    assert "team_a_path" not in out
    assert "team_b_path" not in out
    assert "compiler" not in out
    assert out["mean"] == 0.5
    assert out["stdev"] == 0.1
    # per_match entries must keep seed/score but drop wall_ms.
    for m in out["per_match"]:
        assert "wall_ms" not in m
        assert "seed" in m and "score" in m


def test_normalise_fitness_none_passthrough():
    assert reproduce._normalise_fitness(None) is None
    assert reproduce._normalise_fitness({}) == {}


def test_normalise_state_scrubs_run_id_and_timing():
    state = {
        "run_id": "abc123",
        "wall_start_iso": "2026-01-01T00:00:00Z",
        "champion_generation": 1,
        "champion_sha256": "deadbeef",
        "history": [
            {"generation": 0, "wall_seconds": 1.0, "accepted": True},
            {"generation": 1, "wall_seconds": 2.0, "accepted": False},
        ],
        "champion_fitness": {"mean": 0.8, "wall_seconds": 5.0},
        "config": {
            "client": "mock",
            "mock_response_paths": ["/abs/path/000.md", "/abs/path/001.md"],
        },
    }
    out = reproduce._normalise_state(state)
    assert "run_id" not in out
    assert "wall_start_iso" not in out
    assert out["champion_generation"] == 1
    assert out["champion_sha256"] == "deadbeef"
    for row in out["history"]:
        assert "wall_seconds" not in row
    assert out["champion_fitness"] == {"mean": 0.8}
    # Absolute mock response paths must be stripped from config.
    assert "mock_response_paths" not in out["config"]
    assert out["config"]["client"] == "mock"


def test_normalise_manifest_drops_run_dir():
    manifest = {
        "track": "A",
        "lineages": [
            {"seed": 1, "run_dir": "/tmp/run_a/seed1", "tokens_total": 1234},
            {"seed": 2, "run_dir": "/tmp/run_b/seed2", "tokens_total": 5678},
        ],
        "opponent": "/tmp/weights/opponent.cpp",
        "initial_seed_ai": "/tmp/weights/seed.cpp",
    }
    out = reproduce._normalise_manifest(manifest)
    assert out["track"] == "A"
    for lin in out["lineages"]:
        assert "run_dir" not in lin
    assert "opponent" not in out
    assert "initial_seed_ai" not in out


def test_fingerprint_digest_is_stable():
    fp1 = {"states": {"a": {"x": 1}}, "champions": {"a": "d"}, "manifest": None}
    fp2 = {"manifest": None, "champions": {"a": "d"}, "states": {"a": {"x": 1}}}
    # sort_keys=True => insertion order in the input dict must not matter.
    assert reproduce.fingerprint_digest(fp1) == reproduce.fingerprint_digest(fp2)


def test_fingerprint_digest_detects_divergence():
    fp1 = {"states": {"a": {"x": 1}}, "champions": {}, "manifest": None}
    fp2 = {"states": {"a": {"x": 2}}, "champions": {}, "manifest": None}
    assert reproduce.fingerprint_digest(fp1) != reproduce.fingerprint_digest(fp2)


# ---------------------------------------------------------------------------
# _materialise_mock_dir
# ---------------------------------------------------------------------------


def test_materialise_mock_dir_expands_template(tmp_path: Path) -> None:
    template_dir = tmp_path / "tmpl"
    template_dir.mkdir()
    (template_dir / "response.md").write_text("hello world", encoding="utf-8")
    staging = tmp_path / "staging"

    out = reproduce._materialise_mock_dir(template_dir, staging, count=5)
    assert out == staging
    files = sorted(p.name for p in staging.iterdir())
    assert files == ["000.md", "001.md", "002.md", "003.md", "004.md"]
    for p in staging.iterdir():
        assert p.read_text(encoding="utf-8") == "hello world"


def test_materialise_mock_dir_missing_template_errors(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    staging = tmp_path / "staging"
    with pytest.raises(FileNotFoundError):
        reproduce._materialise_mock_dir(empty, staging, count=3)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_parser_defaults():
    parser = reproduce.build_parser()
    args = parser.parse_args(
        [
            "--mini-config",
            "/tmp/cfg",
            "--out-root",
            "/tmp/out",
        ]
    )
    assert args.mini_config == Path("/tmp/cfg")
    assert args.out_root == Path("/tmp/out")
    assert args.seeds == "1,2"
    assert args.generations == 2
    assert args.n_matches == 3


def test_cli_rejects_nonexistent_mini_config(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    rc = reproduce.main(
        [
            "--mini-config",
            str(missing),
            "--out-root",
            str(tmp_path / "out"),
        ]
    )
    assert rc == reproduce.EXIT_USAGE


# ---------------------------------------------------------------------------
# fingerprint_run: round-trip on a hand-built track tree
# ---------------------------------------------------------------------------


def _write_state_file(path: Path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")


def test_fingerprint_run_gathers_states_champions_manifest(tmp_path: Path) -> None:
    # Two lineages, one champion each, plus a manifest at the root.
    _write_state_file(
        tmp_path / "seed1" / "state.json",
        run_id="s1",
        champion_sha256="deadbeef",
        history=[{"generation": 0, "wall_seconds": 1.0}],
    )
    _write_state_file(
        tmp_path / "seed2" / "state.json",
        run_id="s2",
        champion_sha256="cafebabe",
        history=[{"generation": 0, "wall_seconds": 2.0}],
    )
    ch1 = tmp_path / "seed1" / "champions" / "gen_0000.cpp"
    ch1.parent.mkdir(parents=True)
    ch1.write_text("// a", encoding="utf-8")
    ch2 = tmp_path / "seed2" / "champions" / "gen_0000.cpp"
    ch2.parent.mkdir(parents=True)
    ch2.write_text("// b", encoding="utf-8")
    manifest_path = tmp_path / "track_a_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "track": "A",
                "lineages": [
                    {"seed": 1, "run_dir": "/abs/run/seed1"},
                    {"seed": 2, "run_dir": "/abs/run/seed2"},
                ],
            }
        ),
        encoding="utf-8",
    )

    fp = reproduce.fingerprint_run(tmp_path)
    assert set(fp["states"].keys()) == {"seed1/state.json", "seed2/state.json"}
    assert set(fp["champions"].keys()) == {
        "seed1/champions/gen_0000.cpp",
        "seed2/champions/gen_0000.cpp",
    }
    assert fp["manifest"] is not None
    for lin in fp["manifest"]["lineages"]:
        assert "run_dir" not in lin
    # run_id and wall_seconds must be gone.
    for st in fp["states"].values():
        assert "run_id" not in st
        for row in st["history"]:
            assert "wall_seconds" not in row


# ---------------------------------------------------------------------------
# Integration: byte-identical smoke test (slow; needs_cxx)
# ---------------------------------------------------------------------------


@needs_cxx
def test_reproduce_mini_track_a_byte_identical(tmp_path: Path) -> None:
    """End-to-end: the committed ``mini_track_a`` fixture must produce
    identical fingerprints across two back-to-back track_a runs.

    Small config (1 seed, 1 generation, 2 matches) keeps wall time
    bounded for per-PR CI; the repo-level CI job uses the larger
    default (2 seeds, 2 generations, 3 matches).
    """
    mini_config = _REPO_ROOT / "scripts" / "ci_fixtures" / "mini_track_a"
    out_root = tmp_path / "smoke"
    rc = reproduce.main(
        [
            "--mini-config",
            str(mini_config),
            "--out-root",
            str(out_root),
            "--seeds",
            "1",
            "--generations",
            "1",
            "--n-matches",
            "2",
        ]
    )
    assert rc == reproduce.EXIT_OK
    assert (out_root / "reproduce.ok").is_file()
