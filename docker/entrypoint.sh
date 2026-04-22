#!/bin/sh
# SwarmEvolve sandbox entrypoint (M8).
#
# Expected layout at container startup:
#   /work/src/a.cpp   — Team A AI source (namespace TeamA { ... })  [read-only bind]
#   /work/src/b.cpp   — Team B AI source (namespace TeamB { ... })  [read-only bind]
#   /work/out         — writable output directory (one per run)      [rw bind]
#
# Steps:
#   1. Copy AI sources into the writable tmpfs so the injector can emit
#      guard-annotated copies without touching the read-only mount.
#   2. Run scripts/inject_guards.py on both sources (idempotent).
#   3. Compile engine + injected AIs with `-Werror`.
#   4. Execute the match with `--record /work/out/trace.jsonl` if caller
#      did not pass `--no-record`.
#   5. Emit /work/out/sandbox_status.json with high-level outcome.
#
# Any step failure writes sandbox_status.json with status != "ok" and exits
# with a non-zero code. The Python wrapper (scripts/sandbox.py) parses the
# status file to surface structured errors to the orchestrator.

set -eu

OUT=/work/out
SRC_RO=/work/src
TMP=/tmp/sbx
ASSETS=/opt/swarmevolve

mkdir -p "$TMP/src/a" "$TMP/src/b" "$OUT"

# Fail fast if required inputs missing.
if [ ! -f "$SRC_RO/a.cpp" ]; then
    printf '{"status":"invalid_input","reason":"missing /work/src/a.cpp"}\n' >"$OUT/sandbox_status.json"
    echo "entrypoint: missing /work/src/a.cpp" >&2
    exit 2
fi
if [ ! -f "$SRC_RO/b.cpp" ]; then
    printf '{"status":"invalid_input","reason":"missing /work/src/b.cpp"}\n' >"$OUT/sandbox_status.json"
    echo "entrypoint: missing /work/src/b.cpp" >&2
    exit 2
fi

cp "$SRC_RO/a.cpp" "$TMP/src/a/ai.orig.cpp"
cp "$SRC_RO/b.cpp" "$TMP/src/b/ai.orig.cpp"

# Copy the engine + headers into the tmpfs tree. The baselines and headers
# are baked into the image so a malicious mount can't swap them out.
cp "$ASSETS/engine.cpp" "$TMP/src/engine.cpp"
cp "$ASSETS/engine.h"   "$TMP/src/engine.h"
cp "$ASSETS/types.h"    "$TMP/src/types.h"
cp "$ASSETS/ai_abi.h"   "$TMP/src/ai_abi.h"

# ---- 1. Inject loop guards. ---------------------------------------------
# The injector has no --output flag; it emits to stdout with --stdout and
# rewrites in place otherwise. Use --stdout so the original inputs stay
# untouched (makes debugging easier — inject_*.log captures failure mode).
set +e
python3 "$ASSETS/inject_guards.py" --stdout "$TMP/src/a/ai.orig.cpp" \
        >"$TMP/src/a/ai.cpp" 2>"$OUT/inject_a.log"
RC_A=$?
python3 "$ASSETS/inject_guards.py" --stdout "$TMP/src/b/ai.orig.cpp" \
        >"$TMP/src/b/ai.cpp" 2>"$OUT/inject_b.log"
RC_B=$?
set -e

if [ "$RC_A" -ne 0 ] || [ "$RC_B" -ne 0 ]; then
    printf '{"status":"inject_failed","rc_a":%d,"rc_b":%d}\n' "$RC_A" "$RC_B" \
        >"$OUT/sandbox_status.json"
    exit 3
fi

# ---- 2. Compile. --------------------------------------------------------
set +e
g++ -std=c++17 -O2 \
    -Wall -Wextra -Wshadow -Wpedantic -Werror \
    -Wno-unknown-pragmas \
    -I"$TMP/src" \
    "$TMP/src/engine.cpp" "$TMP/src/a/ai.cpp" "$TMP/src/b/ai.cpp" \
    -o "$TMP/swarmevolve" \
    >"$OUT/compile.log" 2>&1
RC_C=$?
set -e

if [ "$RC_C" -ne 0 ]; then
    printf '{"status":"compile_failed","rc":%d}\n' "$RC_C" \
        >"$OUT/sandbox_status.json"
    exit 4
fi

# ---- 3. Run the engine. -------------------------------------------------
# All remaining args are forwarded verbatim (--seed / --max-ticks /
# --drones-a / --drones-b / --record / --max-messages / etc.).
# If the caller didn't pass --record, add one pointing at /work/out.
HAS_RECORD=0
for arg in "$@"; do
    if [ "$arg" = "--record" ] || [ "$arg" = "--no-record" ]; then
        HAS_RECORD=1
        break
    fi
done
if [ "$HAS_RECORD" -eq 0 ]; then
    set -- "$@" --record "$OUT/trace.jsonl"
fi

set +e
"$TMP/swarmevolve" "$@" >"$OUT/engine.stdout.log" 2>"$OUT/engine.stderr.log"
RC_E=$?
set -e

if [ "$RC_E" -eq 0 ] || [ "$RC_E" -eq 1 ] || [ "$RC_E" -eq 2 ]; then
    # 0=A_WIN, 1=B_WIN, 2=DRAW — all are valid terminations from engine.cpp.
    printf '{"status":"ok","engine_rc":%d}\n' "$RC_E" >"$OUT/sandbox_status.json"
    exit 0
fi

printf '{"status":"engine_crashed","engine_rc":%d}\n' "$RC_E" \
    >"$OUT/sandbox_status.json"
exit 5
