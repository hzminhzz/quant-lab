#!/usr/bin/env bash
# T4 setup: private registry copy, read-only data view, shared virtualenv.
set -euo pipefail
WT="$1"
ROOT="/home/quant/dev/quant-lab"

bash "$(dirname "$0")/../_shared/setup_scratch_registry.sh" "$WT"

if [ ! -e "$WT/.venv" ]; then
  ln -s "$ROOT/.venv" "$WT/.venv"
fi
mkdir -p "$WT/.workspace/hypotheses/registered" "$WT/experiments/active/bench"
