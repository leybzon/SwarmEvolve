#!/usr/bin/env python3
"""Single source of truth for C++ compile flags (ADR-0001).

Every build of the engine — the evolutionary sandbox (``fitness.py``),
the orchestrator, the test helpers, the Makefile and the sandbox
container's ``docker/entrypoint.sh`` — takes its flags from here, so a
candidate that compiles on the host also compiles in the sandbox.

Flag sets
---------
``STD`` / ``OPTIMIZE``
    Language standard and optimisation level for match binaries.
``WARNINGS``
    The strict warning policy: ``-Werror`` on everything below.
``OPENACC_PRAGMAS``
    ``#pragma acc`` must be ignored silently by non-OpenACC compilers.
    nvc++ understands the pragma and rejects the flag, so it is omitted
    for that family.
``UNUSED_RELAXED``
    ADR-0001 §1: unused-symbol warnings are style, not correctness, and
    stay off for LLM-authored candidates.
``GCC_FALSE_POSITIVES``
    GCC-only. At -O2, GCC 13+ reports ``-Wmaybe-uninitialized`` on the
    stack-allocated ``attack_events`` buffer that ``write_trace_line_v2``
    receives with an event count of 0 at tick 0 (it is never read), and
    ``-Wstringop-overflow`` on memsets whose length is
    ``sizeof(T) * num_drones_*`` (an int the optimiser cannot prove
    non-negative). Clang rejects these GCC-specific names, so they are
    added for the GCC family only.
``FP_DETERMINISM``
    Evaluation builds only (``eval_flags``): ``-ffp-contract=off`` stops
    the compiler fusing a*b+c into an FMA, whose single rounding would
    make match outcomes depend on the target's FMA support. Both fitness
    backends and the eval container's batch mode compile with it.

The CLI prints the flags for one compiler, space-separated, for use from
make and sh::

    python3 scripts/compile_flags.py --compiler g++
    python3 scripts/compile_flags.py --compiler clang++ --no-optimize
    python3 scripts/compile_flags.py --compiler g++ --eval

Stdlib only, and compatible with the sandbox image's Python 3.10.
"""

from __future__ import annotations

import argparse
import functools
import subprocess
import sys
from pathlib import Path

STD: tuple[str, ...] = ("-std=c++17",)
OPTIMIZE: tuple[str, ...] = ("-O2",)
WARNINGS: tuple[str, ...] = ("-Wall", "-Wextra", "-Wshadow", "-Wpedantic", "-Werror")
OPENACC_PRAGMAS: tuple[str, ...] = ("-Wno-unknown-pragmas",)
UNUSED_RELAXED: tuple[str, ...] = (
    "-Wno-unused-variable",
    "-Wno-unused-parameter",
    "-Wno-unused-function",
    "-Wno-unused-but-set-variable",
    "-Wno-unused-const-variable",
)
GCC_FALSE_POSITIVES: tuple[str, ...] = ("-Wno-maybe-uninitialized", "-Wno-stringop-overflow")
FP_DETERMINISM: tuple[str, ...] = ("-ffp-contract=off",)

FAMILIES = ("gcc", "clang", "nvhpc", "unknown")


@functools.cache
def compiler_family(compiler: str) -> str:
    """Classify ``compiler`` as gcc, clang, nvhpc or unknown.

    Asks the compiler itself (``--version``) so that aliases such as
    ``c++`` resolve correctly; falls back to the executable name when the
    compiler cannot be run.
    """
    try:
        proc = subprocess.run(
            [compiler, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        banner = (proc.stdout + proc.stderr).lower()
    except (OSError, subprocess.TimeoutExpired):
        banner = ""
    if banner:
        if "clang" in banner:
            return "clang"
        if "nvc++" in banner or "nvidia" in banner or "nvhpc" in banner:
            return "nvhpc"
        if "free software foundation" in banner or "gcc" in banner or "g++" in banner:
            return "gcc"
    name = Path(compiler).name.lower()
    if "clang" in name:
        return "clang"
    if name.startswith("nvc"):
        return "nvhpc"
    if "g++" in name or "gcc" in name:
        return "gcc"
    return "unknown"


def strict_warning_flags(compiler: str) -> list[str]:
    """``WARNINGS`` plus the per-family adjustments, without the ADR-0001
    relaxations. Used where the code under test is not LLM-authored and
    must be warning-clean (e.g. guard-injector output checks)."""
    family = compiler_family(compiler)
    flags = list(WARNINGS)
    if family != "nvhpc":
        flags += OPENACC_PRAGMAS
    if family == "gcc":
        flags += GCC_FALSE_POSITIVES
    return flags


def warning_flags(compiler: str) -> list[str]:
    """Warning policy for builds that include AI sources (ADR-0001)."""
    flags = strict_warning_flags(compiler)
    if compiler_family(compiler) != "nvhpc":
        # nvc++ acceptance of the GCC-style -Wno-unused-* names has not
        # been verified; the GPU build keeps its historical warning set.
        flags += UNUSED_RELAXED
    return flags


def engine_flags(compiler: str, *, optimize: bool = True) -> list[str]:
    """Full flag list for compiling the engine plus two AI sources.

    Callers add include paths, defines, inputs and ``-o``.
    """
    return [*STD, *(OPTIMIZE if optimize else ()), *warning_flags(compiler)]


def eval_flags(compiler: str) -> list[str]:
    """``engine_flags`` plus ``FP_DETERMINISM``: the flags every evaluation
    build uses, on the host and in the eval container alike."""
    return [*engine_flags(compiler), *FP_DETERMINISM]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Print the shared C++ compile flags.")
    p.add_argument("--compiler", required=True, help="compiler executable, e.g. g++")
    p.add_argument(
        "--no-optimize",
        action="store_true",
        help="omit the optimisation level (caller supplies its own, e.g. -O0 -g)",
    )
    p.add_argument("--family", action="store_true", help="print the compiler family only")
    p.add_argument(
        "--eval", action="store_true", help="print the evaluation flags (adds FP_DETERMINISM)"
    )
    args = p.parse_args(argv)
    if args.family:
        print(compiler_family(args.compiler))
    elif args.eval:
        if args.no_optimize:
            p.error("--eval and --no-optimize are mutually exclusive")
        print(" ".join(eval_flags(args.compiler)))
    else:
        print(" ".join(engine_flags(args.compiler, optimize=not args.no_optimize)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
