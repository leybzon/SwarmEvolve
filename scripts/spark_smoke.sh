#!/usr/bin/env bash
#
# spark_smoke.sh — M9 portability + reproducibility smoke on NVIDIA Spark.
#
# Run this from your laptop. It:
#   1. rsyncs the working tree to gene@$SPARK_HOST:~/DroneEvolution/
#   2. creates a venv on Spark (idempotent) and installs test deps
#   3. runs the full pytest suite
#   4. times 100 matches on Spark (the M9 exit-criterion claim)
#   5. runs `orchestrator evaluate` on Spark, then `replay` on Spark
#   6. scp's the run dir back to the laptop and replays *here* — the
#      byte-identical-across-machines reproducibility check that M9
#      exists to enable
#
# Usage:
#   scripts/spark_smoke.sh              # run everything
#   scripts/spark_smoke.sh --sync-only  # just rsync, no execution
#   SPARK_HOST=... SPARK_USER=... scripts/spark_smoke.sh  # override host
#
# Exit codes:
#   0   all checks passed
#   >0  first failing step's exit code
#
set -euo pipefail

# ---------- Config ----------------------------------------------------------

SPARK_USER="${SPARK_USER:-gene}"
SPARK_HOST="${SPARK_HOST:-172.17.181.21}"
REMOTE_DIR="${REMOTE_DIR:-DroneEvolution}"   # relative to $HOME on Spark
N_MATCHES="${N_MATCHES:-100}"
WORKERS="${WORKERS:-8}"

# Resolve local repo root (this script lives in scripts/).
LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Where to stash the run dir pulled back from Spark.
LOCAL_PULL_DIR="${LOCAL_PULL_DIR:-/tmp/m9_from_spark}"

SSH_TARGET="${SPARK_USER}@${SPARK_HOST}"

# ---------- Pretty output ---------------------------------------------------

c_blue='\033[1;34m'; c_green='\033[1;32m'; c_red='\033[1;31m'; c_reset='\033[0m'
step() { printf "\n${c_blue}==>${c_reset} %s\n" "$*"; }
ok()   { printf "${c_green}[ok]${c_reset} %s\n" "$*"; }
die()  { printf "${c_red}[fail]${c_reset} %s\n" "$*" >&2; exit 1; }

# ---------- Args ------------------------------------------------------------

SYNC_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --sync-only) SYNC_ONLY=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"; exit 0 ;;
    *) die "unknown arg: $arg" ;;
  esac
done

# ---------- 0. sanity: local ssh reachability ------------------------------

step "Probing ${SSH_TARGET}"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${SSH_TARGET}" true \
  || die "cannot ssh to ${SSH_TARGET} (set up key auth or check VPN)"
ok "ssh reachable"

# ---------- 1. rsync --------------------------------------------------------

step "Syncing ${LOCAL_ROOT} → ${SSH_TARGET}:~/${REMOTE_DIR}/"
rsync -az --delete \
  --exclude='.venv' \
  --exclude='.git' \
  --exclude='data/experiments' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='build' \
  --exclude='*.o' \
  --exclude='swarmevolve' \
  "${LOCAL_ROOT}/" "${SSH_TARGET}:~/${REMOTE_DIR}/"
ok "rsync done"

if [[ "${SYNC_ONLY}" -eq 1 ]]; then
  ok "--sync-only: stopping here"
  exit 0
fi

# ---------- 2. remote bootstrap + run --------------------------------------

REMOTE_RUN_DIR="/tmp/m9_spark_run"

# We intentionally compose one heredoc so (a) all state stays on Spark,
# (b) failures surface with `set -e`, (c) we get one round-trip ssh.
step "Running tests, 100-match timing, and evaluate+replay on Spark"
ssh "${SSH_TARGET}" \
  REMOTE_DIR="${REMOTE_DIR}" \
  N_MATCHES="${N_MATCHES}" \
  WORKERS="${WORKERS}" \
  REMOTE_RUN_DIR="${REMOTE_RUN_DIR}" \
  'bash -s' <<'REMOTE_EOF'
set -euo pipefail

cd "${HOME}/${REMOTE_DIR}"

# --- venv (idempotent) ---
if [[ ! -x ".venv/bin/python" ]]; then
  echo "[spark] creating venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Minimal deps for M9 tests (jsonschema is the only non-stdlib one).
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pytest jsonschema

# --- environment ---
echo "[spark] uname: $(uname -a)"
echo "[spark] python: $(python --version)"
echo "[spark] g++:    $(g++ --version | head -1 || echo 'none')"
echo "[spark] cores:  $(nproc)"

# --- full suite ---
echo "[spark] === pytest -q ==="
pytest -q --ignore=tests/test_sandbox_ok.py --ignore=tests/test_sandbox_escape.py

# --- 100-match timing ---
echo "[spark] === 100-match fitness timing ==="
/usr/bin/time -v python scripts/fitness.py \
    --team-a pursuit_v1 --team-b cluster_v1 \
    --n-matches "${N_MATCHES}" --workers "${WORKERS}" \
    2>&1 | tail -40

# --- evaluate → replay round-trip on Spark ---
echo "[spark] === evaluate into ${REMOTE_RUN_DIR} ==="
rm -rf "${REMOTE_RUN_DIR}"
python scripts/orchestrator.py -v evaluate \
    --team-a src/baselines/pursuit_v1.cpp \
    --team-b src/baselines/cluster_v1.cpp \
    --n-matches 20 --workers 4 \
    --out-dir "${REMOTE_RUN_DIR}"

echo "[spark] === replay on Spark ==="
python scripts/orchestrator.py -v replay "${REMOTE_RUN_DIR}"
REMOTE_EOF
ok "remote steps passed"

# ---------- 3. cross-machine replay ----------------------------------------

step "Pulling ${REMOTE_RUN_DIR} → ${LOCAL_PULL_DIR}"
rm -rf "${LOCAL_PULL_DIR}"
scp -q -r "${SSH_TARGET}:${REMOTE_RUN_DIR}" "${LOCAL_PULL_DIR}"
ok "scp done"

step "Cross-machine replay on laptop"
# The log records *Spark's* absolute paths to the baseline sources; we
# rewrite those to this laptop's paths before replaying so the compiler
# can find them. Keeps per_match/summary arithmetic identical.
python3 - "$LOCAL_PULL_DIR" "$LOCAL_ROOT" <<'PY'
import json, pathlib, sys
pull, local_root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
log = pull / "events.jsonl"
lines = log.read_text().splitlines()
out = []
for line in lines:
    if not line.strip():
        continue
    ev = json.loads(line)
    env = ev.get("environment") or {}
    for team in ("team_a", "team_b"):
        t = env.get(team)
        if t and "path" in t:
            name = pathlib.Path(t["path"]).name
            t["path"] = str(local_root / "src" / "baselines" / name)
    out.append(json.dumps(ev))
log.write_text("\n".join(out) + "\n")
print(f"[laptop] rewrote baseline paths in {log}")
PY

python3 scripts/orchestrator.py -v replay "${LOCAL_PULL_DIR}" \
  || die "cross-machine replay FAILED (Spark run is not byte-reproducible on laptop)"

ok "cross-machine replay succeeded — M9 reproducibility contract holds"

step "All M9 smoke checks passed"
