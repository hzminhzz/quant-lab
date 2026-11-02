# PIT monthly-universe execution source audit — 2026-11-02

**Disposition: all 30 previously unpriced monthly transition exits have
independently SHA-verified next-hour Binance open observations in the
historical price archive. This is price-source evidence only, NOT
broker fills, completed Stage 3, or scientific qualification.**

The current Warden source is `CRYPTO_PERPS_PIT100_1H_V1`
(SHA256 `c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a`).
The frozen low-vol factor passed native Stage 1 on this PIT panel
(mean IC `0.05742364`, HAC t `11.5513`,
`ATT-480df2ef5d8788f2-001`) but has no full Stage 3.
This audit does not change any registered signal or contract.

## 1. Sparse PIT price transition defect

Reusing the existing causal audit function
`scripts/audit_lowvol_execution_integrity.py` on the frozen **PIT
low-vol original four-hour signal** recorded:

| Classification | Count | Total gross exposure weight |
|---|---:|---:|
| Current selected-universe open absent while holding | 30 | 1.50 |
| Next selected-universe open absent while holding | 30 | 1.50 |
| Last validation hour with no legal next open | 20 | 1.00 |

The 60 monthly gaps represent **30 matched asset/roster changes**:
30 held positions are present at the last hour of the previous
election month, and the same positions remain held at the next
month's 00:00 UTC when the asset is no longer in the Top-100
selection. The 20 terminal positions at 2025-06-30 23:00 UTC need
an explicit *pre-cutoff* liquidation policy; no protected
2025-07-01 prices were read.

The sparse PIT panel includes **359 pairs of consecutive observed
asset rows separated by more than one hour**, the largest by
**15,337 hours**. Warden's existing `execution_returns` uses
`open.shift(-1).over("asset")`, which **does not verify one-hour
timestamp continuity**. Therefore calling native Stage 3 directly
on the sparse PIT panel would evaluate some long-gap "next
observed" prices as though they were next-hour market returns.
This is a *source/execution data-contract defect*, not a reason
to alter the registered Stage-1 predictive outcome.

## 2. Historical raw price bridge check

`scripts/audit_pit_monthly_exit_prices.py` reconciles those 30
matched gaps against the original Binance USD-M 1h
`ml4t-data` source. Each required monthly next-hour price file
is separately required to appear in the frozen 8,795-file PIT
membership-source SHA inventory, and every accessed file's SHA256
is checked against its original immutable receipt.

| Outcome | Observed |
|---|---:|
| Matched missing-price monthly exits | 30 |
| SHA-verified exact following UTC-hour raw price | **30 / 30** |
| Market source files missing | 0 |
| Invalid/zero next-hour price sources | 0 |
| 20 terminal positions before validation cutoff | Still require cutoff-flat policy |
| Actual limit/market-order fills observed | **0** |

The audit obtains historical **source candles** for those 30
required exits, not an execution guarantee. Market spread, impact,
queue, delist, and path-specific funding events still matter.
A price can be observable without an assured fill at the modeled
open; fees and latency stress are still mandatory.

Full immutable asset/time/position/source SHA receipts:
`docs/evidence/arxiv_stage1_20261102/pit_monthly_exit_source_audit_20261102.json`
SHA256
`28bc41728a746c2571386baa2ce7da4cdd2272a28ae680900998456060e96aa8`.

## 3. Next implementation

For a faithful research-only economic replay, preserve frozen
Stage-1 target weights and apply Warden's one-bar delay exactly
once. Explicitly overlay **exact observed next-hour source opens**
for held positions that leave the selection; never use
`shift(-1)` across a 15,337-hour gap. Flatten before the
2025-07-01 validation cutoff, charge all closing costs and use
the new full 758,734-event Binance funding ledger for actual
event timestamps, preserving funding/open-fill ordering bounds.

**Do not** substitute the old fixed 8h funding mask for the
event ledger or claim native Stage 3 under an unapproved event
execution contract. The original Warden evaluator and manifest
are unchanged. All screening/selection debt is retained, and
there is no production/holdout authorization.

Reproduce only in an isolated worktree with the approved PIT
V1 data available locally (original SHA pinned):

```bash
uv run --frozen python -m scripts.audit_lowvol_execution_integrity \
  --spec experiments/active/arxiv_crypto_lowvol_pit100_20261102.yaml \
  --output .workspace/stage1_optimization/pit_executable_gap_audit_20261102.json
uv run --frozen python -m scripts.audit_pit_monthly_exit_prices \
  --panel data/exploratory/crypto_perps_pit100_1h_v1.parquet \
  --gap-report .workspace/stage1_optimization/pit_executable_gap_audit_20261102.json
```

No protected prices, held-out labels, new Stage-1 screen, Stage-3
trial, source mutations, or broker orders occurred.
