#!/usr/bin/env bash
# scripts/run_hourly_pipeline.sh
# Production hourly pipeline with flock concurrency protection, hypothesis registration (supporting .md and .yaml),
# Stage 1 fail-fast gating, and strict non-interactive qualification guards.

set -euo pipefail

REPO_DIR="${QUANT_LAB_REPO_DIR:-/home/quant/dev/quant-lab}"
cd "$REPO_DIR"

# 1. Enforce non-interactive security guard (blocks autonomous holdout unsealing)
export WARDEN_NON_INTERACTIVE=1

# 2. Concurrency lock (prevents overlapping cron/timer jobs from racing on registry.db)
LOCK_FILE="${QUANT_LAB_HOURLY_LOCK_FILE:-/tmp/quant_lab_hourly.lock}"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date -u)] Job already running. Exiting cleanly."
    exit 0
fi

# 3. Ensure required directories exist
mkdir -p experiments/queue experiments/active/failed experiments/active/archive experiments/active/review \
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

    # Reconcile first. Stage1/Stage3 evidence survives a crash or lost CLI response.
    # Never rerun a validation-consuming screen merely because its YAML is queued.
    STATUS=$(uv run warden spec-status "$spec" --json 2>&1) || true
    if [[ "$STATUS" != *'"source": "warden.spec-status"'* ]]; then
        echo "[REQUIRES_RECONCILIATION] Warden status unavailable for $spec"
        mv "$spec" experiments/active/review/
        continue
    fi
    SPEC_STAGE=$(echo "$STATUS" | grep -o '"state": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    if [ "$SPEC_STAGE" = "STAGE3_FINAL" ]; then
        RUN_ID=$(echo "$STATUS" | grep -o '"run_id": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
        VERDICT=$(echo "$STATUS" | grep -o '"verdict": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
        echo "[ALREADY_RECORDED] $spec: $RUN_ID $VERDICT"
        if [ "$VERDICT" = "CANDIDATE_FOR_QUALIFICATION" ]; then
            if ! uv run warden audit-check --run-id "$RUN_ID" --json; then
                mv "$spec" experiments/active/review/
                continue
            fi
        fi
        mv "$spec" experiments/active/archive/
        continue
    fi
    if [ "$SPEC_STAGE" = "STAGE1_REJECT" ]; then
        echo "[STAGE1_REJECT] Existing Warden screen rejection: $spec"
        mv "$spec" experiments/active/failed/
        continue
    fi
    if [ "$SPEC_STAGE" != "NEW" ] && [ "$SPEC_STAGE" != "STAGE1_PASS" ]; then
        echo "[REQUIRES_RECONCILIATION] $spec: $SPEC_STAGE"
        mv "$spec" experiments/active/review/
        continue
    fi

    if [ "$SPEC_STAGE" = "NEW" ]; then
        # Off-main memory cannot override native SQLite; block genuine replay and
        # unresolved source/contract conflicts before making a new reservation.
        if ! uv run --frozen python -m scripts.research_outcome_memory --check-spec "$spec"; then
            echo "[REVIEW] Existing conflicting research evidence: $spec"
            mv "$spec" experiments/active/review/
            continue
        fi

        if ! uv run warden verify "$spec" --json; then
            echo "[REVIEW] Causality or provenance check failed: $spec"
            mv "$spec" experiments/active/review/
            continue
        fi

        SCREEN_EXIT=0
        SCREEN_OUTPUT=$(uv run warden screen "$spec" --json 2>&1) || SCREEN_EXIT=$?
        echo "$SCREEN_OUTPUT"
        SCREEN_STATUS=$(uv run warden spec-status "$spec" --json 2>&1) || true
        if [[ "$SCREEN_STATUS" != *'"source": "warden.spec-status"'* ]]; then
            echo "[REQUIRES_RECONCILIATION] Stage1 result could not be checked: $spec"
            mv "$spec" experiments/active/review/
            continue
        fi
        SCREEN_STAGE=$(echo "$SCREEN_STATUS" | grep -o '"state": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
        if [ "$SCREEN_STAGE" = "STAGE1_REJECT" ]; then
            echo "[STAGE1_REJECT] $spec (screen process exit=$SCREEN_EXIT)"
            mv "$spec" experiments/active/failed/
            continue
        fi
        if [ "$SCREEN_STAGE" != "STAGE1_PASS" ]; then
            echo "[REQUIRES_RECONCILIATION] Stage1 $SCREEN_STAGE: $spec (exit=$SCREEN_EXIT)"
            mv "$spec" experiments/active/review/
            continue
        fi
    else
        echo "[STAGE1_RESUME] Reusing committed screen PASS; not screening again."
    fi

    # Step C: Stage 2 & 3 Walk-Forward Backtest, 2x Cost Stress, and DSR
    BACKTEST_EXIT=0
    OUTPUT=$(uv run warden backtest "$spec" --json 2>&1) || BACKTEST_EXIT=$?
    echo "$OUTPUT"

    # Step D: The immutable SQLite+Parquet receipt, not CLI exit/sentinel,
    # decides finality. A lost caller response can recover without re-running.
    RUN_ID=$(echo "$OUTPUT" | grep -o '"run_id": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    VERDICT=$(echo "$OUTPUT" | grep -o '"verdict": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    # Caller stdout can disappear *after* Warden atomically commits the trial.
    # Resolve its immutable identity by spec, then verify the exact receipt.
    AFTER=$(uv run warden spec-status "$spec" --json 2>&1) || true
    if [[ "$AFTER" != *'"source": "warden.spec-status"'* ]] ||
       [[ "$AFTER" != *'"state": "STAGE3_FINAL"'* ]]; then
        echo "[REQUIRES_RECONCILIATION] No finalized trial for spec after backtest: $spec"
        mv "$spec" experiments/active/review/
        continue
    fi
    COMMITTED_ID=$(echo "$AFTER" | grep -o '"run_id": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    if [ -z "$COMMITTED_ID" ] || { [ -n "$RUN_ID" ] && [ "$RUN_ID" != "$COMMITTED_ID" ]; }; then
        echo "[REQUIRES_RECONCILIATION] CLI and immutable spec run IDs disagree: $spec"
        mv "$spec" experiments/active/review/
        continue
    fi
    RUN_ID="$COMMITTED_ID"
    RECEIPT=""
    if [ -n "$RUN_ID" ]; then
        RECEIPT=$(uv run warden attempt-status --run-id "$RUN_ID" --json 2>&1) || true
    fi
    LEDGER_VERDICT=$(echo "$RECEIPT" | grep -o '"verdict": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    if [[ "$RECEIPT" != *'"source": "warden.attempt-status"'* ]] ||
       [[ "$RECEIPT" != *'"state": "FINALIZED"'* ]] ||
       [[ "$RECEIPT" != *'"eligible": true'* ]] ||
       [ -z "$LEDGER_VERDICT" ] ||
       { [ -n "$VERDICT" ] && [ "$VERDICT" != "$LEDGER_VERDICT" ]; }; then
        echo "[REQUIRES_RECONCILIATION] No matching final Warden receipt: $spec (exit=$BACKTEST_EXIT)"
        mv "$spec" experiments/active/review/
        continue
    fi
    VERDICT="$LEDGER_VERDICT"
    if [ "$VERDICT" = "FAILED" ] || [ "$VERDICT" = "REJECTED_EARLY" ]; then
        echo "[REQUIRES_RECONCILIATION] Recorded incomplete/early verdict: $RUN_ID $VERDICT"
        mv "$spec" experiments/active/review/
        continue
    fi

    if [ "$VERDICT" == "CANDIDATE_FOR_QUALIFICATION" ]; then
        echo ">>> CANDIDATE FOUND! Executing 6-check adversarial audit for $RUN_ID"
        if ! uv run warden audit-check --run-id "$RUN_ID" --json; then
            echo "[REQUIRES_RECONCILIATION] Audit incomplete/negative: $RUN_ID; backtest result remains recorded"
            mv "$spec" experiments/active/review/
            continue
        fi
    fi

    # Archive only a Warden-reported successful final backtest.
    mv "$spec" experiments/active/archive/
done

# 7. Record atomic transition snapshot
uv run warden handoff --notes "Automated hourly execution completed: ${#QUEUE_SPECS[@]} spec(s) processed."
