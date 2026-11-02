# Quant-lab arXiv low-vol next-step recovery — November 2, 2026

## Scope and disposition

The approved work investigated the 20 missing-price asset-hours, fixed terminal-close *diagnostic accounting* without touching Warden, reconstructed the historical Top-100 Binance USD-M universe using only past observations, and audited actual funding event cadence against the original frozen Warden crypto funding schedule.

**Outcome: original Warden Stage 1 PASS remains a recorded CONDITIONAL signal in a hindsight-selected dataset. It is not a PIT-confirmed PASS or economically/scientifically qualified strategy.** No new Warden selection attempts, Stage 3 trials, protected holdout reads, or live orders were made in this continuation.

The original Warden rules, original `CRYPTO_PERPS_TOP100_1H_V1` contract and its SHA256 `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`, authoritative registry, and original Stage 1 returns/attempt records are unchanged.

## 1. Twenty missing-price events resolved as an accounting boundary

`scripts/audit_lowvol_execution_integrity.py` examined the exact existing 24h low-vol Warden strategy positions, applied the **one-hour execution delay**, and compared them against observed aligned opens at every validation hour.

| Category | Count |
|---|---:|
| Interior missing next-hour opens in an active position | **0** |
| Missing current-hour opens in an active position | **0** |
| Nonconsecutive global hourly timestamps | **0** |
| Invalid non-positive observed opens | **0** |
| **Positions active at 2025-06-30 23:00 UTC without a legal next hour** | **20** |
| Total absolute position weight at last hour | **1.0** |

All 20 events were terminal exposure at the last allowed exploratory open; calling them interior data gaps was incorrect. **We did not read Open[2025-07-01 00:00 UTC] or any protected bar to close them.**

`scripts/evaluate_lowvol_terminal_close.py` implements an **accounting-only** predetermined cut-off flat decision at `val_end - execution_delay - 1`, executed at the last observed open. Full turnover/fee is charged at liquidation, and the preceding return vector is numerically unchanged to 1e-14 (floating-point reduction tolerance). It is *not* an exploitable trading signal and does not change the original frozen strategy or Stage 1 result.

| Cost case | Original net Sharpe | Terminal-flat diagnostic net Sharpe | Ending gross position |
|---|---:|---:|---:|
| 1x taker + slippage | 0.9104 | **0.9083** | 0.0 |
| 2x | 0.1528 | **0.1487** | 0.0 |
| 3x | −0.6003 | **−0.6064** | 0.0 |
| +1 bar delay, 1x cost | 0.7635 | **0.7614** | 0.0 |

**Conclusion:** no unresolved interior price gaps in the original 100-coin panel, but the terminal position needed explicit liquidation-fee accounting. The correction does not rescue the strategy.

## 2. Future-dependent universe confirmed

`scripts/salvage_crypto_top100.py` selected coins by **full-year 2024 quote volume** and required OHLCV/funding partitions through **2026**. This depends on information unavailable to a historical trader in 2023.

The replacement membership audit, `scripts/audit_pit_top100_universe.py`, reconstructs monthly rankings at the first UTC hour of each month using the **preceding 30 calendar days** of Binance archive quote volume, at least 21 days of source bars, and minimum $25,000 in average hourly quote volume. It uses price-source partitions from 2022-12 through 2025-06 only.

| Measure | Verified count |
|---|---:|
| Original historical Binance USD-M 1h Parquet shards SHA-verified | **8,795** |
| Monthly as-of Top-100 elections | **30** |
| Distinct coins ever elected across train + validation | **364** |
| Elected symbols never included in the hindsight Top-100 | **264** |
| Average overlap between as-of election and fixed Top-100 | **54.1 / 100** |
| Min / max monthly overlap | **33 / 74** |

The complete **write-once, source-file SHA256** membership receipt is stored at `docs/evidence/arxiv_stage1_20261102/pit_universe_membership_20261102.json.gz` (SHA256 `7672cb880aaef849df12312192b93942bd4c653476915a66baa3f3e4205f75d2`), including 30 monthly rank tables and all 8,795 shard digests.

### Separately versioned PIT data proposal

`scripts/build_pit_top100_proposal.py` materialized `data/exploratory/crypto_perps_pit100_1h_v1.parquet` **only on the isolated VPS worktree**:

- **2,187,277** strictly OHLC-consistent hourly rows across 364 historical symbols.
- **2,999** observed price shards used, each individually checked against the write-once source archive.
- **5,874** funding source shards, each hashed in the derived source-provenance receipt.
- **One** as-of-selected symbol/month was missing its current-month price shard; it was not backfilled from the future.
- Data SHA256: `c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a`.
- Derived source receipt: `docs/evidence/arxiv_stage1_20261102/pit_source_provenance.json.gz`, SHA256 `93d4af8a31fe2d4009fb59b2f7df29cae687fe20ad8002be1e41beac198b5326`.

Proposed (unapproved) contract and independent dataset manifest:
`docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml` and `docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.dataset.json`.

**Governance:** Neither was installed in protected `contracts/` or signed into Warden's manifest. The existing user approval authorized this **recovery/proposal**; it does not constitute a human-held signing key or permission to fake protected evaluator attestations. A fresh PIT Warden Stage 1 result has **NOT** been run or claimed. The original strong IC **may not generalize** to 364 historically selected coins.

The source archive still may omit symbols delisted prior to its acquisition. This PIT reconstruction corrects the identifiable full-2024/future-2026 selection leak, but does **not independently prove delisting-complete coverage**.

### Training-only low-volatility preflight on the as-of panel

A single fixed-sign training-only check was run via `scripts/pit_lowvol_training_preflight.py`: it loaded only observations before the frozen `val_start = 2024-07-01 UTC`, computed the same inverse-24h-volatility factor and executable four-hour open-to-open label, and split historical training into two equal contiguous halves. **No validation window predictions, Warden screening, or protected-period prices were used**.

| Training half | Eligible historical hours | Mean rank IC | HAC t |
|---|---:|---:|---:|
| Jan–Sep 2023 (first half) | 6,540 | **0.053062** | **10.981** |
| Oct 2023–June 2024 (second half) | 6,559 | **0.053192** | **11.086** |

The corrected PIT data contains **1,312,786 rows and 249 distinct assets** in the training-only interval. The factor's sign and research definitions were **not changed** after this result. These strong in-sample statistics justify a governed PIT Stage 1 confirmation when the new contract is approved, **not** a Stage 1 PASS or execution PnL claim. Training receipt preserved at `docs/evidence/arxiv_stage1_20261102/pit_lowvol_train_preflight_20261102.json` (SHA256 `c4a984035c12b2153a64f16becefb01e404df7d2eccb64bb5663616643d91cfe`), with its original ignored copy in the isolated worktree.

## 3. Funding cadence and availability are material blockers

`scripts/audit_pit_funding_risks.py` validated the new PIT panel without substituting missing funding with a fictional zero:

| Measure | Observed |
|---|---:|
| Missing funding data across PIT rows | **314,653 / 2,187,277 = 14.39%** |
| Missing funding at potential 8-hour UTC settlement rows | **39,333** |
| Raw funding events at 4-hour intervals (selected historical source) | **312,496** |
| Raw funding events at 8-hour intervals | **369,540** |
| Raw events at 1-/2-hour intervals | **7,115 / 1,247** |

The frozen `_funding_settlement_mask` in Warden assumes **8-hour UTC settlement** on all hourly crypto assets. That assumption does not match the event frequencies in the observed Binance archive. Its original historical financing PnL therefore has a material basis limitation.

### Event-based *diagnostic* replay of the old fixed Top-100

`scripts/audit_lowvol_event_funding.py` used 1,200 archived funding shards covering the original 100 assets, 2024-07 through 2025-06. It aligned **115,959** observed funding events by asset and actual UTC hour to the unchanged low-vol position vectors, after terminal-flat accounting. No protected prices or economic performance outside the frozen validation period were accessed.

| Net Sharpe diagnostic | Warden fixed 8h settlement | Observed funding events only | Additional adverse missing-event stress |
|---|---:|---:|---:|
| 1x execution cost | 0.9083 | **0.7852** | **0.4288** |
| 2x execution cost | 0.1487 | **0.0256** | **−0.3299** |
| +1-bar execution delay | 0.7614 | **0.6413** | **0.2829** |

The **adverse stress** adds 1bp of negative funding per unit of absolute position exposure at each 4-hour UTC mark without a sourced event. It is a hypothetical conservative assumption, *not* an assertion that those particular payments occurred. Observed-only source replay likewise cannot treat unavailable events as proven zeros.

**Conclusion:** This strategy does **not** clear economic Stage 3 thresholds and weakens further under more faithful funding cadence. More parameter tuning on the same 2024-2025 validation period is not justified.

### Immutable diagnostic evidence

Each source report was written once inside the isolated worktree, then preserved in versioned `docs/evidence/arxiv_stage1_20261102/`:

| Report | SHA256 |
|---|---|
| `execution_gap_audit_20261102.json` | `7671bc237fa57da1721bd91893e80fd7b83eaafdca95ff5b6ec448d6bb8c61b5` |
| `terminal_flat_20261102.json` | `f61049062e3738235e5f00db324bc71dda13b670ec4cb53f1c10f8bbc71de0d3` |
| `event_funding_stress_20261102.json` | `19cb1cbc97ebfee30dc5c78a052fd517189bf65edf4b41199a5af3eacdd7c4c1` |
| `pit_funding_risks_20261102.json` | `4e02114c56bed0aa32a9198d43ac08911fd6b2d1aedfd41229ca4260a3c9f9d1` |
| `pit_lowvol_train_preflight_20261102.json` | `c4a984035c12b2153a64f16becefb01e404df7d2eccb64bb5663616643d91cfe` |

## 4. Exact acceptance status and next legal gates

| Gate | State |
|---|---|
| Prior frozen-source Stage 1 predictive PASS | **Recorded**, but conditional on future-selected historical universe |
| Interior execution data gap diagnosis | **Verified: 0 interior events** |
| Terminal flat accounting | **Implemented and verified** without protected-period price |
| New as-of membership reconstruction | **Implemented** and archived with hash-pinned inputs |
| Versioned PIT panel | **Built**; contract is a **proposal**, not governed Warden evidence |
| Event-aware funding diagnostic | **Verified**, both 1x and 2x economic Sharpe below Stage 3 |
| PIT version contract acceptance + manifest | **Not approved/signed** |
| PIT low-vol training-only IC check | **Completed**, 0.0531/0.0532 across training halves; not Stage 1 |
| PIT Warden Stage 1 validation | **Not executed** |
| Venue-valid position/funding fills | **Not established** |
| Scientific Stage 3 / 6-check audit | **NOT qualified** |
| Protected holdout / live authorization | **Not touched / not authorized** |

### Next scientifically meaningful implementation

1. Review/sign the **separate** PIT contract and its data SHA through the human-governed approval path; retain inherited 149-hypothesis, 75-trial, 230-screen search debt **plus** the subsequent arXiv attempts.
2. First verify fully causal, prospective monthly-mapped data and funding availability on the **new** source. Run **one** preregistered Warden Stage 1 screen for the unchanged low-vol factor with the new PIT panel. Do not retune its original sign/features.
3. Replace the 8h-only funding economic model with a separately versioned, human-approved **event-aware** simulator/contract, with missing-funding fail-closed, known settlement events, latency ordering, and per-asset funding intervals; preserve the prior results unmodified.
4. Require net performance at 1x and 2x costs, plus DSR lineage control and delist/exit evidence, before another full Stage 3 spend. A PIT predictive PASS alone is not permission to trade.

No data or code was changed inside the authoritative source checkout, protected contracts, Warden core, sealed holdout, or live execution services.
