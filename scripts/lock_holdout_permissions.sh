#!/usr/bin/env bash
# Human-only. Never invoked by an autonomous rollout or test.
set -euo pipefail
TARGET="${1:-/data/holdout}"
[[ ${EUID} == 0 ]] || { echo "Human operator: sudo $0 [$TARGET]" >&2; exit 1; }
[[ -d "$TARGET" && ! -L "$TARGET" ]] || { echo "Expected real directory at '$TARGET'" >&2; exit 1; }
getent group trader >/dev/null || { echo 'Provision trader group first (e.g. sudo groupadd trader)' >&2; exit 1; }
chown -R root:trader "$TARGET"
find "$TARGET" -type d -exec chmod 0750 {} +
find "$TARGET" -type f -exec chmod 0640 {} +
count=$(find "$TARGET" -maxdepth 2 -type f -name "*.parquet" | wc -l)
echo "Holdout locked: directories root:trader 0750; $count parquet file(s) root:trader 0640 at '$TARGET'"
