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

### Demonstrated, not inferred

A temporary contract with no `holdout_sha256` in its record was resolved against a file of arbitrary
bytes: `eligible_market=True`, `reason="verified market data"`. Replacing the file's contents with
different bytes and resolving again also returned `eligible_market=True`. So an unpinned holdout is
accepted, and a substituted one is accepted too.

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

---

# Executed: first holdout read (2026-10-28)

## Approving instruction, verbatim

> "I approve open holdout, since the sharpe are above 1, which is good"
> "Approve, let unseal holdout and test now"

The approval names no run ID. The agent selected `RUN-HYP112-003` on criteria stated in-session
before execution: highest DSR (0.976), lowest max drawdown (14.9%), risk-adjusted Sharpe within 0.023
of the best of the three, and 112/113/114 are the same long-only index bet at three leverages
(113 = S03, 114 = S03 × IDM 2.30). Separate lineages, so 113 and 114 remain readable.

## Pre-read state (recorded before the unseal)

| | |
|---|---|
| protected manifest `sha256` | `dd56056a96d66b18a9916297bca1c81143663da1f1923f4eda8efbf9d6ee4e03` |
| manifest approval | `2026-10-28T16:36:21+00:00`, `human-operator`, `dd56056a96d6` |
| sealed holdout `sha256` | `0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f` |
| exploratory `sha256` | `0e4677355587831849598a1217ab9987b991d9ed8ff0dc2f3369782fad36ba27` |
| git HEAD | `48097c0` |

## Result

```json
{"qual_id": "QUAL-RUN-HYP112-003", "run_id": "RUN-HYP112-003", "lineage_id": "HYP-2026-112",
 "holdout_net_sharpe": 0.7300862645723718, "holdout_gross_sharpe": 0.7313708452674778,
 "holdout_max_dd_pct": 10.86762756458317, "val_to_holdout_ratio": 0.5962866972770623,
 "promoted": false, "verdict": "REJECTED_ON_HOLDOUT", "data_provenance": "verified_market"}
```

Promotion needs `holdout_net_sharpe > 0.80` **and** `ratio >= 0.50`. The ratio clears (0.596); the
level does not (0.730 vs 0.80). Hypothesis `HYP-2026-112` is now `REJECTED_ON_HOLDOUT`.

## Reading

No collapse: 0.730 net against 1.224 in validation is 60% retention, and max drawdown improved from
14.9% to 10.9%. But the level is below the promotion bar, and the decay is the shape predicted for a
book with no signal — 1×/1.5×/2×/+1-bar were identical to three decimals in validation, so the
1.22–1.25 was index beta in a rising window, and the holdout delivered a lower beta Sharpe rather
than a signal that survived. `HYP-2026-113` and `_114` stay `ACTIVE`, but a second read buys the
same 16-month window again — one sample, not two.

---

# Second holdout read (2026-10-28)

## Approving instruction

> "usnseal holdout for other candidate"

The instruction names no run. The agent selected `RUN-HYP113-003` because 113 is the base
variable-risk book (S03, 60-bar vol) and 114 is literally 113 × IDM 2.30 (S04) — so 113 is the
informative read and 114 adds leverage, not information.

## Pre-read state

| | |
|---|---|
| sealed holdout `sha256` | `0e886ca8d9481f579b319595b6217cd5157b007d514d6dd583eee4df776f821f` — **unchanged since read 1** |
| protected manifest `sha256` | `dd56056a96d66b18a9916297bca1c81143663da1f1923f4eda8efbf9d6ee4e03` |
| git HEAD | `48097c0` |

## Result

```json
{"qual_id": "QUAL-RUN-HYP113-003", "run_id": "RUN-HYP113-003", "lineage_id": "HYP-2026-113",
 "holdout_net_sharpe": 0.5820782486538658, "holdout_gross_sharpe": 0.585,
 "holdout_max_dd_pct": 10.0, "val_to_holdout_ratio": 0.46830940684879796,
 "promoted": false, "verdict": "REJECTED_ON_HOLDOUT", "data_provenance": "verified_market"}
```

Fails both bars: level `< 0.80` and retention `< 0.50`. `HYP-2026-113` is now `REJECTED_ON_HOLDOUT`.

## Both reads together

| run | sizing | val netSR | holdout netSR | ratio | holdout DD | verdict |
|---|---|---|---|---|---|---|
| `RUN-HYP112-003` | S02, 1260-bar vol | 1.224 | **0.730** | 0.596 | 10.9% | REJECTED (level) |
| `RUN-HYP113-003` | S03, 60-bar vol | 1.243 | **0.582** | 0.468 | 10.0% | REJECTED (level + ratio) |

The ordering is by volatility-estimator responsiveness, not by signal: the long-run estimator
retained 60% of its validation Sharpe, the responsive one 47%. Both are long-only index beta — the
1×/1.5×/2×/+1-bar columns being identical to three decimals says there is no signal for friction to
bite — so what decayed is the beta premium in the new window, differently per sizing rule. Two reads
of one 16-month window, not two independent confirmations.

`HYP-2026-114` (S04 = S03 × IDM 2.30) remains `ACTIVE`. Its Sharpe is approximately leverage-invariant
against 113's (validation: 1.247 vs 1.243), so a third read is expected to reproduce ~0.58 and add no
information. Not read.

---

# Third holdout read (2026-10-28)

## Approving instruction

> "Yes" — in reply to "If you want it anyway, say so and it's one command."

## Pre-read state

| | |
|---|---|
| sealed holdout `sha256` | `0e886ca8…821f` — **unchanged since read 1** |
| protected manifest `sha256` | `dd56056a96d66b18a9916297bca1c81143663da1f1923f4eda8efbf9d6ee4e03` |
| git HEAD | `48097c0` |

## Result

```json
{"qual_id": "QUAL-RUN-HYP114-003", "run_id": "RUN-HYP114-003", "lineage_id": "HYP-2026-114",
 "holdout_net_sharpe": 0.5941844145412007, "holdout_max_dd_pct": 22.0,
 "val_to_holdout_ratio": 0.4764476172880728, "promoted": false,
 "verdict": "REJECTED_ON_HOLDOUT", "data_provenance": "verified_market"}
```

## All three reads

| run | sizing | val | holdout net | ratio | holdout DD | verdict |
|---|---|---|---|---|---|---|
| `RUN-HYP112-003` | S02, 1260-bar vol | 1.224 | 0.730 | 0.596 | 10.9% | REJECTED (level) |
| `RUN-HYP113-003` | S03, 60-bar vol | 1.243 | 0.582 | 0.468 | 10.0% | REJECTED (level + ratio) |
| `RUN-HYP114-003` | S04 = S03 × IDM 2.30 | 1.247 | 0.594 | 0.476 | **22.0%** | REJECTED (level + ratio) |

The third read returned what was predicted before it was spent: 113 and 114 are 0.012 apart on
Sharpe (0.582 vs 0.594), because IDM scales exposure without changing the ratio — but **drawdown
doubled, 10.0% to 22.0%**. That is the cleanest measurement of what the IDM lever adds: risk, not
return. It also means the read bought no information: it confirmed leverage-invariance on the same
16-month window for the third time.

All three lineages are now `REJECTED_ON_HOLDOUT`. Three reads, one window.
