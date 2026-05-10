#!/usr/bin/env python3
"""Byte-identical smoke harness (M20).

Re-runs a track-A mini configuration twice against a frozen mock LLM
and fails unless both runs produce the same deterministic fingerprint:

* SHA-256 of each accepted champion source.
* Per-generation ``status``, ``accepted``, ``candidate_sha256``,
  ``mean/stdev/ci_low/ci_high``, ``wins_a/wins_b/draws/invalid``.
* Final ``champion_generation``, ``champion_sha256``,
  ``champion_fitness_mean``.
* Track-A manifest body minus timing / absolute paths.

Wall-clock timings, ``run_id``, ISO timestamps, and absolute paths are
excluded from the comparison because they legitimately vary between
runs. If you want a byte-exact whole-tree diff, use ``git diff --no-index``
on two manually created clones; this harness is what CI runs.

Usage::

    python3 scripts/reproduce.py \\
        --mini-config scripts/ci_fixtures/mini_track_a \\
        --out-root build/mini_track_a \\
        --compare

Exit codes: 0 on match, 30 on divergence, 2 on CLI misuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent
REPO_ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_LOG = logging.getLogger("swarmevolve.reproduce")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DIVERGED = 30


# ---------------------------------------------------------------------------
# Fingerprint extraction
# ---------------------------------------------------------------------------


# Fields that legitimately vary between otherwise-identical runs.
_UNSTABLE_STATE_KEYS = {"run_id", "wall_start_iso"}
_UNSTABLE_HISTORY_KEYS = {"wall_seconds"}
_UNSTABLE_MANIFEST_KEYS = {"run_dir"}
# FitnessResult fields that vary run-to-run (wall clocks + absolute
# paths recorded by the compile wrapper). The rest of the payload
# (mean, stdev, wins, per-match seeds, deterministic per-match scores)
# is the whole point of the fingerprint.
_UNSTABLE_FITNESS_KEYS = {
    "wall_seconds",
    "team_a_path",
    "team_b_path",
    "compiler",
}
_UNSTABLE_PER_MATCH_KEYS = {"wall_ms"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalise_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sorted(row.items()) if k not in _UNSTABLE_HISTORY_KEYS}


def _normalise_fitness(fit: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a ``FitnessResult`` payload onto its deterministic fields.

    Drops wall-clock timings, absolute paths, and compiler hashes that
    legitimately differ between otherwise-identical runs; keeps the
    per-match seed/outcome vectors, aggregate statistics, and the
    per-match score distribution that the engine computes
    deterministically given identical inputs.
    """
    if not isinstance(fit, dict):
        return fit
    out: dict[str, Any] = {}
    for k, v in sorted(fit.items()):
        if k in _UNSTABLE_FITNESS_KEYS:
            continue
        if k == "per_match" and isinstance(v, list):
            out[k] = [
                {kk: vv for kk, vv in sorted(m.items()) if kk not in _UNSTABLE_PER_MATCH_KEYS}
                for m in v
                if isinstance(m, dict)
            ]
        else:
            out[k] = v
    return out


def _normalise_state(state: dict[str, Any]) -> dict[str, Any]:
    """Project a ``state.json`` onto the determinism fingerprint.

    Drops timing / identity fields, nests each history row through
    :func:`_normalise_history_row`, and keeps LoopConfig only to the
    extent that it controls semantic output (templates, flags).
    """
    out: dict[str, Any] = {
        k: v
        for k, v in sorted(state.items())
        if k not in _UNSTABLE_STATE_KEYS and k not in {"history", "config", "champion_fitness"}
    }
    out["history"] = [_normalise_history_row(r) for r in state.get("history", [])]
    out["champion_fitness"] = _normalise_fitness(state.get("champion_fitness"))
    # LoopConfig is kept as-is (it's pure config); but drop the absolute
    # mock_response_paths if present since they're filesystem-dependent.
    cfg = dict(state.get("config", {}) or {})
    cfg.pop("mock_response_paths", None)
    out["config"] = cfg
    return out


def _normalise_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Manifest is already pure data; only scrub absolute paths."""
    payload = json.loads(json.dumps(manifest, sort_keys=True))
    for lineage in payload.get("lineages", []):
        for key in _UNSTABLE_MANIFEST_KEYS:
            lineage.pop(key, None)
    for k in ("opponent", "seed_ai_a", "seed_ai_b", "initial_seed_ai"):
        payload.pop(k, None)
    return payload


def fingerprint_run(track_root: Path) -> dict[str, Any]:
    """Build a normalised fingerprint of every ``state.json`` and the
    track manifest. Stable across reruns when the underlying mock
    transcripts are identical.
    """
    fp: dict[str, Any] = {"states": {}, "champions": {}, "manifest": None}
    for sp in sorted(track_root.rglob("state.json")):
        rel = sp.relative_to(track_root).as_posix()
        fp["states"][rel] = _normalise_state(json.loads(sp.read_text(encoding="utf-8")))
    for cp in sorted(track_root.rglob("champions/*.cpp")):
        rel = cp.relative_to(track_root).as_posix()
        fp["champions"][rel] = _sha256_file(cp)
    for mp in sorted(track_root.glob("track_*_manifest.json")):
        fp["manifest"] = _normalise_manifest(json.loads(mp.read_text(encoding="utf-8")))
        break  # only one manifest per track root
    return fp


def fingerprint_digest(fp: dict[str, Any]) -> str:
    """Sort-key stable digest of a fingerprint dict."""
    blob = json.dumps(fp, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Mini-track execution
# ---------------------------------------------------------------------------


def _materialise_mock_dir(template_dir: Path, staging: Path, count: int = 40) -> Path:
    """Expand a single-template mini-config into the ``count`` canned
    responses that ``evolve --client mock`` consumes.

    Expects ``template_dir/response.md`` to exist; duplicates it into
    ``staging/NNN.md``. This keeps the committed fixture tiny while
    still giving evolve enough canned responses for a multi-step run.
    """
    src = template_dir / "response.md"
    if not src.is_file():
        raise FileNotFoundError(f"mini-config missing response.md: {src}")
    staging.mkdir(parents=True, exist_ok=True)
    body = src.read_text(encoding="utf-8")
    for i in range(count):
        (staging / f"{i:03d}.md").write_text(body, encoding="utf-8")
    return staging


def _run_track_a(
    *,
    out_dir: Path,
    mock_dir: Path,
    seeds: str,
    generations: int,
    n_matches: int,
    extra_argv: list[str] | None = None,
) -> int:
    """Invoke track_a.main against the mock LLM."""
    import tracks.track_a as track_a

    argv = [
        "--seeds",
        seeds,
        "--out-dir",
        str(out_dir),
        "--generations",
        str(generations),
        "--n-matches",
        str(n_matches),
        "--client",
        "mock",
        "--mock-response-dir",
        str(mock_dir),
        "--no-aar",
        "--no-journal",
        "--checkpoint-every",
        "1",
    ]
    if extra_argv:
        argv += extra_argv
    _LOG.info("reproduce: running track_a with argv=%s", argv)
    return track_a.main(argv)


def run_and_compare(
    *,
    mini_config: Path,
    out_root: Path,
    seeds: str,
    generations: int,
    n_matches: int,
) -> int:
    """Run the mini-track twice; fail with :data:`EXIT_DIVERGED` if the
    two fingerprints disagree."""
    run_a = out_root / "run_a"
    run_b = out_root / "run_b"
    for p in (run_a, run_b):
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)

    # Expand the 1-template mini-config into a dir of canned responses.
    mock_dir = _materialise_mock_dir(
        mini_config,
        out_root / "_mock_responses",
    )

    rc_a = _run_track_a(
        out_dir=run_a,
        mock_dir=mock_dir,
        seeds=seeds,
        generations=generations,
        n_matches=n_matches,
    )
    rc_b = _run_track_a(
        out_dir=run_b,
        mock_dir=mock_dir,
        seeds=seeds,
        generations=generations,
        n_matches=n_matches,
    )
    if rc_a != 0 or rc_b != 0:
        _LOG.error("track_a exited non-zero: rc_a=%d rc_b=%d", rc_a, rc_b)
        return EXIT_DIVERGED

    fp_a = fingerprint_run(run_a)
    fp_b = fingerprint_run(run_b)
    d_a = fingerprint_digest(fp_a)
    d_b = fingerprint_digest(fp_b)
    _LOG.info("run_a digest: %s", d_a)
    _LOG.info("run_b digest: %s", d_b)
    if d_a != d_b:
        _LOG.error("reproduce FAILED: digests differ")
        _dump_diff(fp_a, fp_b, out_root)
        return EXIT_DIVERGED
    _LOG.info("reproduce OK: %s", d_a)
    (out_root / "reproduce.ok").write_text(d_a + "\n", encoding="utf-8")
    return EXIT_OK


def _dump_diff(fp_a: dict[str, Any], fp_b: dict[str, Any], out_root: Path) -> None:
    """Write the two fingerprints side-by-side so CI logs show what
    diverged without needing the whole run artifacts."""
    (out_root / "fingerprint_a.json").write_text(
        json.dumps(fp_a, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_root / "fingerprint_b.json").write_text(
        json.dumps(fp_b, indent=2, sort_keys=True), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reproduce",
        description="Byte-identical mini-track smoke test (M20).",
    )
    p.add_argument(
        "--mini-config",
        type=Path,
        required=True,
        help="Directory of canned mock responses (.md files)",
    )
    p.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Scratch root; both runs land under <out-root>/run_a/ and <out-root>/run_b/",
    )
    p.add_argument("--seeds", default="1,2", help="Track-A --seeds value (default '1,2')")
    p.add_argument("--generations", type=int, default=2)
    p.add_argument("--n-matches", type=int, default=3)
    p.add_argument("-v", "--verbose", action="count", default=0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose >= 2
        else logging.INFO
        if args.verbose == 1
        else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.mini_config.is_dir():
        _LOG.error("mini-config dir not found: %s", args.mini_config)
        return EXIT_USAGE
    args.out_root.mkdir(parents=True, exist_ok=True)
    return run_and_compare(
        mini_config=args.mini_config.resolve(),
        out_root=args.out_root.resolve(),
        seeds=args.seeds,
        generations=args.generations,
        n_matches=args.n_matches,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
