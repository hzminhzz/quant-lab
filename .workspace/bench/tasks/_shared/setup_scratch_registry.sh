#!/usr/bin/env bash
# Prepare a scratch worktree: private registry copy + read-only data view.
# The registry is COPIED, never symlinked — a benchmark must not be able to burn a
# production trial, register a real hypothesis, or read a sealed holdout through a link.
set -euo pipefail
WT="$1"
ROOT="/home/quant/dev/quant-lab"

mkdir -p "$WT/registry"
cp -a "$ROOT/registry/." "$WT/registry/"

if [ ! -e "$WT/data" ]; then
  ln -s "$ROOT/data" "$WT/data"
fi

python - "$WT" <<'PY'
import json, sqlite3, sys
from pathlib import Path
wt = Path(sys.argv[1])
db = wt / "registry" / "registry.db"
counts = {}
with sqlite3.connect(db) as c:
    for t in ("hypotheses", "trials", "qualifications"):
        counts[t] = c.execute(f"select count(*) from {t}").fetchone()[0]
(wt / ".bench_baseline.json").write_text(json.dumps(counts))
print("baseline", counts)
PY
