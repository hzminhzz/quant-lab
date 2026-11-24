#!/usr/bin/env bash
# scripts/run_hourly_pipeline.sh
# Thin systemd/cron entrypoint delegating queue orchestration to scripts/hourly_queue_coordinator.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${QUANT_LAB_REPO_DIR:-/home/quant/dev/quant-lab}"

export WARDEN_NON_INTERACTIVE=1
export QUANT_LAB_REPO_DIR="$REPO_DIR"
export QUANT_LAB_HOURLY_LOCK_FILE="${QUANT_LAB_HOURLY_LOCK_FILE:-/tmp/quant_lab_hourly.lock}"

PYTHON_BIN="${QUANT_LAB_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x "$SCRIPT_DIR/../.venv/bin/python" ]]; then
        PYTHON_BIN="$SCRIPT_DIR/../.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/hourly_queue_coordinator.py" "$@"
