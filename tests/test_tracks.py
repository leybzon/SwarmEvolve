"""Tests for scripts/tracks/*.py (M19).

Pure-Python tests cover CLI surface, parser defaults, shared helpers,
manifest roundtrip, and schema validation. Integration tests that
invoke ``evolve.main`` with the deterministic ``MockClient`` require a
C++ compiler on PATH and are gated behind ``needs_cxx``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
# Also make ``tests.`` importable for the _build_helper shim.
_TESTS = _REPO_ROOT / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from _build_helper import CXX  # type: ignore  # noqa: E402

from tracks import _common  # noqa: E402
from tracks import track_a, track_b, track_c  # noqa: E402

BASELINES = _REPO_ROOT / "src" / "baselines"
PURSUIT_V1 = BASELINES / "pursuit_v1.cpp"

needs_cxx = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


# ---------------------------------------------------------------------------
# Pure-Python helpers
# ---------------------------------------------------------------------------


def test_parse_seed_list_variants():
    assert track_a._parse_seed_list("1,2,3") == [1, 2, 3]
    assert track_a._parse_seed_list("3 1 2") == [1, 2, 3]
    assert track_a._parse_seed_list("1,1,2") == [1, 2]
    assert track_a._parse_seed_list("") == []


def test_parse_seed_list_rejects_garbage():
    with pytest.raises(SystemExit):
        track_a._parse_seed_list("1, abc")


def test_parsers_expose_expected_flags():
    pa = track_a.build_parser()
    args = pa.parse_args(["--seeds", "1", "--out-dir", "/tmp/x"])
    assert args.seeds == "1"
    assert args.out_dir == Path("/tmp/x")
    assert args.aar is True and args.journal is True
    # Track B adds --rr-n-matches + --no-rr.
    pb = track_b.build_parser()
    argb = pb.parse_args(["--seeds", "1", "--out-dir", "/tmp/y",
                          "--no-rr", "--rr-n-matches", "7"])
    assert argb.no_rr is True and argb.rr_n_matches == 7
    # Track C adds --seed-ai-a/-b + --yardstick-every.
    pc = track_c.build_parser()
    argc = pc.parse_args(["--seeds", "1", "--out-dir", "/tmp/z",
                          "--yardstick-every", "3"])
    assert argc.yardstick_every == 3


def test_forward_common_argv_translates_flags():
    parser = _common.build_common_parser("p", "d")
    args = parser.parse_args([
        "--model", "claude-mock",
        "--client", "mock",
        "--mock-response-dir", "/tmp/r",
        "--n-matches", "3",
        "--no-aar",
        "--no-journal",
        "--workers", "2",
        "--out-dir", "/tmp/x",
        "-vv",
    ])
    argv = _common.forward_common_argv(args)
    assert "--model" in argv and "claude-mock" in argv
    assert "--client" in argv and "mock" in argv
    assert "--mock-response-dir" in argv and "/tmp/r" in argv
    assert "--no-aar" in argv and "--aar" not in argv
    assert "--no-journal" in argv and "--journal" not in argv
    assert "--workers" in argv and "2" in argv
    # Verbose flag is repeated.
    assert argv.count("-v") == 2


def test_invoke_evolve_non_strict_passes_rc(monkeypatch):
    calls: list[list[str]] = []

    def fake_main(argv):
        calls.append(list(argv))
        return 7

    monkeypatch.setattr(_common.evolve, "main", fake_main)
    assert _common.invoke_evolve(["--x"], strict=False) == 7
    assert calls == [["--x"]]


def test_invoke_evolve_strict_raises(monkeypatch):
    def fake_main(argv):
        return 11

    monkeypatch.setattr(_common.evolve, "main", fake_main)
    with pytest.raises(RuntimeError, match="evolve.main exited 11"):
        _common.invoke_evolve(["--x"], strict=True)


# ---------------------------------------------------------------------------
# Filesystem helpers + manifest roundtrip
# ---------------------------------------------------------------------------


def test_atomic_write_json_sorts_keys(tmp_path: Path):
    out = tmp_path / "m.json"
    _common.atomic_write_json(out, {"b": 2, "a": 1, "z": [3, 2, 1]})
    txt = out.read_text(encoding="utf-8")
    # Keys must appear in sorted order.
    assert txt.index('"a"') < txt.index('"b"') < txt.index('"z"')
    assert json.loads(txt) == {"a": 1, "b": 2, "z": [3, 2, 1]}


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello swarm")
    import hashlib
    assert _common.sha256_file(p) == hashlib.sha256(b"hello swarm").hexdigest()


def test_read_state_returns_none_when_missing(tmp_path: Path):
    assert _common.read_state(tmp_path) is None


def test_read_champion_path_prefers_state(tmp_path: Path):
    champs = tmp_path / "champions"
    champs.mkdir()
    snap = champs / "gen_0003.cpp"
    snap.write_text("// snap\n")
    (tmp_path / "state.json").write_text(json.dumps({
        "champion_source_rel": "champions/gen_0003.cpp",
    }))
    assert _common.read_champion_path(tmp_path) == snap.resolve()


def test_read_champion_path_falls_back_to_best(tmp_path: Path):
    champs = tmp_path / "champions"
    champs.mkdir()
    best = champs / "best.cpp"
    best.write_text("// best\n")
    # No state.json.
    assert _common.read_champion_path(tmp_path) == best.resolve()


def test_summarise_lineage_empty(tmp_path: Path):
    row = _common.summarise_lineage(tmp_path, seed=42)
    assert row.seed == 42
    assert row.generations_run == 0
    assert row.generations_accepted == 0
    assert row.champion_generation == -1
    assert row.champion_sha256 is None
    assert row.champion_fitness_mean is None


def test_summarise_lineage_populated(tmp_path: Path):
    champs = tmp_path / "champions"
    champs.mkdir()
    snap = champs / "gen_0002.cpp"
    snap.write_text("// gen 2\n")
    (tmp_path / "state.json").write_text(json.dumps({
        "champion_source_rel": "champions/gen_0002.cpp",
        "champion_generation": 2,
        "champion_fitness": {"mean": 0.375},
        "tokens_input": 120,
        "tokens_output": 80,
        "history": [
            {"status": "rejected"},
            {"status": "accepted"},
            {"status": "accepted"},
        ],
    }))
    row = _common.summarise_lineage(tmp_path, seed=42)
    assert row.generations_run == 3
    assert row.generations_accepted == 2
    assert row.champion_generation == 2
    assert row.champion_fitness_mean == pytest.approx(0.375)
    assert row.tokens_input == 120 and row.tokens_output == 80
    assert row.champion_sha256 is not None
    assert len(row.champion_sha256) == 64


def test_write_manifest_schema_validates(tmp_path: Path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (_REPO_ROOT / "docs" / "track_manifest_schema.json").read_text()
    )
    # Synthesize a lineage row by summarising an empty dir.
    row = _common.summarise_lineage(tmp_path, seed=1)
    manifest = tmp_path / "manifest.json"
    _common.write_manifest(
        manifest,
        track="A", model="m1",
        lineages=[row],
        extra={"n_matches": 3, "generations_requested": 1},
    )
    jsonschema.validate(
        instance=json.loads(manifest.read_text()),
        schema=schema,
    )


# ---------------------------------------------------------------------------
# CLI: -h exits 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mod", [track_a, track_b, track_c])
def test_cli_help_exits_ok(mod):
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["-h"])
    assert excinfo.value.code == 0


@pytest.mark.parametrize("mod", [track_a, track_b, track_c])
def test_cli_requires_seeds(tmp_path, mod):
    with pytest.raises(SystemExit):
        mod.main(["--out-dir", str(tmp_path)])


# ---------------------------------------------------------------------------
# Integration: mock-client track runs (C++ compiler required)
# ---------------------------------------------------------------------------


def _fenced(body: str) -> str:
    return f"```cpp\n{body}\n```\n"


def _baseline_cpp(namespace: str) -> str:
    src = PURSUIT_V1.read_text()
    return src.replace("TEAM_NS_PLACEHOLDER", namespace)


@pytest.fixture()
def mock_response_dir(tmp_path: Path) -> Path:
    mr = tmp_path / "responses"
    mr.mkdir()
    # Provide plenty of canned responses (enough for any multi-step track).
    body = _fenced(_baseline_cpp("TeamA"))
    for i in range(40):
        (mr / f"{i:03d}.md").write_text(body)
    return mr


@needs_cxx
def test_track_a_multi_seed_smoke(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_a"
    rc = track_a.main([
        "--seeds", "1,2",
        "--generations", "1",
        "--n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
    ])
    assert rc == 0
    manifest = json.loads((out / "track_a_manifest.json").read_text())
    assert manifest["track"] == "A"
    assert manifest["schema_version"] == 1
    assert [row["seed"] for row in manifest["lineages"]] == [1, 2]
    for seed in (1, 2):
        assert (out / f"seed{seed}" / "state.json").is_file()


@needs_cxx
def test_track_a_resume(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_a_resume"
    # First run: 1 generation.
    rc = track_a.main([
        "--seeds", "1",
        "--generations", "1",
        "--n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
    ])
    assert rc == 0
    state1 = json.loads((out / "seed1" / "state.json").read_text())
    assert state1["generation"] == 1

    # Second run: resume with --generations 2 (extend budget).
    rc2 = track_a.main([
        "--seeds", "1",
        "--generations", "2",
        "--n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
        "--resume",
    ])
    assert rc2 == 0
    state2 = json.loads((out / "seed1" / "state.json").read_text())
    assert state2["generation"] == 2
    assert len(state2["history"]) >= len(state1["history"])


@needs_cxx
def test_track_b_chain_with_rr(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_b"
    rc = track_b.main([
        "--seeds", "1",
        "--generations", "2",
        "--n-matches", "1",
        "--rr-n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
    ])
    assert rc == 0
    manifest = json.loads((out / "track_b_manifest.json").read_text())
    assert manifest["track"] == "B"
    # Per-generation step dirs exist.
    for gen in range(2):
        step = out / "seed1" / f"gen{gen:04d}"
        assert step.is_dir()
        assert (step / "state.json").is_file()
    # Lineage-level champions recorded (seed snapshot always present).
    champs = list((out / "seed1" / "champions").glob("*.cpp"))
    assert champs, "expected at least the seed champion snapshot"
    # Round-robin was attempted.
    assert manifest["rr_enabled"] is True
    assert "seed1" in manifest["rr_summaries"]


@needs_cxx
def test_track_b_no_rr_skips_tournament(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_b_nor"
    rc = track_b.main([
        "--seeds", "1",
        "--generations", "1",
        "--n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
        "--no-rr",
    ])
    assert rc == 0
    manifest = json.loads((out / "track_b_manifest.json").read_text())
    assert manifest["rr_enabled"] is False
    assert not (out / "seed1" / "tournament.json").is_file()


@needs_cxx
def test_track_c_coevo_with_yardstick(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_c"
    rc = track_c.main([
        "--seeds", "1",
        "--generations", "2",
        "--n-matches", "1",
        "--yardstick-every", "1",
        "--yardstick-n-matches", "1",
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
    ])
    assert rc == 0
    manifest = json.loads((out / "track_c_manifest.json").read_text())
    assert manifest["track"] == "C"
    # Two lineages per seed (A and B) → two manifest rows for seed=1.
    assert len(manifest["lineages"]) == 2
    # Per-generation step dirs for both lineages.
    for lab in ("A", "B"):
        for gen in range(2):
            assert (out / "seed1" / lab / f"gen{gen:04d}" / "state.json").is_file()
    # Yardstick rows produced (once per generation, starting at 0 with every=1).
    yp = out / "seed1" / "yardstick.jsonl"
    assert yp.is_file()
    rows = [json.loads(L) for L in yp.read_text().splitlines() if L.strip()]
    gens = [r["generation"] for r in rows]
    assert gens == sorted(gens)
    assert len(rows) >= 1


@needs_cxx
def test_track_c_yardstick_off(tmp_path: Path, mock_response_dir: Path):
    out = tmp_path / "track_c_ys_off"
    rc = track_c.main([
        "--seeds", "1",
        "--generations", "1",
        "--n-matches", "1",
        "--yardstick-every", "0",  # disabled
        "--workers", "1",
        "--client", "mock",
        "--mock-response-dir", str(mock_response_dir),
        "--out-dir", str(out),
    ])
    assert rc == 0
    assert not (out / "seed1" / "yardstick.jsonl").is_file()
