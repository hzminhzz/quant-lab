#!/usr/bin/env bash
# scripts/run_hourly_pipeline.sh
# Production hourly pipeline with flock concurrency protection, hypothesis registration (supporting .md and .yaml),
# Stage 1 fail-fast gating, and strict non-interactive qualification guards.

set -euo pipefail

REPO_DIR="/home/quant/dev/quant-lab"
cd "$REPO_DIR"

# 1. Enforce non-interactive security guard (blocks autonomous holdout unsealing)
export WARDEN_NON_INTERACTIVE=1

# 2. Concurrency lock (prevents overlapping cron/timer jobs from racing on registry.db)
LOCK_FILE="/tmp/quant_lab_hourly.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date -u)] Job already running. Exiting cleanly."
    exit 0
fi

# 3. Ensure required directories exist
mkdir -p experiments/queue experiments/active/failed experiments/active/archive \
         .workspace/hypotheses/queue .workspace/hypotheses/registered .workspace/hypotheses/failed

# 4. Enforce protected contracts, warden core, and LOC budgets
uv run warden check-worktree

# 5. Pre-register any newly dropped hypotheses from .workspace/hypotheses/queue/ (.md, .yaml, .yml)
shopt -s nullglob
HYP_QUEUE=(.workspace/hypotheses/queue/*.md .workspace/hypotheses/queue/*.yaml .workspace/hypotheses/queue/*.yml)
for hyp in "${HYP_QUEUE[@]}"; do
    echo "[$(date -u)] Registering hypothesis: $hyp"
    if uv run warden register-hypothesis "$hyp" --json; then
        mv "$hyp" .workspace/hypotheses/registered/
    else
        echo "[ERROR] Failed Stage 0 feasibility or validation for $hyp"
        mv "$hyp" .workspace/hypotheses/failed/
    fi
done

# 6. Process experiment YAML specifications
QUEUE_SPECS=(experiments/queue/*.yaml experiments/queue/*.yml)
if [ ${#QUEUE_SPECS[@]} -eq 0 ]; then
    echo "[$(date -u)] No pending experiment specs in experiments/queue/."
    exit 0
fi

for spec in "${QUEUE_SPECS[@]}"; do
    echo "=== [$(date -u)] Processing $spec ==="
    
    # Step A: Prefix-Invariance Causality Proof
    if ! uv run warden verify "$spec" --json; then
        echo "[FAIL] Lookahead leakage or invalid pipeline: $spec"
        mv "$spec" experiments/active/failed/
        continue
    fi

    # Step B: Stage 1 Signal Triage Gate (Fail-Fast)
    if ! uv run warden screen "$spec" --json; then
        echo "[REVISE] Failed Stage 1 screen: $spec. Halting before Stage 2 backtest."
        mv "$spec" experiments/active/failed/
        continue
    fi

    # Step C: Stage 2 & 3 Walk-Forward Backtest, 2x Cost Stress, and DSR
    OUTPUT=$(uv run warden backtest "$spec" --json || true)
    echo "$OUTPUT"

    # Step D: Parse Results & Trigger Adversarial Audit for Qualifying Runs
    RUN_ID=$(echo "$OUTPUT" | grep -o '"run_id": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    VERDICT=$(echo "$OUTPUT" | grep -o '"verdict": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)

    if [ "$VERDICT" == "CANDIDATE_FOR_QUALIFICATION" ] && [ -n "$RUN_ID" ]; then
        echo ">>> CANDIDATE FOUND! Executing 6-check adversarial audit for $RUN_ID"
        uv run warden audit-check --run-id "$RUN_ID" --json
    fi

    # Move processed spec to active archive
    mv "$spec" experiments/active/archive/
done

# 7. Record atomic transition snapshot
uv run warden handoff --notes "Automated hourly execution completed: ${#QUEUE_SPECS[@]} spec(s) processed."
