#!/usr/bin/env bash
# scripts/herdr_dispatch.sh
# Dispatches long-running warden commands to a Herdr worker pane when available,
# falling back to synchronous execution otherwise.

set -euo pipefail

PANE="w1:p2"
TIMEOUT="300000"  # 5 minutes in ms
COMMAND=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pane)
            PANE="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        *)
            if [[ -z "$COMMAND" ]]; then
                COMMAND="$1"
            else
                COMMAND="$COMMAND $1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$COMMAND" ]]; then
    echo "Usage: $0 [--pane <pane_id>] [--timeout <ms>] <command...>" >&2
    exit 1
fi

if [[ "${HERDR_ENV:-0}" == "1" ]] && command -v herdr >/dev/null 2>&1; then
    echo "[herdr_dispatch] Dispatching to Herdr pane '$PANE': $COMMAND"
    herdr pane run "$PANE" "$COMMAND"
    herdr pane wait-output "$PANE" --regex "WARDEN_RUN_COMPLETE|WARDEN_RUN_FAILED" --timeout "$TIMEOUT"
    herdr pane read "$PANE" --lines 60
else
    echo "[herdr_dispatch] HERDR_ENV not set or herdr CLI not found; running synchronously"
    eval "$COMMAND"
fi
