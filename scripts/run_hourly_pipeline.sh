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

    # Existing native Warden history AND open-PR scientific failures must
    # block automatic repeats; new data does not replenish trial budgets.
    # This check is read-only and fails closed if the index/registry is absent.
    if ! uv run --frozen python -m scripts.research_outcome_memory --check-spec "$spec"; then
        echo "[REJECTED/REVIEW] Already attempted, scientifically rejected, or unsupported lineage: $spec"
        mv "$spec" experiments/active/failed/
        continue
    fi

    # Steps A-C: verify (prefix-invariance), Stage 1 screen and Stage 2/3 backtest run in
    # ONE process (scripts/run_spec_session.py). Each stage keeps its own policy decision,
    # its own worktree integrity check, its own audit-log entry and its own exit code; only
    # the interpreter, the `uv run` sync and the registry handle are shared.
    SESSION_OUTPUT=$(uv run --frozen python -m scripts.run_spec_session "$spec" --json || true)
    echo "$SESSION_OUTPUT"
    if ! echo "$SESSION_OUTPUT" | grep -q '"ok"'; then
        echo "[ERROR] Spec session produced no result envelope (policy or worktree refusal): $spec"
        mv "$spec" experiments/active/failed/
        continue
    fi
    FAILED_STAGE=$(echo "$SESSION_OUTPUT" | grep -o '"failed_stage": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)

    case "$FAILED_STAGE" in
        verify)
            echo "[FAIL] Lookahead leakage or invalid pipeline: $spec"
            mv "$spec" experiments/active/failed/
            continue
            ;;
        screen)
            echo "[REVISE] Failed Stage 1 screen: $spec. Halting before Stage 2 backtest."
            mv "$spec" experiments/active/failed/
            continue
            ;;
        backtest)
            echo "[FAIL] Stage 2/3 backtest error: $spec"
            mv "$spec" experiments/active/failed/
            continue
            ;;
        "") ;;
        *)
            echo "[FAIL] Stage '$FAILED_STAGE' was refused or failed for $spec"
            mv "$spec" experiments/active/failed/
            continue
            ;;
    esac

    # Step D: Parse Results & Trigger Adversarial Audit for Qualifying Runs
    RUN_ID=$(echo "$SESSION_OUTPUT" | grep -o '"run_id": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)
    VERDICT=$(echo "$SESSION_OUTPUT" | grep -o '"verdict": *"[^"]*"' | head -n1 | cut -d'"' -f4 || true)

    if [ "$VERDICT" == "CANDIDATE_FOR_QUALIFICATION" ] && [ -n "$RUN_ID" ]; then
        echo ">>> CANDIDATE FOUND! Executing 6-check adversarial audit for $RUN_ID"
        uv run warden audit-check --run-id "$RUN_ID" --json
    fi

    # Move processed spec to active archive
    mv "$spec" experiments/active/archive/
done

# 7. Record atomic transition snapshot
uv run warden handoff --notes "Automated hourly execution completed: ${#QUEUE_SPECS[@]} spec(s) processed."
