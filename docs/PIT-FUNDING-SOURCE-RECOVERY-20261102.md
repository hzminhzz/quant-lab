# PIT Top-100 Binance funding source recovery — 2026-11-02

## Research disposition

**Historical rate provenance repaired; execution economics remain unqualified.**

This follow-up is stacked on [quant-lab PR #41](https://github.com/hzminhzz/quant-lab/pull/41).
PR #41 subsequently recorded one native Warden PIT Stage-1 **PASS** for
`HYP-ARXIV-XS-LOWVOL-PIT100-20261102` (mean IC `0.05742364`,
HAC t `11.5513`; `ATT-480df2ef5d8788f2-001`). Its score is
**not an independent temporal holdout** and does not qualify Stage 3.
This funding recovery did **not** rescreen that signal or alter the
original Stage-1 selection receipts.

The earlier **unsigned dataset proposal** had `2,187,277` hourly
price rows from 364 historically selected Binance USD-M symbols, but
`314,653` rows (14.39%) lacked a prior observed funding rate in
the 16-hour as-of lookback. This has been traced to a **missing local
source acquisition** problem rather than evidence of 314,653
zero-funding hours.

## 1. Root cause and Binance Vision ZIP acquisition

The precise missingness was **77 symbols with no locally matched
funding rate at all**, not 314,653 sporadic time gaps. The other 287
symbols already had complete local as-of observations.

Source: [Binance Public Data](https://github.com/binance/binance-public-data)
and official public monthly archives at
`https://data.binance.vision/data/futures/um/monthly/fundingRate/`.

`scripts/recover_pit_funding_archives.py` selected **432**
historical missing asset-months and **158** preceding-month warmup
archives. It made exactly **590** bounded official monthly requests
and checked each published `.zip.CHECKSUM` against the downloaded
ZIP's SHA-256, then parsed its observed event timestamps, rates and
`funding_interval_hours` values. It never requested an archive on or
after the protected 2025-07-01 boundary.

| Source result | Count |
|---|---:|
| Monthly funding ZIPs with matching Binance SHA-256 checksums | **588** |
| Official monthly ZIPs not published (404) | **2** |
| Original 77-symbol group events recovered in ZIPs | **68,242** |
| PIT rows recovered by nonfuture 16h as-of lookup | **313,908** |
| PIT rows still missing after ZIP recovery | **745** |

Unpublished monthly files: `BNXUSDT-fundingRate-2022-12.zip`
and `BNXUSDT-fundingRate-2023-01.zip`.
The remaining **745** missing PIT rows belong exclusively to
`BNXUSDT` between 2023-01-01 00:00 UTC and
2023-02-01 00:00 UTC.

Immutable receipts:

- Monthly archive manifest:
  `docs/evidence/arxiv_stage1_20261102/pit_funding_archive_recovery_20261102.json`
  SHA256 `64656affc7e0abb4808ffdc55c2ab905f3359c101b6001c4cb16211afc1bfee5`.
- Derived recovery-only event Parquet SHA256
  `1e80a6017f26e4190b18a0fff592856eb403668c73195a4d30752b694ac7079e`.

The archive receipt contains all 590 URL/status pairs, individual
SHA256 values and official checksum matches. Raw ZIPs are in the
isolated worktree's gitignored
`data/exploratory/pit_funding_vision_recovery_20261102/`.

## 2. Combined event ledger and versioned PIT overlay V2

`scripts/build_pit_funding_overlay.py` verified all original
**5,874 funding source Parquets** against the frozen upstream PIT
provenance receipt SHA256
`93d4af8a31fe2d4009fb59b2f7df29cae687fe20ad8002be1e41beac198b5326`,
combined those actual settlement events with the newly recovered
monthly archive events, and built a separate **758,641-event**
funding ledger. One canonical funding event has a known rate but
**unknown interval**; the interval remains NULL and no eight-hour
assumption was inserted.

The companion V2 PIT overlay preserves all **2,187,277 price rows
without changes** and adds source-tagged historical funding:
`CANONICAL_LOCAL`, `BINANCE_VISION_SHA_VERIFIED`, or null.
A `funding_rate` on this hourly panel means the **last observed
funding rate at or before the bar timestamp** with a 16-hour age
limit. It must **not** be charged on every bar as a real funding fee.
Actual settlement cashflows belong to the event ledger.

V2 receipt:
`docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v2_20261102.json`
SHA256 `c558de1ae21ae333f0e43d9163b9f8ebcf1ef8e76394a9437cec2a7807bca7e3`.

- V2 panel SHA256:
  `aa5f0757b64b03bf4ac250d0ed0849333b920bfca0bf276c84baec01b1ebaf8a`
- V2 event ledger SHA256:
  `c75fbef639d648e6dff23a943d446a220415151985c565b9e63333834b7ffb33`
- Observed-rate as-of coverage: **99.965939%**.
- No source parquet, old V1 panel, contract, manifest, Warden
  evaluator, registry, selection history or protected holdout changed.

## 3. Recovering the last 745 via official Binance REST

The historical Binance USD-M API:
[GET /fapi/v1/fundingRate](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)
exposes **93** observed `BNXUSDT` January 2023 events, even though
the corresponding two monthly ZIPs were not published. The fixed
query spans 2023-01-01 through 2023-01-31 inclusive with limit=1000,
and the raw HTTP response is pinned as separate provenance by
`scripts/recover_pit_bnx_rest_gap.py`.

The REST records include exact funding timestamps and rates.
They **do not** include `funding_interval_hours`, so all 93 retain
null interval metadata. Do not infer 8h merely from the observed
spacing. V2 remains sealed; the REST supplement builds a new V3
overlay and a new versioned settlement ledger.

V3 receipt:
`docs/evidence/arxiv_stage1_20261102/pit_funding_overlay_v3_20261102.json`
SHA256 `c04c3f431b1c0c6ea5b65592857ec799a6cf08fcd1885807aa96de05c5a46640`.

| Outcome | Observed source result |
|---|---:|
| Additional REST events | **93** |
| Additional previous-unknown PIT hourly observations | **745** |
| Remaining hourly rate gaps under 16h causal as-of lookup | **0** |
| Final rate availability | **2,187,277 / 2,187,277 = 100%** |
| Combined observed settlement-event records | **758,734** |

- V3 price+funding overlay SHA256:
  `187102bb1fc172351467a07b5cdac275be32f3f464af62b0a4023574a002abe8`
- V3 event ledger SHA256:
  `6ac0630855a88983cc9576cea0b7f094b50799fc8b0cea6ad0d92312e0e661aa`
- Original panel bytes and OHLCV rows remained unchanged.
- Missing settlement-interval metadata: at least **94**
  actual events (93 from the official REST supplement, plus one
  canonical historical rate event). They are not fabricated.

**IMPORTANT LIMITATION:** 100% *as-of past funding rate availability*
is not identical to complete knowledge of every contemporaneous
funding settlement payment. Settlement intervals changed among
1h/2h/4h/8h historical schedules. A timestamp near an hourly open
may have a funding-versus-fill ordering ambiguity (PR #44).
Actual venue tradeability, delisting exits and order books remain
untested. This funding recovery does not imply Stage 3 PnL.

## 4. Reproducibility

All scripts refuse preexisting evidence outputs and validate source
hashes before deriving data. Use an isolated research worktree with the
exact upstream PIT panel (original SHA256
`c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a`):

```bash
uv run --frozen python -m scripts.recover_pit_funding_archives \
  --panel-path /path/to/pinned/crypto_perps_pit100_1h_v1.parquet
uv run --frozen python -m scripts.build_pit_funding_overlay \
  --panel-path /path/to/pinned/crypto_perps_pit100_1h_v1.parquet
uv run --frozen python -m scripts.recover_pit_bnx_rest_gap
```

Three provenance layers are separately SHA-pinned. Reproducing
with changed Binance source bytes must be disclosed rather than
silently assuming equivalence.

## 5. Exact next scientific gate

**Engineering source recovery: COMPLETE for historical as-of rate
availability.**

**PIT Stage 1: previously PASSED**, recorded under PR #41 with
a separate approved V1 contract. The funding repair does not change
its frozen factor or recorded outcome.

**PIT Stage 3: NOT EXECUTED / NOT QUALIFIED.** Before any full
Warden economic trial, a separately approved, versioned event-aware
contract/evaluator must apply actual per-asset settlement events,
explicit unknown interval provenance, and next-open executable
position exits for symbols moving out of the monthly PIT universe.
The frozen Warden evaluator's **8h-only funding schedule** cannot
correctly consume this event ledger as is. Retain the prior selection
history and avoid a second Stage-1 run of the same low-vol lineage.

No protected holdout read, exchange order, or capital authorization
was made.
