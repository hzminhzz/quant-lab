# Contract proposal — broad crypto-perp 1h panel, 2021-01 → 2026-10

Status: **proposed, not registered.** `contracts/` is the principal's. Nothing here is signed, no
panel exists yet, no trial is spent by this document.

Prepared 2026-10-28 for HYP-2026-125 (weekly cross-sectional momentum), whose faithful 7-day
specification was rejected at Stage 1 on the narrow `CRYPTO_PERPS_TOP100_1H_V1` panel: mean IC
-0.0452, HAC t -1.01, net Sharpe +0.126 at 1x / -0.052 at 2x over 52 weekly rebalances.

## Why a new contract instead of another trial

Two defects in the current panel are structural, not statistical:

1. **Sample size.** 52 independent weekly bets. The skip-week variant reaches net Sharpe 0.962
   (2x: 0.843, DD 14.4%) but scores DSR 0.315 at the stored `crypto_perps:1h` family floor and
   0.832 even on a clean floor. No formation tweak fixes a 52-observation sample.
2. **Survivorship.** The 100-symbol panel is a current listing set projected backwards: every
   symbol runs to the panel end, none delist. Dead coins are absent, which flatters a long-short
   book whose short leg is losers.

The lake store `crypto_futures_ohlcv_1h_BINANCE_UM_PERP` fixes (2) and mostly fixes (1): 864
symbols, 2020-01-01 → 2026-10-26, **31 symbols end well before the panel end** (LUNAUSDT stops
2022-05-13, LENDUSDT 2020-11-10, BZRX 2021-12-19, YFII 2022-04-12, ANC 2022-05-13). It also carries
`quote_volume` and `taker_buy_quote_volume`, so the universe can rank on traded value directly
instead of `volume × close`.

## Proposed contract

```yaml
contract_id: CRYPTO_PERPS_BROAD_1H_V1
asset_class: crypto_perps
frequency: 1h
bars_per_year: 8760
exploratory_data_path: data/exploratory/crypto_perps_broad_1h.parquet
holdout_data_path: data/holdout/crypto_perps_broad_1h.parquet
train_start: '2021-01-01T00:00:00Z'
val_start:   '2021-07-01T00:00:00Z'
val_end:     '2025-07-01T00:00:00Z'
holdout_start: '2025-07-01T00:00:00Z'
holdout_end:   '2026-10-26T00:00:00Z'
n_folds: 4
label_horizon_bars: 168      # was 4 on the sibling contract: the hold is one week, not four bars
embargo_bars: 168            # was 1-2: an embargo shorter than the hold does not separate folds
execution_delay_bars: 1
execution_mode: NEXT_BAR
taker_fee_bps: 4.0
slippage_bps: 2.0
funding_bps_per_8h: 1.0
min_expected_move_to_cost_ratio: 2.5
data_provenance: verified_market
data_snapshot_sha256: <pin after the panel build>
```

Rationale for each non-obvious field:

- **Start 2021-01, not 2020-01.** Listings before 2020-01: 3 symbols. Before 2021-01: 81. A 25-name
  liquid universe needs a populated pool; 2020 H1 cannot supply one. 2021-01 is the first defensible
  start with margin.
- **val = 2021-07 → 2025-07 (4 years, 209 weekly rebalances).** The Stage-1 gate and the DSR both
  read the validation slice only, so this is where extra history converts into power:
  sqrt(209/52) = 2.0x on the t-statistic. 6 months of train exist only because the contract
  requires `train_start < val_start`; a rules strategy fits nothing.
- **`label_horizon_bars: 168` and `embargo_bars: 168`.** The sibling contract declares a 4-bar label
  for a 168-bar hold, and HYP-2026-125 had to disclose the mismatch because it measures IC against
  its own 168-bar forward return. Setting both to the true hold removes the ambiguity. These are
  semantic changes to purge behaviour, so they need explicit approval — they are the reason this
  is a proposal and not an edit.
- **Holdout 2025-07 → 2026-10 unchanged from the sibling.** Extending history backwards keeps the
  sealed window sealed. Crypto's holdout has never been read (unlike indices/metals, whose 2026
  window is already consumed), so it remains a genuine one-shot read.
- **Costs identical to the siblings** (4 bps taker + 2 bps slippage per side, 1 bps/8h funding) so
  cross-contract comparison stays clean. Measured liquidity supports them: the selected names trade
  a median $1.26B of 7-day quote volume.

## Panel build

Inputs, both already qualified in the lake:

| Input | Store | Shape |
|---|---|---|
| prices | `crypto_futures_ohlcv_1h_BINANCE_UM_PERP` | 864 symbols, 2020-01-01 → 2026-10-26 |
| funding | `crypto_futures_funding_<SYM>_PERP` (530 stores) | BTC/ETH from 2019, SOL from 2020 |

Steps:
1. Filter to `2021-01-01 → 2026-10-26`, drop symbols with fewer than 168 bars of history.
2. Join funding on the 8h settlement grid; the funding column is required by
   `simulate_positions` (`funding_rate`), and the lake's 1h store has none. **This join is the main
   engineering task**, not the pivot.
3. Write long-format parquet: `timestamp, asset, open, high, low, close, volume, quote_volume,
   funding_rate`; `asset` uppercase Utf8, prices Float64, timestamps cast explicitly (the lake mixes
   ms and us units across stores).
4. Split the file at `2025-07-01`: exploratory (2021-07 → 2025-07) and holdout (2025-07 → 2026-10).
   The holdout file is written once and never opened by an agent.
5. Pin `data_snapshot_sha256` = sha256 of the exploratory file.

Memory plan: 50k bars x 864 symbols x 3 matrices is ~1 GB as Float64. Warden's backtest slice is
capped at 18 GB (`MemoryMax`), and `earlyoom` prefers python, so the strategy must pivot only the
columns it needs and free the rest. HYP-2026-125's module already pivots three matrices — at 864
symbols that is 3x the current footprint; budget one run at a time, no parallel sweeps.

## Universe rule (strategy-level, not contract-level)

At each weekly rebalance (Sunday 00:00 UTC, every 168 bars):
- eligible: finite close 168 bars back, ≥80% of the window present, positive trailing 168-bar
  `quote_volume`;
- universe: top 25 eligible by trailing 168-bar `quote_volume`;
- rank those 25 by trailing return; long top 5 (+0.10 each), short bottom 5 (-0.10), gross 1.0,
  dollar-neutral, no stops, no vol scaling.

**Delisting handling — implemented 2026-10-28 (approved), no longer outstanding.** A symbol whose
data ends is force-flat from its own last tradable bar; the surviving leg then runs one-sided (half
gross, net exposure) until the next weekly rebalance, which rebuilds a full book from the survivors.
Covered by `tests/experiments/test_xs_weekly_momentum_delisting.py`; full suite 278 passed, 8 skipped.
The `-1.0` terminal-return convention in the `ml4t-define-universe` guide applies to bankruptcies;
for an exchange delisting we close at the last bar and let the last observed return stand.

## Disclosures that belong in the hypothesis file

1. **Validation data is partly spent.** The formation ladder run on 2026-10-28 swept 8 formations x
   (2023-01 → 2024-07) x (2024-07 → 2025-07) x 3 liquidity tiers. The val window proposed here
   contains both of those periods, so the last 2.5 years of it are *not* fresh evidence. Only
   2021-07 → 2023-01 is untouched. Warden's `k_eff` does not see unregistered sweeps, so the
   recorded DSR will be optimistic by that amount — state it in every report.
2. **Expected outcome is still uncertain.** With sqrt(4) more independent weeks, the skip-week
   variant's HAC t 0.82 → ~1.6, still short of the 2.0 Stage-1 gate unless the true IC is higher than
   measured. This contract raises the ceiling; it does not promise a pass.
3. **Venue mismatch remains.** This is Binance UM, not Hyperliquid. If the author's edge is
   HL-specific (retail-heavy flow, thinner book), a Binance result cannot confirm or refute it. Our
   HL holdings are second/tick/L2 from the recorder era only (~15 symbols, no 2025 history).

## Decisions needed from the principal

1. Approve `CRYPTO_PERPS_BROAD_1H_V1` as written, or change `label_horizon_bars` / `embargo_bars`
   from 168 back to the sibling values (which keeps the mismatch and should then be disclosed
   inside the hypothesis instead).
2. Approve the val window 2021-07 → 2025-07 despite disclosure 1, or shorten it to 2023-01 → 2025-07
   (130 weeks, less power, same contamination).
3. Approve the panel build running under the agent (write `data/exploratory/`), with the holdout file
   written once and never reopened.
4. Confirm the universe rule above is the one to test — in particular top-25 by trailing quote volume
   rather than a fixed list or an exchange-reported ranking.

## Build log — 2026-10-28

**Funding decision (principal chose option 3): the panel ships funded symbols only.** 530 of 863
symbols have a funding store; the other 333 are excluded rather than carried at `funding_rate 0.0`.

Two consequences the build exposed, neither visible when the option was chosen:

1. **The survivorship advantage mostly disappears.** Of the 31 symbols that die inside the window,
   26 have no funding store. Keeping funded names only leaves **5 delisted symbols** (AKROUSDT,
   BTSUSDT, TOMOUSDT, DODOUSDT, YFIIUSDT — all 2021/2022 deaths, none recent). The 863-symbol panel
   was chosen partly because it contains dead names; funded-only largely removes that.
2. **Breadth survives.** Funded symbols alive on 2021-07-01: 88 (2022-01: 104, 2023-01: 118,
   2025-01: 278), so a 25-name point-in-time universe exists from the first validation bar with
   margin.

**Funding bucketing had to change.** Warden charges `crypto_perps` 1h funding only on bars where
`hour % 8 == 0` (`_funding_settlement_mask`), but settlements arrive at 1h, 4h and 8h intervals
depending on the symbol. The builder buckets every settlement into its containing 8-hour window and
places the window total on the boundary bar, so an hourly-settling symbol still pays its full
funding. Verified: funding is non-zero on hours {0, 8, 16} only, 12.07% of bars.

**Written (exploratory only):**

| file | rows | symbols | span | sha256 |
|---|---|---|---|---|
| `data/exploratory/crypto_perps_broad_1h.parquet` | 6,349,427 | 372 | 2021-01-01 .. 2025-06-30 | `4f547925f1cfdf1d2794d7a9ad014849aef7d94cbef26ba9849f088a9c3c63ca` |

Checks on the written file: 0 duplicate `(timestamp, asset)` keys, 0 nulls in open/close/volume/
quote_volume, 0 non-positive closes, schema `timestamp: Datetime(us, UTC)`, `asset: Utf8`, prices
and volumes `Float64`.

**Holdout NOT written, by design.** `data/holdout/` is `root:trader`, so `--execute` refuses at the
sealed partition (`PermissionError`, exit 3). The principal creates it, or opens the window with
`scripts/lock_holdout_permissions.sh`; the builder then fills it in one pass. Nothing under
`data/holdout/` was read or listed.

**Still outstanding:** the contract YAML itself. `contracts/` is the principal's; the pin to paste is
`data_snapshot_sha256: 4f547925f1cfdf1d2794d7a9ad014849aef7d94cbef26ba9849f088a9c3c63ca`.

**Open alternative if the dead names matter:** keep all 863 symbols and charge the 333 unfunded ones
the contract's declared `funding_bps_per_8h: 1.0` per settlement instead of `0.0`. That restores 26
delisted names and never understates funding, at the cost of a uniform constant in place of observed
rates. Rebuilding is a single `--execute --force` run.
