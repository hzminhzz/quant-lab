# Binance hourly data recovery and 72-hour order-flow research — 2026-11-02

**Status:** DATA_REPAIRED_FOR_RESEARCH; CAUSAL_PREFLIGHT_COMPLETE;
ATS and NTR both **provisional Stage-1 REJECT**. No qualified strategy.
No native `warden screen` was called on the unsigned 72h contract.

This is a continuation of [PR #40](https://github.com/hzminhzz/quant-lab/pull/40)
and its prior checkpoint `docs/RECOVERY-EXECUTION-20261102.md`. Do not
reinterpret this as a replacement for the immutable original hypotheses.

## 1. Diagnosis: what was wrong

Two original experimental modules exist in the **dirty source checkout**, not
in the isolated PR #40 branch; they were inspected **read-only**:

| Original hypothesis / module | Actual defect | Recovery |
|---|---|---|
| `HYP-2026-050`, `cs_stealth_trade_size_1h.py` | If real Binance `quote_volume` or trade count absent, fabricated `volume * close` and count=1, rather than genuine mean trade size; also pre-shifted features before Warden's execution lag | Actual `quote_volume / trade_count` aggregated over complete 168h and 720h; no fallback or pre-lag |
| `HYP-2026-054`, `cs_taker_flow_imbalance_1h.py` | If taker-buy volume absent, fabricated buy/sell pressure from candle direction times total volume; pre-shifted before Warden's lag | Actual observed `taker_buy_quote_volume` and `quote_volume`, windows 24/72/168h, no fallback or pre-lag |
| Both original hypotheses | Frozen contract `CRYPTO_PERPS_TOP100_1H_V1` specifies a **4-hour label**, but each original thesis specifies a **72-hour** forecast/holding period | Separate **proposed** `CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1`, with 72h horizon and embargo, never edit the original frozen contract |

The local derived Binance panel is not newly backfilled synthetic history:
the added four trade fields were all joined exactly from existing Binance
USDT-M observed 1h Parquet partitions. Price/funding values and row keys
matched the pinned original panel. New features must use a completed 1h
bar; in a future sanctioned Warden run, the evaluator supplies the
next-bar execution delay.

### Physical dataset

- Input original panel SHA256:
  `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`
- Output `data/exploratory/crypto_perps_top100_1h_orderflow_v1.parquet`
  SHA256: `3045cb84e059810beec5a0f8afc2945cb7d3aee1425297a93d175126c428eafa`
- 2,174,396 rows, 100 assets, from 2,987 hash-chained hourly
  partitions (combined raw chain
  `39994173e004c6200b10a9e281097038812c5dd30c47aae91e632f6995819a5f`).
- Strict source window: 2023-01-01 to **2025-06-30 23:00 UTC**.
- One complete 100-asset no-trade hour at 2024-10-28 20:00 UTC is
  marked `flow_valid=False`, never filled with synthetic trade volume.
  Independent official Binance futures archive CSVs for BTCUSDT,
  ETHUSDT and SOLUSDT on `2024-10-28` all report volume **0** for
  the 20:00 UTC hour. The other 97 archives were not independently
  fetched; local canonical raw values were checked.
- Strict validation rejects source-null trade fields, duplicate
  timestamps, unequal original OHLCV, mislabeled `flow_valid` values,
  non-contiguous rolling windows and impossible taker fractions.
  A legitimate historical zero-trade bar contributes **zero trades**
  to later aggregated sums but cannot itself generate a tradable signal.

Source and output are local, digest-pinned, and **write-once**. Neither
the original Binance lake nor Warden's original frozen panel was edited.

## 2. Registered-thesis descendants and exact preflight

Frozen, human-unapproved research **proposals**, not Warden registry rows:

- `docs/contract-proposals/HYP-ORDERFLOW-ATS-H72-20261102.yaml`,
  explicitly descended from original `HYP-2026-050`.
- `docs/contract-proposals/HYP-ORDERFLOW-NTR-H72-20261102.yaml`,
  explicitly descended from original `HYP-2026-054`.
- Proposed unsigned `docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.yaml`,
  with its source manifest JSON alongside.
- Warden-compatible strategy adapters and future experiment specs
  exist under `strategies/candidates/recovery/` and
  `docs/contract-proposals/*_experiment.yaml`. Deliberately **not**
  placed in `experiments/active/` while the contract is unsigned, so
  automated research loops cannot accidentally ingest them. They
  **cannot** be run against
  the original four-bar Warden contract.

The reproducible preview:

```bash
uv run --frozen python -m scripts.screen_binance_orderflow_provisional \
  --output .workspace/campaigns/binance_orderflow/provisional_stage1_20261102.json
uv run --frozen python -m scripts.verify_binance_orderflow_wiring --factor all
```

Inputs were frozen **before evaluation**. Both factors use Ridge(alpha=10),
fold-local StandardScaler, 4 sequential purged training/test folds,
72h label and 72h embargo. Exact entry and exit at t+1h and t+73h
**open**, respectively; no bar-forward-filling, missing execution hour,
or label after frozen `2025-07-01T00:00:00Z`. Train labels mature
before their test fold; the first test begins 2024-07-01. Only
**nonoverlapping UTC 72h anchors** contribute to validation IC,
min 20 non-BTC available assets (actual median **99**), and real
flow-valid signals. The Warden native
`evaluate_stage1_cross_sectional_ml` computes IC/HAC gate thresholds.
There is **no separate cross-sectional PnL/Stage3 result**.

| Metric | HYP-ORDERFLOW-ATS-H72 | HYP-ORDERFLOW-NTR-H72 |
|---|---:|---:|
| Valid independent 72h IC anchors | 121 | 121 |
| Rejected/no-label anchors | 1 | 1 |
| Mean 72h Spearman IC | **-0.00204189** | **+0.00216803** |
| Warden HAC t-statistic | **-0.167605** | **+0.182768** |
| Warden gate function (IC >= .015 and HAC t >= 2) | **FAIL** | **FAIL** |
| Native persisted `warden screen` | **NOT RUN** | **NOT RUN** |
| Stage3 trial | **NOT RUN** | **NOT RUN** |

**Immutable selection evidence**
`.workspace/campaigns/binance_orderflow/provisional_stage1_20261102.json`
SHA256:
`6552be460a31fab8a6140742b8c03a53990d5e1b3737efe81949ccf6304b1842`.
This preflight and its sister study count as selection attempts for any
future statistical interpretation. They must not be silently erased
because `warden` did not write them into a separately governed registry.
The 149-hypothesis / 75-trial original research family's prior
multiplicity likewise remains relevant.

### Warden-ready compatibility (not an approved trial)

`scripts/verify_binance_orderflow_wiring.py` loaded the identical real
dataset through both future Warden strategy modules and reproduced the
same 121 IC values and Warden gate results. Position matrices:
**21,888 timestamps x 100 symbols**. Target weights remain 5 longs
+ 5 shorts, 10% absolute per position, **gross <= 100%**. These are
*desired weights*, NOT filled account positions. Funding, liquidity,
exposure constraints, actual execution prices and native venue economics
were **not** simulated; data has a known no-trade hour which requires
an execution-aware no-fill policy before economic qualification.

## 3. Controls / unfinished original blockers

- The original source checkout's untracked versions of
  `cs_stealth_trade_size_1h.py` and `cs_taker_flow_imbalance_1h.py`
  were **preserved**, not silently overwritten. The research lineage
  explicitly replaces their fabricated data assumptions with
  `strategies/candidates/recovery/binance_{ats,ntr}_h72.py` adapters.
- The new contract is not in protected `contracts/` or in the
  signed `.warden_manifest.sha256`. The human principal must
  separately authorize and sign a new Warden contract for any *native*
  screen. The user authorized data repair/research continuation,
  **not** impersonating a human contract signer.
- Native Stage 3 is **not justified** because both previously frozen
  72h preflights fail Stage 1 by a large margin. Re-running identical
  failed thesis variants under a signed contract should be motivated
  by governance parity, not search for a pass.
- Original `HYP-2026-035` (Kyle's Lambda) and `HYP-2026-062`
  (volume-bucket VPIN) still require honest trade-sequence history
  for their fixed 2023-2025 validation window. The existing local
  BTC futures tick archive begins 2026; hourly aggregate taker flow
  does **not** faithfully reconstruct a trade-by-trade VPIN or
  estimated tick impact. Official Binance USD-M aggTrades archives
  for 2024-07-01 were verified available by HTTP 200 for BTC, ETH,
  SOL, with compressed daily sizes about 11.2MB/10.0MB/6.8MB
  respectively, but a whole-universe multi-year download
  is a **separate, potentially substantial storage/data acquisition**
  project. VPS primary disk had 71GB available at inspection; no
  bulk historical trades were downloaded.
- The Top-100 universe's historical point-in-time membership has not
  been separately attested; thus even a hypothetical signal gate pass
  would not yet imply genuine historical tradability or survivor-free
  trading results.
- Original FX quote-side 115..119 remain blocked; their separately
  frozen Dukascopy NEXT_BAR analogs 120..124 all had negative Stage3
  disposition. Avoid rescreening a duplicate alias.

## 4. Engineering verification and next action

- **45 focused tests passed** (new factor and native Warden prefix
  checks, original recovery, 15m CME admission).
- Ruff on all new files **passed**.
- SHA-pinned source persisted as expected, no protected holdouts
  accessed. Original source registry unchanged. No Stage3, no
  trading orders, no production deployment.
- The priority is **different source-complete hypotheses**, not
  revising either ATS/NTR variant to force a Stage1 pass. For the
  faithful historical VPIN/Lambda claims, decide whether trade-level
  acquisition is economically worth the data burden before starting.
