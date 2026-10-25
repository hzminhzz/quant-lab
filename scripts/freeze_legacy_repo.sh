#!/usr/bin/env bash
# scripts/freeze_legacy_repo.sh
# Non-destructive helper to mark legacy ~/quant-research repository read-only.
# Default mode is --dry-run. Autonomous agents must NEVER run with --apply.

set -euo pipefail

TARGET_DIR="${1:-$HOME/quant-research}"
MODE="${2:---dry-run}"

if [[ "$MODE" == "--dry-run" ]] || [[ "$#" -eq 0 ]]; then
    echo "=== LEGACY REPOSITORY FREEZE (DRY RUN) ==="
    echo "Target directory: $TARGET_DIR"
    if [[ -d "$TARGET_DIR" ]]; then
        echo "Status: Directory exists."
        echo "Dry run preview: The following command would be executed by the human trader:"
        echo "  chmod -R a-w '$TARGET_DIR'"
    else
        echo "Status: Directory does not exist on this host ($TARGET_DIR)."
        echo "Dry run preview: No action required."
    fi
    echo "Result: DRY RUN COMPLETE (zero changes made to filesystem)."
    exit 0
fi

if [[ "$MODE" == "--apply" ]]; then
    echo "HUMAN APPROVAL GATE: Applying read-only freeze to legacy repository."
    if [[ ! -t 0 ]]; then
        echo "Error: Interactive terminal required for --apply." >&2
        exit 1
    fi
    read -r -p "Type 'YES_FREEZE_LEGACY' to mark $TARGET_DIR read-only: " CONFIRM
    if [[ "$CONFIRM" != "YES_FREEZE_LEGACY" ]]; then
        echo "Aborted: Confirmation mismatch." >&2
        exit 1
    fi
    if [[ -d "$TARGET_DIR" ]]; then
        chmod -R a-w "$TARGET_DIR"
        echo "Legacy repository frozen: chmod -R a-w '$TARGET_DIR' applied successfully."
    else
        echo "Warning: Target directory '$TARGET_DIR' not found."
    fi
    exit 0
fi

echo "Usage: $0 [<target_dir>] [--dry-run|--apply]" >&2
exit 1
