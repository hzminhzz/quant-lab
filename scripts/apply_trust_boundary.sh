#!/usr/bin/env bash
# Human-controlled trust-boundary cutover / rollback (issue-4 ticket 10).
# Default is --dry-run: it prints the plan and changes nothing.
# --apply validates EVERY precondition before the first chown/chmod, records the exact
# pre-apply ownership+mode, then applies. It is never run by the coding agent.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd -P)"

MODE="dry-run"
TRUST_GROUP="${WARDEN_TRUST_GROUP:-warden-trust}"
OPERATOR="${WARDEN_OPERATOR:-}"
AGENT_UID="${WARDEN_AGENT_UID:-}"
KEY_FILE="${WARDEN_HUMAN_KEY_FILE:-}"
STATE_DIR="${WARDEN_TRUST_STATE_DIR:-$REPO/.warden_state/trust_boundary}"
RECORD="$STATE_DIR/pre_apply.tsv"

usage() { sed -n '2,7p' "$0"; }

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

protected_paths() {
  {
    find src/warden -type f -name '*.py'
    find contracts -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \)
    find contracts/datasets -maxdepth 1 -type f -name '*.json'
    for f in AGENTS.md pyproject.toml uv.lock scripts/check_loc_budget.py \
             scripts/lock_holdout_permissions.sh scripts/lock_governance_permissions.sh \
             scripts/preflight_trust_boundary.py scripts/apply_trust_boundary.sh; do
      [[ -f "$f" ]] && echo "$f"
    done
  } | sort -u
}

failures=0
refuse() { echo "REFUSED: $*" >&2; failures=$((failures + 1)); }

inside_repo() { [[ "$(realpath -e -- "$1" 2>/dev/null || true)" == "$REPO"/* ]]; }

# --- validation: read-only, runs BEFORE any mutating chown/chmod -------------------
validate_preconditions() {
  failures=0
  [[ "$(id -u)" -eq 0 ]] || refuse "--apply requires root (uid 0); the coding agent must never run it"
  [[ "${WARDEN_HUMAN_APPROVED_CUTOVER:-0}" == "1" ]] || refuse "set WARDEN_HUMAN_APPROVED_CUTOVER=1 after recording explicit operator approval"
  [[ -n "$AGENT_UID" && "$AGENT_UID" =~ ^[0-9]+$ && "$AGENT_UID" -ne 0 ]] || refuse "declare the unprivileged agent uid in WARDEN_AGENT_UID (non-zero)"
  [[ -n "$OPERATOR" ]] || refuse "declare the evaluator/operator account in WARDEN_OPERATOR"
  if [[ -n "$OPERATOR" ]]; then
    id -u "$OPERATOR" >/dev/null 2>&1 || refuse "operator account '$OPERATOR' does not exist"
    [[ "$(id -u "$OPERATOR" 2>/dev/null || echo 1)" -ne 0 ]] || refuse "the operator must not be root"
    [[ "$(id -u "$OPERATOR" 2>/dev/null || echo 0)" -ne "${AGENT_UID:-0}" ]] || refuse "operator and agent identities must differ"
  fi
  getent group "$TRUST_GROUP" >/dev/null 2>&1 || refuse "trust group '$TRUST_GROUP' does not exist (create it first)"
  if [[ -n "$AGENT_UID" ]] && id -nG "$AGENT_UID" >/dev/null 2>&1; then
    if id -nG "$AGENT_UID" | tr ' ' '\n' | grep -qx -- "$TRUST_GROUP"; then
      refuse "the agent identity must not be a member of the trust group '$TRUST_GROUP'"
    fi
    for privileged in sudo docker root; do
      if id -nG "$AGENT_UID" | tr ' ' '\n' | grep -qx -- "$privileged"; then
        refuse "the agent identity is in privileged group '$privileged' and cannot be the trust boundary"
      fi
    done
  fi
  [[ -n "$KEY_FILE" ]] || refuse "set WARDEN_HUMAN_KEY_FILE to the offline human signing key"
  if [[ -n "$KEY_FILE" ]]; then
    [[ -f "$KEY_FILE" ]] || refuse "signing key '$KEY_FILE' is not a regular file"
    [[ "$(stat -c '%a' "$KEY_FILE" 2>/dev/null || echo 000)" == "600" ]] || refuse "signing key must be mode 0600"
    inside_repo "$KEY_FILE" && refuse "signing key must live outside the repository"
  fi
  [[ -e "$RECORD" ]] && refuse "a rollback record already exists at '$RECORD'; refusing to clobber previously recorded ownership"
  if [[ -d "$STATE_DIR" ]]; then
    [[ -w "$STATE_DIR" ]] || refuse "rollback state directory '$STATE_DIR' is not writable"
  else
    [[ -w "$(dirname "$STATE_DIR")" ]] || refuse "rollback state directory '$STATE_DIR' cannot be created"
  fi
  local path
  while IFS= read -r path; do
    inside_repo "$path" || refuse "protected path '$path' resolves outside the repository"
    [[ -e "$path" ]] || refuse "protected path '$path' does not exist"
  done < <(protected_paths)
  [[ "$failures" -eq 0 ]]
}

capture_rollback_record() {
  local tmp="$RECORD.tmp"
  : > "$tmp"
  local path
  while IFS= read -r path; do
    stat -c '%n\t%u\t%g\t%a' -- "$path" >> "$tmp"
  done < <(protected_paths)
  sha256sum "$tmp" | awk '{print $1}' > "$RECORD.sha256"
  mv "$tmp" "$RECORD"
  echo "Recorded pre-apply ownership+mode in $RECORD"
}

apply_plan() {
  local path
  while IFS= read -r path; do
    run chown -R "${OPERATOR}:${TRUST_GROUP}" "$path"
    run chmod -R go-w "$path"
  done < <(protected_paths)
  run chmod 0555 src/warden contracts
  run chmod 0444 .warden_manifest.sha256
  run chmod 0444 scripts/check_loc_budget.py
  run runuser -u "$OPERATOR" -- uv run --frozen --no-sync warden generate-manifest --signing-key-file "$KEY_FILE"
  run uv run --frozen --no-sync warden check-worktree
}

echo "=== warden trust-boundary cutover ($MODE) ==="
echo "operator: '${OPERATOR:-<undeclared>}'; agent uid: '${AGENT_UID:-<undeclared>}'; trust group: $TRUST_GROUP"

if [[ "$MODE" == "dry-run" ]]; then
  echo "--- precondition check (read-only) ---"
  if validate_preconditions; then
    echo "Preconditions satisfied: --apply would proceed."
  else
    echo "The cutover is BLOCKED until the REFUSED items above are resolved on the host."
  fi
  echo "--- plan (nothing is executed) ---"
  run chown -R "${OPERATOR:-<operator>}:${TRUST_GROUP}" "src/warden contracts .warden_manifest.sha256 scripts/check_loc_budget.py"
  run chmod -R go-w "src/warden contracts .warden_manifest.sha256 scripts/check_loc_budget.py"
  echo "DRY RUN: record pre-apply uid/gid/mode for all protected paths -> $RECORD (before any mutation)"
  echo "DRY RUN: chmod 0555 src/warden contracts"
  echo "DRY RUN: chmod 0444 .warden_manifest.sha256 scripts/check_loc_budget.py"
  echo "DRY RUN: (as ${OPERATOR:-<operator>}) uv run --frozen --no-sync warden generate-manifest --signing-key-file <offline key>"
  echo "DRY RUN: uv run warden check-worktree && uv run warden status --json"
  echo
  echo "Operator steps:"
  echo "  1. Create the unprivileged agent identity (no sudo/docker/trust groups): see docs/TRUST_BOUNDARY.md"
  echo "  2. Create the trust group and add only the human operator: groupadd $TRUST_GROUP"
  echo "  3. Export WARDEN_OPERATOR, WARDEN_AGENT_UID and WARDEN_HUMAN_KEY_FILE (0600, outside the repo)"
  echo "  4. WARDEN_HUMAN_APPROVED_CUTOVER=1 sudo -E scripts/apply_trust_boundary.sh --apply"
  echo "  5. Verify as the agent identity: uv run warden generate-manifest (must REFUSE),"
  echo "     python3 scripts/preflight_trust_boundary.py --json (must be COMPLIANT=true)"
  echo "Rollback: sudo scripts/apply_trust_boundary.sh --rollback (restores the recorded uid/gid/mode)"
  exit 0
fi

if [[ "$MODE" == "apply" ]]; then
  echo "--- validation before any mutation ---"
  if ! validate_preconditions; then
    echo "REFUSED: $failures precondition(s) failed; nothing was changed." >&2
    exit 3
  fi
  mkdir -p "$STATE_DIR"
  capture_rollback_record
  apply_plan
  exit 0
fi

# rollback: restore exactly what the apply recorded, never root-derived defaults
if [[ "$(id -u)" -ne 0 ]]; then
  echo "REFUSED: --rollback requires root; nothing changed." >&2
  exit 3
fi
if [[ ! -f "$RECORD" || ! -f "$RECORD.sha256" ]]; then
  echo "REFUSED: no rollback record at '$RECORD'; refusing to guess ownership." >&2
  exit 3
fi
if [[ "$(sha256sum "$RECORD" | awk '{print $1}')" != "$(cat "$RECORD.sha256")" ]]; then
  echo "REFUSED: rollback record digest mismatch; refusing to restore altered metadata." >&2
  exit 3
fi
while IFS=$'\t' read -r path uid gid mode; do
  [[ -e "$path" ]] || { echo "REFUSED: recorded path '$path' is missing." >&2; exit 3; }
  run chown "$uid:$gid" "$path"
  run chmod "$mode" "$path"
done < "$RECORD"
echo "Rollback complete from $RECORD. Signed manifest history and audit evidence are untouched."
