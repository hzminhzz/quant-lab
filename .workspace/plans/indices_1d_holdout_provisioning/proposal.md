# Provision the INDICES_1D_V1 holdout so Step 4 can run

Status: **proposal, not applied.** Nothing under `contracts/` or `data/holdout/` was changed.

## State

| | |
|---|---|
| Contract declares | `data/holdout/indices_1d.parquet` |
| Sealed directory | file absent |
| Staged copy | `data/holdout_staging/indices_1d.parquet`, 2,309 rows, 6 assets, 2025-07-01 -> 2026-10-25 |
| Contract window | 2025-07-01 -> 2026-10-26 (contained in the staged range) |
| Dataset pin | `contracts/datasets/INDICES_1D_V1.json` has `holdout_sha256: null` |
| Staged file sha256 | `0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f` |

`qualify_candidate` calls `resolve_dataset(contract, role="holdout")` before reading anything, and that
requires a pinned digest, so qualification fails on the pin even if the file were present. Both halves
must be fixed: seal the file *and* pin its digest.

Only the digest was computed here — no returns were examined, and no statistic was taken from the file.

## Commands (human; the seal and the pin are contract changes)

```bash
cd /home/quant/dev/quant-lab

# 1. seal the file at the ownership the other holdouts use
sudo install -o root -g trader -m 0640 \
     data/holdout_staging/indices_1d.parquet data/holdout/indices_1d.parquet
sha256sum data/holdout/indices_1d.parquet    # must print 0e886ca8...821f

# 2. pin the digest in the dataset record (contract change — needs approval)
#    set "holdout_sha256": "0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f"

# 3. grant the agent read on this one file
sudo setfacl -m u:quant:x data/holdout
sudo setfacl -m u:quant:r data/holdout/indices_1d.parquet

# 4. re-baseline the protected manifest, since the contract changed
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

Expect `data/holdout/indices_1d.parquet True 0e886ca8d948`. Only then can
`warden qualify --run-id RUN-HYP112-002 --unseal-holdout --human-confirm-unseal YES_UNSEAL_HOLDOUT`
be attempted, and it reads the lineage once.

## What this unlocks, and what it does not

Unlocks Step 4 for `RUN-HYP112-002`, `RUN-HYP113-002`, `RUN-HYP114-002` — three lineages, each
readable once. It does not fix the audit: those runs remain 5/6 on check 3, and the
`qualify_candidate` gate admits them only through the human-authorized high-Sharpe `REVISE` bypass
(`net_sharpe >= 1.0` and `stress_2x_cost_sharpe >= 0.50`, both met at 1.22-1.25 and 1.22-1.25).

All three share the same 16-month window, so the first read spends it; the second and third would be
draws from an already-observed window.
