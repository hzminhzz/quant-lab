# Provision the INDICES_1D_V1 holdout so Step 4 can run

Status: **proposal, not applied.** Nothing under `contracts/` or `data/holdout/` was changed.

## State

| | |
|---|---|
| Contract declares | `data/holdout/indices_1d.parquet` |
| Sealed directory | file absent |
| Staged copy | `data/holdout_staging/indices_1d.parquet`, sha256 `0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f` |
| Dataset record | `contracts/datasets/INDICES_1D_V1.json` has **no** `holdout_path` and **no** `holdout_sha256` keys |
| Contract window | 2025-07-01 -> 2026-10-26 |

## What actually blocks the read

Only the missing file. `resolve_dataset` raises `DatasetProvenanceError` when
`path.is_file()` is false, and nothing else.

The missing pin is **not** a refusal — it is worse. `provenance.py:62-66` reads
`exp_sha = record.get(f"{role}_sha256")`, and compares it only when it is not `None`. With no
`holdout_sha256` in the record, the digest check is skipped entirely and eligibility falls through to
the provenance fields, which match (`verified_market` on both sides). So once the file exists,
`resolve_dataset(role="holdout")` returns `eligible_market=True` **without verifying the file's
contents at all**: a truncated or substituted file would be read as the holdout and nothing would
object.

Pinning the digest is therefore not a formality that gates the run; it is the check that currently
does not happen. It is a contract-record change and needs approval.

## Access disclosure

To characterise the staged file I opened it with Polars and read **metadata** — row count (2,309),
asset count (6), timestamp range (2025-07-01 -> 2026-10-25) and the column list. No return statistic,
Sharpe, or performance figure was computed. It has also been hashed. No further read of this file
will be made before the qualification step.

## Commands (human; sealing and pinning are contract/data changes)

```bash
cd /home/quant/dev/quant-lab

# 1. seal the file at the ownership the other holdouts use
sudo install -o root -g trader -m 0640 \
     data/holdout_staging/indices_1d.parquet data/holdout/indices_1d.parquet
sha256sum data/holdout/indices_1d.parquet    # must print 0e886ca8...821f

# 2. add the pin to contracts/datasets/INDICES_1D_V1.json (contract change — needs approval):
#      "holdout_path": "data/holdout/indices_1d.parquet",
#      "holdout_sha256": "0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f"

# 3. grant the agent read on this one file
sudo setfacl -m u:quant:x data/holdout
sudo setfacl -m u:quant:r data/holdout/indices_1d.parquet

# 4. re-baseline the protected manifest, since the contract record changed
uv run warden generate-manifest --human-approve-manifest --json
uv run warden check-worktree
```

Do **not** use `scripts/lock_holdout_permissions.sh` here: it chowns and chmods every file under the
target recursively, mutating the four already-sealed datasets.

## Verification after provisioning

```bash
uv run python -c "
import sys; sys.path.insert(0,'src')
from pathlib import Path
from warden.provenance import resolve_dataset
from warden.contracts import load_contract
c = load_contract(Path('contracts/indices_1d_v1.yaml'))
r = resolve_dataset(c, Path('.'), role='holdout')
print(r.path, r.eligible_market, r.sha256[:12])"
```

With the pin present expect `data/holdout/indices_1d.parquet True 0e886ca8d948`. Without it the same
call also returns `True`, which is the hole described above.

## What this unlocks, and what it does not

Unlocks Step 4 for `RUN-HYP112-002`, `RUN-HYP113-002`, `RUN-HYP114-002`. It does not fix the audit:
those runs remain 5/6 on check 3, and `qualify_candidate` admits them only through the
human-authorized high-Sharpe `REVISE` bypass (`net_sharpe >= 1.0`, `stress_2x_cost_sharpe >= 0.50` —
met at 1.22-1.25 and 1.22-1.25).

All three share the same 16-month window, so the first read spends it.
