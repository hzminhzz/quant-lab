#!/usr/bin/env bash
# Human-controlled trust-boundary cutover / rollback (issue-4 ticket 10).
# Default is --dry-run: it prints the exact privileged plan and changes nothing.
# --apply requires BOTH: effective uid 0 AND WARDEN_HUMAN_APPROVED_CUTOVER=1.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="dry-run"
TRUST_GROUP="${WARDEN_TRUST_GROUP:-warden-trust}"
OPERATOR="${WARDEN_TRUST_OPERATOR:-root}"
PROTECTED=("src/warden" "contracts" ".warden_manifest.sha256" "scripts/check_loc_budget.py")

usage() { sed -n '2,6p' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --rollback) MODE="rollback"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

run() {
  if [[ "$MODE" == "dry-run" ]]; then echo "DRY RUN: $*"; else echo "+ $*"; "$@"; fi
}

echo "=== warden trust-boundary cutover ($MODE) ==="
echo "operator identity: $(id -un) uid=$(id -u); trust group: $TRUST_GROUP"
python3 scripts/preflight_trust_boundary.py || echo "(preflight reported non-compliance, expected before cutover)"

if [[ "$MODE" == "dry-run" ]]; then
  echo "--- plan (nothing is executed) ---"
  for path in "${PROTECTED[@]}"; do
    echo "DRY RUN: chown -R ${OPERATOR}:${TRUST_GROUP} ${path}"
    echo "DRY RUN: chmod -R go-w ${path}"
  done
  echo "DRY RUN: chmod 0555 src/warden contracts"
  echo "DRY RUN: chmod 0444 src/warden/*.py contracts/*.yaml contracts/datasets/*.json"
  echo "DRY RUN: chmod 0444 .warden_manifest.sha256 scripts/check_loc_budget.py"
  echo "DRY RUN: (as ${OPERATOR}, holding WARDEN_HUMAN_SIGNING_KEY) uv run warden generate-manifest"
  echo "DRY RUN: uv run warden check-worktree && uv run warden status --json"
  echo
  echo "Operator steps:"
  echo "  1. Review: scripts/preflight_trust_boundary.py --json"
  echo "  2. Create the trust group and add only the human operator: groupadd $TRUST_GROUP (once)"
  echo "  3. Export the human-held key OUTSIDE agent reach: WARDEN_HUMAN_SIGNING_KEY=..."
  echo "  4. WARDEN_HUMAN_APPROVED_CUTOVER=1 sudo -E scripts/apply_trust_boundary.sh --apply"
  echo "  5. Verify as the agent identity: uv run warden generate-manifest (must REFUSE),"
  echo "     python3 scripts/preflight_trust_boundary.py --json (must be COMPLIANT=true)"
  echo "Rollback: sudo scripts/apply_trust_boundary.sh --rollback (restores agent ownership; keeps history)"
  exit 0
fi

if [[ "$MODE" == "apply" ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "REFUSED: --apply requires root (uid 0). The coding agent must never run this." >&2
    exit 3
  fi
  if [[ "${WARDEN_HUMAN_APPROVED_CUTOVER:-0}" != "1" ]]; then
    echo "REFUSED: set WARDEN_HUMAN_APPROVED_CUTOVER=1 after recording explicit operator approval." >&2
    exit 3
  fi
  for path in "${PROTECTED[@]}"; do
    run chown -R "${OPERATOR}:${TRUST_GROUP}" "$path"
    run chmod -R go-w "$path"
  done
  run chmod 0555 src/warden contracts
  run chmod 0444 .warden_manifest.sha256 scripts/check_loc_budget.py
  if [[ -z "${WARDEN_HUMAN_SIGNING_KEY:-}" ]]; then
    echo "REFUSED: WARDEN_HUMAN_SIGNING_KEY must be present for the operator signature." >&2
    exit 3
  fi
  run uv run warden generate-manifest
  run uv run warden check-worktree
  exit 0
fi

# rollback: restore agent-writable working copy, keep signed manifest history
AGENT_UID="${WARDEN_AGENT_UID:-$(id -u)}"
AGENT_GID="${WARDEN_AGENT_GID:-$(id -g)}"
if [[ "$(id -u)" -ne 0 ]]; then
  echo "ROLLBACK DRY RUN: chown -R ${AGENT_UID}:${AGENT_GID} ${PROTECTED[*]}" >&2
  echo "REFUSED: --rollback requires root; nothing changed." >&2
  exit 3
fi
for path in "${PROTECTED[@]}"; do run chown -R "${AGENT_UID}:${AGENT_GID}" "$path"; done
echo "Rollback complete. The previous signed manifest and audit evidence are untouched."
