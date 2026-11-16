#!/usr/bin/env bash
# T6 setup: private registry copy so a benchmark run can never touch a production trial.
set -euo pipefail
exec bash "$(dirname "$0")/../_shared/setup_scratch_registry.sh" "$1"
