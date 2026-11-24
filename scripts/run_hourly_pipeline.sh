#!/usr/bin/env bash
# scripts/run_hourly_pipeline.sh
# Thin systemd/cron entrypoint delegating idempotent progression to `warden advance --pending`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${QUANT_LAB_REPO_DIR:-/home/quant/dev/quant-lab}"

export WARDEN_NON_INTERACTIVE=1
export QUANT_LAB_REPO_DIR="$REPO_DIR"
export QUANT_LAB_HOURLY_LOCK_FILE="${QUANT_LAB_HOURLY_LOCK_FILE:-/tmp/quant_lab_hourly.lock}"

cd "$REPO_DIR"

exec 9>"$QUANT_LAB_HOURLY_LOCK_FILE"
if ! flock -n 9; then
    if [[ " $* " == *" --json "* ]]; then
        echo '{"locked": true, "processed": 0, "outcomes": []}'
    else
        echo "Job already running. Exiting cleanly."
    fi
    exit 0
fi

exec uv run --frozen warden advance --pending "$@"
