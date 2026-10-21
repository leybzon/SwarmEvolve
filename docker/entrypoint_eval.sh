#!/bin/sh
# SwarmEvolve eval-image entrypoint (v2 study).
#
#   batch [eval_batch.py args]   compile /work/src/{a,b}.cpp once and play a
#                                list of seeds; writes /work/out/results.jsonl
#                                and /work/out/status.json
#   info                         print the platform info as JSON
#   anything else                single match, as in the M8 sandbox image
#                                (docker/entrypoint.sh)
set -eu

ASSETS=/opt/swarmevolve

case "${1:-}" in
    batch)
        shift
        exec python3 "$ASSETS/eval_batch.py" \
            --src /work/src --out /work/out --work /tmp/eval \
            --engine-dir "$ASSETS" --harness-dir "$ASSETS" "$@"
        ;;
    info)
        exec python3 "$ASSETS/eval_batch.py" --info \
            --engine-dir "$ASSETS" --harness-dir "$ASSETS"
        ;;
    *)
        exec "$ASSETS/entrypoint.sh" "$@"
        ;;
esac
