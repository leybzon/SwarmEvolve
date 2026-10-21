#!/usr/bin/env python3
"""Platform fingerprint stamped on every evaluation result (v2 study).

A fingerprint says where a match was compiled and played: the backend,
the eval image's content-addressed ID, the compiler version, the compile
flags, the CPU model and the kernel, plus the hashes of the harness files
(engine, injector, flags, runner) and, for the container, the image's
build record. ``scripts/fitness.py`` builds one from the batch status for
every :class:`fitness.FitnessResult`.

The canonical platform lives in ``prereg/PLATFORM.json``. A result is
``canonical`` only when every key in ``CANONICAL_KEYS`` matches it and the
file is final; while the file is a draft a match is labelled
``canonical-draft``. Anything else is ``dev`` and must never be mixed into
reported numbers.

CLI::

    python3 scripts/platform_fingerprint.py --show
    python3 scripts/platform_fingerprint.py --write-draft prereg/PLATFORM.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform as _platform
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

PLATFORM_JSON = REPO_ROOT / "prereg" / "PLATFORM.json"

CANONICAL_KEYS: tuple[str, ...] = (
    "backend",
    "image_id",
    "compiler_version",
    "flags",
    "cpu_model",
    "kernel",
)


def from_status(
    status: dict[str, Any],
    *,
    backend: str,
    image: str | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    """Fingerprint from an ``eval_batch`` status (or ``--info``) dict."""
    fp: dict[str, Any] = {
        "backend": backend,
        "image": image,
        "image_id": image_id,
        "compiler_version": status.get("compiler_version", ""),
        "flags": list(status.get("flags", [])),
        "cpu_model": status.get("cpu_model", ""),
        "kernel": status.get("kernel", ""),
        "harness_sha256": dict(status.get("harness_sha256", {})),
    }
    if "build_info" in status:
        fp["build_info"] = dict(status["build_info"])
    return fp


def canonical_part(fp: dict[str, Any]) -> dict[str, Any]:
    return {k: fp.get(k) for k in CANONICAL_KEYS}


def load_platform(path: Path = PLATFORM_JSON) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def platform_label(fp: dict[str, Any], path: Path = PLATFORM_JSON) -> str:
    """``canonical``, ``canonical-draft`` or ``dev`` for fingerprint ``fp``."""
    ref = load_platform(path)
    if ref is None or canonical_part(fp) != canonical_part(ref["fingerprint"]):
        return "dev"
    return "canonical-draft" if ref.get("status") == "draft" else "canonical"


# ---------------------------------------------------------------------------
# Container info + PLATFORM.json draft
# ---------------------------------------------------------------------------


def container_info(image: str, runtime: str | None = None) -> tuple[str, dict[str, Any]]:
    """(image_id, info) from ``entrypoint_eval.sh info`` under the isolation flags."""
    import sandbox

    rt = runtime or sandbox.detect_runtime()
    image_id = sandbox.resolve_image_id(image, runtime=rt)
    proc = subprocess.run(
        [rt, "run", *sandbox.isolation_flags(1), image_id, "info"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise sandbox.SandboxError(f"info failed rc={proc.returncode}: {proc.stderr[-500:]}")
    return image_id, json.loads(proc.stdout)


def _os_release() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _first_line(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = proc.stdout.splitlines()
    return lines[0].strip() if lines else ""


def build_draft(image: str, runtime: str | None = None) -> dict[str, Any]:
    image_id, info = container_info(image, runtime=runtime)
    fp = from_status(info, backend="sandbox", image=image, image_id=image_id)
    return {
        "status": "draft",
        "note": (
            "Draft canonical platform for the v2 study. A result counts as canonical "
            "only if its fingerprint matches every key in canonical_keys and this file "
            "is final (status != draft). Written by scripts/platform_fingerprint.py."
        ),
        "recorded_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canonical_keys": list(CANONICAL_KEYS),
        "fingerprint": fp,
        "host": {
            "os": _os_release(),
            "architecture": _platform.machine(),
            "logical_cpus": os.cpu_count(),
            "container_runtime": _first_line(
                [runtime or os.environ.get("CONTAINER_RUNTIME") or "docker", "--version"]
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    import sandbox

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--image", default=sandbox.DEFAULT_IMAGE)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true", help="print the container fingerprint")
    g.add_argument("--write-draft", type=Path, metavar="PATH", help="write a PLATFORM.json draft")
    args = p.parse_args(argv)
    draft = build_draft(args.image)
    if args.show:
        print(json.dumps(draft["fingerprint"], indent=2, sort_keys=True))
        return 0
    args.write_draft.parent.mkdir(parents=True, exist_ok=True)
    args.write_draft.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.write_draft}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
