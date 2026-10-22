"""Tests for the eval container's batch mode and the fitness backends (v2).

Unit tests (no container) cover backend selection, seed splitting, the
batch command's isolation flags, platform labels, stale-image refusal and
the invalid-match categories on the host backend. The ``docker`` tests
need a container runtime and the eval image (``make docker-build``) and
skip cleanly without them:

* both backends give identical per-match results on the baselines;
* the four invalid categories are reported separately in the container
  and score as losses for the program under test;
* a trace recorded in the container matches the host's byte for byte.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_batch
import fitness
import platform_fingerprint
import sandbox

BASELINES = REPO_ROOT / "src" / "baselines"
ADVERSARIAL = REPO_ROOT / "tests" / "fixtures" / "inject" / "adversarial"
IMAGE = os.environ.get("SANDBOX_IMAGE", sandbox.DEFAULT_IMAGE)

_AI_HEAD = (
    '#include "../ai_abi.h"\n'
    '#include "../types.h"\n'
    "namespace TeamA {\n"
    "void drone_ai(int, const GameParams*, const AllyState*,\n"
    "              const EnemyState*, const float[][MSG_SIZE],\n"
    "              float*, Action* out) {\n"
    "    out->velocity.x = 0; out->velocity.y = 0; out->target_id = -1;\n"
    "    for (int i = 0; i < MSG_SIZE; ++i) { out->message_out[i] = 0; }\n"
)
CRASHING_AI = _AI_HEAD + "    __builtin_trap();\n}\n}\n"
BROKEN_AI = _AI_HEAD + "    this does not compile;\n}\n}\n"
SLEEPING_AI = (
    '#include "../ai_abi.h"\n'
    '#include "../types.h"\n'
    "#include <unistd.h>\n"
    "namespace TeamA {\n"
    "void drone_ai(int, const GameParams*, const AllyState*,\n"
    "              const EnemyState*, const float[][MSG_SIZE],\n"
    "              float*, Action* out) {\n"
    "    out->velocity.x = 0; out->velocity.y = 0; out->target_id = -1;\n"
    "    (void)usleep(200000);\n"
    "}\n}\n"
)


def _sandbox_or_skip() -> None:
    try:
        rt = sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")
    if not sandbox.image_present(IMAGE, runtime=rt):
        pytest.skip(f"eval image '{IMAGE}' not found; run `make docker-build` first")


@pytest.fixture()
def home_tmp() -> Iterator[Path]:
    """Scratch under $HOME so Colima can bind-mount it (see test_sandbox_ok)."""
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="eval_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(dir_: Path, name: str, text: str) -> Path:
    p = dir_ / name
    p.write_text(text)
    return p


def _rows(result: fitness.FitnessResult) -> list[dict]:
    """Per-match rows without wall time, the only field allowed to differ."""
    return [{k: v for k, v in m.items() if k != "wall_ms"} for m in result.per_match]


# ---------------------------------------------------------------------------
# Unit tests (no container)
# ---------------------------------------------------------------------------


def test_default_backend_is_sandbox(monkeypatch):
    monkeypatch.delenv(fitness.BACKEND_ENV, raising=False)
    assert fitness.resolve_backend(None) == "sandbox"
    monkeypatch.setenv(fitness.BACKEND_ENV, "host")
    assert fitness.resolve_backend(None) == "host"
    assert fitness.resolve_backend("sandbox") == "sandbox"
    with pytest.raises(ValueError):
        fitness.resolve_backend("gpu")


def test_split_seeds_is_a_deterministic_partition():
    seeds = list(range(100, 111))
    parts = fitness.split_seeds(seeds, 4)
    assert parts == fitness.split_seeds(seeds, 4)
    assert sorted(s for p in parts for s in p) == seeds
    assert all(parts) and len(parts) == 4
    assert len(fitness.split_seeds(seeds[:2], 8)) == 2


def test_batch_command_keeps_every_isolation_flag(tmp_path):
    cmd = sandbox.build_batch_command(
        staging=tmp_path / "src",
        out_dir=tmp_path / "out",
        image="sha256:abc",
        runtime="docker",
        name="n",
        cpus=4,
        batch_args=["--jobs", "4"],
    )
    expected = [f for f in sandbox.SANDBOX_FLAGS if f != "--cpus=2"] + ["--cpus=4"]
    for flag in expected:
        assert flag in cmd, flag
    assert "--cpus=2" not in cmd
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "no-new-privileges"):
        assert flag in cmd
    assert "--memory=512m" in cmd and "--pids-limit=64" in cmd and "65534:65534" in cmd
    assert f"{(tmp_path / 'src').resolve()}:/work/src:ro" in cmd
    assert cmd[cmd.index("sha256:abc") + 1] == "batch"


def test_isolation_flags_change_only_cpus():
    assert sandbox.isolation_flags(2) == sandbox.SANDBOX_FLAGS
    changed = [
        (a, b)
        for a, b in zip(sandbox.isolation_flags(8), sandbox.SANDBOX_FLAGS, strict=True)
        if a != b
    ]
    assert changed == [("--cpus=8", "--cpus=2")]


def _fp(**overrides) -> dict:
    fp = {
        "backend": "sandbox",
        "image": "swarmevolve-eval:latest",
        "image_id": "sha256:1",
        "compiler_version": "g++ 11",
        "flags": ["-O2"],
        "cpu_model": "cpu",
        "kernel": "k",
        "harness_sha256": {},
    }
    fp.update(overrides)
    return fp


def test_platform_label(tmp_path):
    path = tmp_path / "PLATFORM.json"
    assert platform_fingerprint.platform_label(_fp(), path) == "dev"
    path.write_text(json.dumps({"status": "draft", "fingerprint": _fp()}))
    assert platform_fingerprint.platform_label(_fp(), path) == "canonical-draft"
    # Non-canonical keys (tag, harness hashes) do not matter; canonical ones do.
    assert platform_fingerprint.platform_label(_fp(image="other:tag"), path) == "canonical-draft"
    for key, value in (("kernel", "k2"), ("image_id", "sha256:2"), ("backend", "host")):
        assert platform_fingerprint.platform_label(_fp(**{key: value}), path) == "dev", key
    path.write_text(json.dumps({"status": "final", "fingerprint": _fp()}))
    assert platform_fingerprint.platform_label(_fp(), path) == "canonical"


def test_stale_image_is_refused(tmp_path, monkeypatch):
    def fake_batch(a, b, seeds, work_dir, **kwargs):
        rows = [eval_batch._row(s, "DRAW", 0.0, 2, 1) for s in seeds]
        status = {"harness_sha256": {"engine.cpp": "old"}, "n_done": len(seeds)}
        return sandbox.BatchResult("sha256:x", rows, status, 0.1, None)

    monkeypatch.setattr(sandbox, "resolve_image_id", lambda *a, **k: "sha256:x")
    monkeypatch.setattr(sandbox, "run_batch_in_sandbox", fake_batch)
    with pytest.raises(fitness.StaleImageError):
        fitness.evaluate_fitness(
            BASELINES / "pursuit_v1.cpp",
            BASELINES / "cluster_v1.cpp",
            n_matches=4,
            backend="sandbox",
            scratch_root=tmp_path,
        )


def _categories(tmp_path: Path, backend: str, **kwargs) -> dict[str, fitness.FitnessResult]:
    """One evaluation per invalid category, with A under test."""
    cases = {
        "GUARD_FAIL": ADVERSARIAL / "goto_based_loop.cpp",
        "COMPILE_FAIL": _write(tmp_path, "broken.cpp", BROKEN_AI),
        "CRASH": _write(tmp_path, "crash.cpp", CRASHING_AI),
        "TIMEOUT": _write(tmp_path, "sleep.cpp", SLEEPING_AI),
    }
    out = {}
    for outcome, src in cases.items():
        out[outcome] = fitness.evaluate_fitness(
            src,
            BASELINES / "cluster_v1.cpp",
            n_matches=3,
            seed_base=7,
            workers=1,
            timeout=0.5 if outcome == "TIMEOUT" else 10.0,
            under_test="A",
            backend=backend,
            scratch_root=tmp_path / "scratch",
            raise_on_build_failure=False,
            **kwargs,
        )
    return out


def _assert_categories(results: dict[str, fitness.FitnessResult]) -> None:
    for outcome, r in results.items():
        assert [m["outcome"] for m in r.per_match] == [outcome] * 3, (outcome, r.per_match)
        assert [m["score"] for m in r.per_match] == [-1.0] * 3, outcome
        assert r.invalid == 3 and r.draws == 0 and r.mean == -1.0, outcome
    assert {m["failed_side"] for m in results["GUARD_FAIL"].per_match} == {"A"}
    assert {m["failed_side"] for m in results["COMPILE_FAIL"].per_match} == {"A"}


def test_invalid_categories_host(tmp_path):
    _assert_categories(_categories(tmp_path, "host"))


def test_build_failure_raises_with_result_attached(tmp_path):
    broken = _write(tmp_path, "broken.cpp", BROKEN_AI)
    with pytest.raises(fitness.CompileError) as info:
        fitness.evaluate_fitness(
            broken,
            BASELINES / "cluster_v1.cpp",
            n_matches=2,
            under_test="A",
            backend="host",
            scratch_root=tmp_path,
        )
    assert not isinstance(info.value, fitness.GuardError)
    assert "side=A" in str(info.value) and "error:" in str(info.value)
    assert info.value.result is not None
    assert [m["outcome"] for m in info.value.result.per_match] == ["COMPILE_FAIL"] * 2
    with pytest.raises(fitness.GuardError):
        fitness.evaluate_fitness(
            ADVERSARIAL / "goto_based_loop.cpp",
            BASELINES / "cluster_v1.cpp",
            n_matches=1,
            backend="host",
            scratch_root=tmp_path,
        )


def test_result_carries_backend_and_platform(tmp_path):
    r = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        n_matches=2,
        backend="host",
        scratch_root=tmp_path,
    )
    assert r.backend == "host"
    for key in ("compiler_version", "flags", "cpu_model", "kernel", "harness_sha256", "label"):
        assert r.platform[key], key
    assert "-ffp-contract=off" in r.platform["flags"]
    assert r.platform["image_id"] is None and r.platform["label"] == "dev"


# ---------------------------------------------------------------------------
# Container tests
# ---------------------------------------------------------------------------


PAIRS = [
    ("pursuit_v1.cpp", "cluster_v1.cpp"),
    ("cluster_v1.cpp", "pursuit_v1.cpp"),
    ("pursuit_v1.cpp", "stationary_v1.cpp"),
    ("cluster_v1.cpp", "cluster_v1.cpp"),
]


@pytest.mark.docker
@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_backends_identical_on_baselines(home_tmp, a, b):
    _sandbox_or_skip()
    kwargs = dict(n_matches=60, seed_base=1000, under_test="A", scratch_root=home_tmp)
    host = fitness.evaluate_fitness(BASELINES / a, BASELINES / b, backend="host", **kwargs)
    box = fitness.evaluate_fitness(
        BASELINES / a, BASELINES / b, backend="sandbox", image=IMAGE, workers=4, **kwargs
    )
    assert _rows(box) == _rows(host)
    for field in ("wins_a", "wins_b", "draws", "invalid", "mean", "ci_low", "ci_high"):
        assert getattr(box, field) == getattr(host, field), field
    assert box.backend == "sandbox" and box.platform["image_id"].startswith("sha256:")
    assert box.platform["harness_sha256"] == host.platform["harness_sha256"]
    assert box.platform["flags"] == host.platform["flags"]


@pytest.mark.docker
def test_invalid_categories_sandbox(home_tmp):
    _sandbox_or_skip()
    _assert_categories(_categories(home_tmp, "sandbox", image=IMAGE))


@pytest.mark.docker
def test_sandbox_trace_matches_host(home_tmp):
    _sandbox_or_skip()
    traces = {}
    for backend in ("host", "sandbox"):
        traces[backend] = home_tmp / f"{backend}.jsonl"
        fitness.evaluate_fitness(
            BASELINES / "pursuit_v1.cpp",
            BASELINES / "cluster_v1.cpp",
            n_matches=3,
            seed_base=41,
            workers=2,
            backend=backend,
            image=IMAGE,
            container_cpus=1,
            scratch_root=home_tmp / "scratch",
            record_trace_for_match_idx=1,
            trace_output_path=traces[backend],
        )
    assert traces["sandbox"].read_bytes() == traces["host"].read_bytes()
    assert traces["sandbox"].stat().st_size > 0


def test_platform_json_draft_is_well_formed():
    ref = platform_fingerprint.load_platform()
    if ref is None:
        pytest.skip("prereg/PLATFORM.json not written yet")
    assert ref["status"] in ("draft", "final")
    assert ref["canonical_keys"] == list(platform_fingerprint.CANONICAL_KEYS)
    fp = ref["fingerprint"]
    assert fp["backend"] == "sandbox" and fp["image_id"].startswith("sha256:")
    assert "-ffp-contract=off" in fp["flags"]
    assert all(fp[k] for k in platform_fingerprint.CANONICAL_KEYS)


def test_drones_and_arena_scale_reach_the_engine(tmp_path):
    kwargs = dict(n_matches=6, seed_base=3, backend="host", scratch_root=tmp_path)
    base = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp", BASELINES / "cluster_v1.cpp", **kwargs
    )
    wide = fitness.evaluate_fitness(
        BASELINES / "pursuit_v1.cpp",
        BASELINES / "cluster_v1.cpp",
        drones_a=20,
        drones_b=20,
        arena_scale=2.0,
        **kwargs,
    )
    assert _rows(base) != _rows(wide)
    # 20 drones a side: survivors above the default team size of 10 are possible.
    assert max(max(m["a_alive"], m["b_alive"]) for m in wide.per_match) <= 20


@pytest.mark.docker
def test_backends_identical_with_drones_and_arena_scale(home_tmp):
    _sandbox_or_skip()
    kwargs = dict(
        n_matches=20,
        seed_base=77,
        drones_a=20,
        drones_b=20,
        arena_scale=1.5,
        scratch_root=home_tmp,
    )
    host = fitness.evaluate_fitness(
        BASELINES / "cluster_v1.cpp", BASELINES / "pursuit_v1.cpp", backend="host", **kwargs
    )
    box = fitness.evaluate_fitness(
        BASELINES / "cluster_v1.cpp",
        BASELINES / "pursuit_v1.cpp",
        backend="sandbox",
        image=IMAGE,
        workers=4,
        **kwargs,
    )
    assert _rows(box) == _rows(host)
