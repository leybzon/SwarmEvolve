"""Shared helpers for SwarmEvolve M19 track runners.

The three track scripts (``track_a``, ``track_b``, ``track_c``) all wrap
``scripts/evolve.py``'s ``main(argv)`` entry point in process. They share:

* A common ``argparse`` skeleton (:func:`build_common_parser`).
* A wrapper that forwards ``argv`` to ``evolve.main`` and raises on
  non-zero exit codes (:func:`invoke_evolve`).
* Small file helpers to snapshot champion source files, read the
  persisted loop state, and write atomic manifests.

Deliberate non-goals: this module never talks to the network, never
forks a subprocess, and never imports the C++ engine. All heavy lifting
stays inside ``evolve.main`` so tests can drive everything with the
deterministic ``MockClient``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make sibling ``scripts/`` importable both when invoked as a script and
# when installed as a package (``python -m scripts.tracks.track_a``).
_THIS = Path(__file__).resolve()
_SCRIPTS = _THIS.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evolve  # noqa: E402
import tournament  # noqa: E402

REPO_ROOT = _SCRIPTS.parent
BASELINES = REPO_ROOT / "src" / "baselines"
PURSUIT_V1 = BASELINES / "pursuit_v1.cpp"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_RESUME_FAILED = 30
EXIT_EVOLVE_FAILED = 31

TRACK_MANIFEST_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# CLI skeleton
# ---------------------------------------------------------------------------


def build_common_parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Produce the CLI skeleton shared by all three tracks.

    Track-specific options (``--seeds``, ``--yardstick-every``, etc.)
    are added by the callers on top of the returned parser.
    """
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "--model", default=None, help="Model id forwarded to evolve (overrides $ANTHROPIC_MODEL)"
    )
    p.add_argument("--generations", type=int, default=5, help="Generations per lineage")
    p.add_argument(
        "--n-matches",
        type=int,
        default=10,
        help="Matches per generation (and per tournament pairing)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel match workers (defaults to evolve's default)",
    )
    p.add_argument(
        "--client",
        choices=("anthropic", "mock"),
        default="anthropic",
        help="LLM client kind forwarded to evolve",
    )
    p.add_argument(
        "--mock-response-dir",
        default=None,
        help="Directory (or single .md file) of canned responses when --client=mock",
    )
    p.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Track root; each lineage/step is a subdirectory",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoints already present in --out-dir "
        "(missing subdirs are started fresh)",
    )
    p.add_argument("--aar", dest="aar", action="store_true", help="Enable AARs (default on)")
    p.add_argument("--no-aar", dest="aar", action="store_false", help="Disable AARs")
    p.add_argument(
        "--journal",
        dest="journal",
        action="store_true",
        help="Enable learning journal (default on)",
    )
    p.add_argument(
        "--no-journal", dest="journal", action="store_false", help="Disable learning journal"
    )
    p.set_defaults(aar=True, journal=True)
    p.add_argument(
        "--checkpoint-every", type=int, default=1, help="evolve checkpoint cadence (generations)"
    )
    p.add_argument("--max-compile-failures", type=int, default=5)
    p.add_argument(
        "--max-compile-retries",
        type=int,
        default=10,
        help="Per-generation retry budget when the LLM's candidate "
        "fails parse/lint/inject/compile. Forwarded to evolve.",
    )
    p.add_argument("--accept-margin", type=float, default=0.0)
    p.add_argument(
        "--max-tokens",
        type=int,
        default=0,
        help="Hard cap on cumulative LLM tokens (input+output) "
        "summed across every state.json under --out-dir. "
        "0 disables enforcement.",
    )
    p.add_argument("-v", "--verbose", action="count", default=0)
    return p


def forward_common_argv(args: argparse.Namespace) -> list[str]:
    """Translate the shared skeleton namespace back into ``evolve`` argv
    tokens. Track-specific tokens (``--seed``, ``--out-dir``, etc.) must
    be prepended by the caller.
    """
    argv: list[str] = []
    if args.model is not None:
        argv += ["--model", args.model]
    argv += ["--client", args.client]
    if args.mock_response_dir is not None:
        argv += ["--mock-response-dir", str(args.mock_response_dir)]
    argv += ["--n-matches", str(args.n_matches)]
    if args.workers is not None:
        argv += ["--workers", str(args.workers)]
    argv += ["--checkpoint-every", str(args.checkpoint_every)]
    argv += ["--max-compile-failures", str(args.max_compile_failures)]
    argv += ["--max-compile-retries", str(args.max_compile_retries)]
    argv += ["--accept-margin", str(args.accept_margin)]
    argv += ["--aar" if args.aar else "--no-aar"]
    argv += ["--journal" if args.journal else "--no-journal"]
    for _ in range(max(0, args.verbose)):
        argv.append("-v")
    return argv


# ---------------------------------------------------------------------------
# Evolve wrapper
# ---------------------------------------------------------------------------


def invoke_evolve(argv: list[str], *, strict: bool = True) -> int:
    """Call ``evolve.main`` in process. Returns the exit code; raises
    :class:`RuntimeError` when ``strict`` and the return code is nonzero.
    """
    rc = evolve.main(argv)
    if strict and rc != EXIT_OK:
        raise RuntimeError(f"evolve.main exited {rc} (argv={argv!r})")
    return rc


# ---------------------------------------------------------------------------
# Filesystem + state helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as sorted-keys pretty JSON atomically (same FS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".manifest.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_state(run_dir: Path) -> dict[str, Any] | None:
    """Read ``<run_dir>/state.json`` if present; otherwise return None."""
    sp = run_dir / "state.json"
    if not sp.is_file():
        return None
    return json.loads(sp.read_text(encoding="utf-8"))


def read_champion_path(run_dir: Path) -> Path | None:
    """Return the absolute path of the lineage's current champion.

    Prefers ``state.champion_source_rel`` from ``state.json``; falls
    back to ``champions/best.cpp`` if state is missing. Returns None
    when neither exists (fresh run, no generations completed).
    """
    st = read_state(run_dir)
    if st and st.get("champion_source_rel"):
        p = (run_dir / st["champion_source_rel"]).resolve()
        if p.is_file():
            return p
    best = run_dir / "champions" / "best.cpp"
    return best.resolve() if best.is_file() else None


def copy_snapshot(src: Path, dst: Path) -> Path:
    """Copy ``src`` to ``dst`` (creating parents). Overwrites if dst exists."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Namespace normalisation for tournament re-use
# ---------------------------------------------------------------------------

#: The per-file placeholder that ``scripts/fitness.py::_render`` substitutes
#: at build time. Champion snapshots produced by ``evolve`` are already
#: substituted (they live under ``namespace TeamA`` or ``namespace TeamB``),
#: so we must re-insert the placeholder before a champion can be compiled
#: into either slot of a round-robin tournament.
PLACEHOLDER_TOKEN = "TEAM_NS_PLACEHOLDER"


def neutralise_namespace(source_text: str) -> str:
    """Return a copy of ``source_text`` with hard-coded ``namespace TeamA``
    or ``namespace TeamB`` occurrences replaced by the
    :data:`PLACEHOLDER_TOKEN`. If the source already contains the
    placeholder, it is returned unchanged. Other occurrences of
    ``TeamA``/``TeamB`` as identifiers (e.g. comments, string literals)
    are preserved.
    """
    if PLACEHOLDER_TOKEN in source_text:
        return source_text
    replaced = source_text
    # Only rewrite the namespace open/close, not every mention. We accept
    # both ``namespace TeamA`` (decl/open) and ``// namespace TeamA``
    # (close marker) since that's the canonical layout emitted by evolve.
    for team in ("TeamA", "TeamB"):
        replaced = replaced.replace(f"namespace {team}", f"namespace {PLACEHOLDER_TOKEN}")
    return replaced


def neutralised_copy(src: Path, dst: Path) -> Path:
    """Read ``src``, :func:`neutralise_namespace`, and write to ``dst``.
    The destination directory is created if needed.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = Path(src).read_text(encoding="utf-8")
    dst.write_text(neutralise_namespace(text), encoding="utf-8")
    return dst


# ---------------------------------------------------------------------------
# Manifest primitives
# ---------------------------------------------------------------------------


@dataclass
class LineageSummary:
    """One row of a track manifest: one lineage's final state."""

    seed: int
    run_dir: str  # absolute path
    generations_run: int
    generations_accepted: int
    champion_generation: int
    champion_sha256: str | None
    champion_fitness_mean: float | None
    tokens_input: int
    tokens_output: int
    # Exit code from ``evolve.main`` for this lineage. 0 == ran to
    # completion, 30 == lineage exhausted (max_compile_failures hit),
    # other non-zero values are hard failures. None means the caller
    # did not record an exit code (e.g. loaded from an old manifest).
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "run_dir": self.run_dir,
            "generations_run": self.generations_run,
            "generations_accepted": self.generations_accepted,
            "champion_generation": self.champion_generation,
            "champion_sha256": self.champion_sha256,
            "champion_fitness_mean": self.champion_fitness_mean,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "exit_code": self.exit_code,
        }


def summarise_lineage(
    run_dir: Path,
    *,
    seed: int,
    exit_code: int | None = None,
) -> LineageSummary:
    """Build a :class:`LineageSummary` from a completed (or partial) run dir.

    ``exit_code`` threads through the ``evolve.main`` return code so a
    lineage that exited 30 (``max_compile_failures``) is flagged as
    exhausted rather than silently merged with healthy lineages.
    """
    st = read_state(run_dir) or {}
    history = st.get("history", [])
    accepted = sum(1 for h in history if h.get("status") == "accepted")
    champ_gen = int(st.get("champion_generation", -1))
    champ_path = read_champion_path(run_dir)
    champ_sha = sha256_file(champ_path) if champ_path and champ_path.is_file() else None
    champ_fit = st.get("champion_fitness") or {}
    mean = champ_fit.get("mean")
    return LineageSummary(
        seed=seed,
        run_dir=str(run_dir.resolve()),
        generations_run=len(history),
        generations_accepted=accepted,
        champion_generation=champ_gen,
        champion_sha256=champ_sha,
        champion_fitness_mean=(float(mean) if mean is not None else None),
        tokens_input=int(st.get("tokens_input", 0)),
        tokens_output=int(st.get("tokens_output", 0)),
        exit_code=exit_code,
    )


def write_manifest(
    out_path: Path,
    *,
    track: str,
    model: str,
    lineages: list[LineageSummary],
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a stable, sorted-keys JSON manifest to ``out_path``."""
    payload: dict[str, Any] = {
        "schema_version": TRACK_MANIFEST_SCHEMA_VERSION,
        "track": track,
        "model": model,
        "lineages": [row.to_dict() for row in lineages],
    }
    if extra:
        # Merge shallowly; manifest consumers ignore unknown keys.
        for k, v in sorted(extra.items()):
            payload[k] = v
    atomic_write_json(out_path, payload)


from tracks._budget import BudgetExceeded, TokenBudget  # noqa: E402

EXIT_BUDGET_EXCEEDED = 40  # track runner aborted because --max-tokens was crossed


__all__ = [
    "EXIT_BUDGET_EXCEEDED",
    "EXIT_EVOLVE_FAILED",
    "EXIT_INVALID_INPUT",
    "EXIT_OK",
    "EXIT_RESUME_FAILED",
    "PLACEHOLDER_TOKEN",
    "PURSUIT_V1",
    "REPO_ROOT",
    "TRACK_MANIFEST_SCHEMA_VERSION",
    "BudgetExceeded",
    "LineageSummary",
    "TokenBudget",
    "atomic_write_json",
    "build_common_parser",
    "copy_snapshot",
    "evolve",
    "forward_common_argv",
    "invoke_evolve",
    "neutralise_namespace",
    "neutralised_copy",
    "read_champion_path",
    "read_state",
    "sha256_file",
    "summarise_lineage",
    "tournament",
    "write_manifest",
]
