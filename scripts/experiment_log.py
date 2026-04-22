#!/usr/bin/env python3
"""Append-only experiment log (M9).

Every fitness run / evolutionary generation / tournament round that
produces non-trivial output also writes a single ``events.jsonl`` under
``<run_dir>/``. The file is append-only (one JSON object per line, no
editing, no reordering) so downstream tooling can tail it during long
runs and the log becomes a faithful audit trail.

Design constraints
------------------
* **Reproducibility metadata up front.** The first event is always
  ``experiment_start`` and records: git SHA, Python version, platform,
  hardware info, SHA-256 of each AI source, and a hash of the pinned
  requirements. ``scripts/reproduce.py`` (M15 aspirational; hooks in
  now) can use this to rebuild the environment.
* **Monotonic sequence.** ``seq`` starts at 0 and increments with every
  write. Out-of-order log lines are a bug.
* **ISO-8601 UTC timestamps.** No wall-clock arithmetic downstream.
* **Secret redaction.** API keys slip into logs through free-form
  payloads (error messages, prompts, LLM responses). Any string
  matching a well-known key pattern is replaced with
  ``***REDACTED***`` before write. This is defence-in-depth — the
  evolutionary loop (M10) is the producer most likely to leak.
* **Append-only IO.** Opened in ``"a"`` mode, flushed after every
  event so a crash leaves a well-formed prefix.

Public API
----------
>>> with ExperimentLog("data/experiments/demo") as log:
...     log.write("match_result", seed=0, outcome="A_WIN")
...     log.write("error", msg="oops")  # doctest: +SKIP
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Regex patterns that match API-key-shaped strings. Keep this list tight:
# a false positive in a trace payload is annoying but a false negative
# leaks a secret. Patterns are anchored with a length guard so short
# hex strings (e.g. a git SHA) aren't shredded.
_SECRET_PATTERNS = [
    # Anthropic: sk-ant-… up to the next whitespace/quote/comma.
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    # OpenAI legacy: sk-… (20+ chars).
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # Google Gemini: AIza… (39 chars).
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    # Bearer tokens.
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),
]
REDACTED = "***REDACTED***"


def redact(obj: Any) -> Any:
    """Recursively replace secret-looking substrings with ``REDACTED``.

    Handles ``str``, ``dict``, ``list``, ``tuple``; leaves other types
    untouched (ints, floats, bools, None, custom objects — redaction
    runs on JSON-serialisable payloads before :func:`json.dumps`).
    """
    if isinstance(obj, str):
        s = obj
        for pat in _SECRET_PATTERNS:
            s = pat.sub(REDACTED, s)
        return s
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Reproducibility snapshot
# ---------------------------------------------------------------------------


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2.0,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_dirty() -> bool | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=2.0,
        )
        if out.returncode != 0:
            return None
        return bool(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _requirements_hash() -> str | None:
    """SHA-256 of pyproject.toml (proxy for pinned dep set)."""
    p = REPO_ROOT / "pyproject.toml"
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_environment_snapshot(
    *,
    team_a_src: Path | None = None,
    team_b_src: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect the reproducibility-metadata dict used by ``experiment_start``.

    Callers pass in the AI source paths so hashes are recorded
    alongside the git SHA. ``extra`` is merged last, so callers can
    add fields (e.g. ``{"llm_model": "claude-3-5-sonnet"}``) without
    subclassing.
    """
    snapshot: dict[str, Any] = {
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "requirements_sha256": _requirements_hash(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "cpu_count": os.cpu_count(),
        "hostname": platform.node(),
    }
    if team_a_src is not None:
        snapshot["team_a"] = {
            "path": str(Path(team_a_src).resolve()),
            "sha256": _sha256_file(Path(team_a_src)) if Path(team_a_src).is_file() else None,
        }
    if team_b_src is not None:
        snapshot["team_b"] = {
            "path": str(Path(team_b_src).resolve()),
            "sha256": _sha256_file(Path(team_b_src)) if Path(team_b_src).is_file() else None,
        }
    if extra:
        snapshot.update(extra)
    return snapshot


# ---------------------------------------------------------------------------
# ExperimentLog
# ---------------------------------------------------------------------------


class ExperimentLog:
    """Append-only JSONL log in ``<run_dir>/events.jsonl``.

    Usage as a context manager ensures ``experiment_end`` is written
    and the underlying file handle is closed even on exception. The
    class is not thread-safe: each process/run owns one log.
    """

    def __init__(self, run_dir: Path | str, *, filename: str = "events.jsonl") -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / filename
        self._fh: IO[str] | None = None
        self._seq = 0
        self._closed = False

    # -- context-manager plumbing -------------------------------------

    def __enter__(self) -> "ExperimentLog":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc is not None:
                # Best-effort error record; don't swallow the original.
                self.write(
                    "experiment_error",
                    error_type=exc_type.__name__ if exc_type else "unknown",
                    error=str(exc),
                )
            self.write("experiment_end")
        finally:
            self.close()

    def open(self) -> None:
        if self._fh is not None:
            return
        self._fh = self.path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._closed:
            return
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._closed = True

    # -- writing ------------------------------------------------------

    def write(self, event_type: str, **fields: Any) -> dict[str, Any]:
        """Append one event. Returns the serialised dict for caller inspection.

        Fields are redacted before serialisation so a producer that
        accidentally passes ``api_key=...`` does not leak. The special
        key ``_raw=True`` disables redaction (reserved for
        architecture-test-only uses).
        """
        if self._fh is None:
            raise RuntimeError("ExperimentLog not open; use as context manager")
        raw_escape = fields.pop("_raw", False)
        payload = fields if raw_escape else redact(fields)
        event: dict[str, Any] = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "type": event_type,
            **(payload if isinstance(payload, dict) else {"payload": payload}),
        }
        self._fh.write(json.dumps(event, sort_keys=False) + "\n")
        self._fh.flush()
        self._seq += 1
        return event

    def write_start(
        self,
        *,
        experiment_type: str,
        team_a_src: Path | str | None = None,
        team_b_src: Path | str | None = None,
        config: dict[str, Any] | None = None,
        extra_env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Shorthand for the canonical first event.

        Callers pass ``experiment_type`` (e.g. ``"fitness"``,
        ``"evolve"``, ``"tournament"``) and the AI source paths; this
        attaches the full reproducibility snapshot and the free-form
        ``config`` dict (CLI args, usually).
        """
        ta = Path(team_a_src) if team_a_src else None
        tb = Path(team_b_src) if team_b_src else None
        env = build_environment_snapshot(team_a_src=ta, team_b_src=tb, extra=extra_env)
        return self.write(
            "experiment_start",
            experiment_type=experiment_type,
            config=config or {},
            environment=env,
        )

    # -- reading ------------------------------------------------------

    @classmethod
    def read(cls, run_dir: Path | str, *, filename: str = "events.jsonl") -> list[dict[str, Any]]:
        """Load and parse the events log of a completed run.

        Returns events in write order. Raises ``FileNotFoundError`` if
        the log is absent and ``ValueError`` if any line is
        unparseable (we surface corruption rather than silently
        dropping events).
        """
        path = Path(run_dir) / filename
        if not path.is_file():
            raise FileNotFoundError(f"no events log at {path}")
        events: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"malformed event at {path}:{lineno}: {exc}"
                    ) from exc
        return events


__all__ = [
    "ExperimentLog",
    "REDACTED",
    "build_environment_snapshot",
    "redact",
]
