#!/usr/bin/env bash
# scripts/run_hourly_pipeline.sh
# Thin systemd/cron entrypoint delegating idempotent progression to `warden advance --pending`.
# A busy registry (exit 75) is a clean cron deferral, not a failed job: the queue is
# untouched and the next run retries nothing because advances are idempotent.

set -euo pipefail

REPO_DIR="${QUANT_LAB_REPO_DIR:-/home/quant/dev/quant-lab}"

export WARDEN_NON_INTERACTIVE=1
export QUANT_LAB_REPO_DIR="$REPO_DIR"
export QUANT_LAB_HOURLY_LOCK_FILE="${QUANT_LAB_HOURLY_LOCK_FILE:-/tmp/quant_lab_hourly.lock}"

cd "$REPO_DIR"

JSON_MODE=0
for arg in "$@"; do
    if [[ "$arg" == "--json" ]]; then
        JSON_MODE=1
    fi
done

exec 9>"$QUANT_LAB_HOURLY_LOCK_FILE"
if ! flock -n 9; then
    if [[ "$JSON_MODE" -eq 1 ]]; then
        echo '{"locked": true, "processed": 0, "outcomes": []}'
    else
        echo "Job already running. Exiting cleanly."
    fi
    exit 0
fi

set +e
OUTPUT="$(uv run --frozen warden advance --pending "$@" 2>&1)"
RC=$?
set -e

if [[ $RC -eq 75 ]]; then
    # Quiet busy: emit one structured document (or one line) and succeed, so cron does
    # not treat a concurrent writer — the CLI, MCP adapter or another session — as a fault.
    if [[ "$JSON_MODE" -eq 1 ]]; then
        echo '{"busy": true, "locked": false, "processed": 0, "advanced": 0, "blocked": 0, "outcomes": []}'
    else
        echo "Warden registry busy; queued work is untouched and will be retried on the next run."
    fi
    exit 0
fi

if [[ -n "$OUTPUT" ]]; then
    printf '%s\n' "$OUTPUT"
fi
exit $RC
