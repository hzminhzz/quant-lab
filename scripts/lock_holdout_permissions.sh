#!/usr/bin/env bash
# Human-only. Never invoked by an autonomous rollout or test.
set -euo pipefail
[[ ${EUID} == 0 ]] || { echo 'Human operator: sudo ./scripts/lock_holdout_permissions.sh' >&2; exit 1; }
[[ -d /data/holdout && ! -L /data/holdout ]] || { echo 'Expected real /data/holdout directory' >&2; exit 1; }
getent group trader >/dev/null || { echo 'Provision trader group first' >&2; exit 1; }
chown -R root:trader /data/holdout
# Directories need search permission; chmod -R 0640 would make the data unreadable even to trader.
find /data/holdout -type d -exec chmod 0750 {} +
find /data/holdout -type f -exec chmod 0640 {} +
echo 'Holdout locked: directories root:trader 0750; files root:trader 0640'
