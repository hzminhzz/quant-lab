# FX Stage 3 — the unblock, prepared and not applied

Two independent blockers stop every FX_MAJORS Stage-3 row. Both need a contract or data change,
which the agent prepares and the principal approves. **Nothing in this document has been applied**:
`contracts/`, `data/exploratory/` and `data/holdout/` are untouched, no manifest was regenerated, no
holdout was read.

Evidence for each claim: `.workspace/fx_stage3/decision_record.md`. Live ledger:
`.workspace/fx_stage3/stage3_closure.md`.

---

## B1 — `QUOTE_SIDE` has no quote data

Measured: all seven `FX_MAJORS_*` contracts are `QUOTE_SIDE`; every exploratory panel and every
`forex_minute_*` lake store is `[timestamp, (asset|symbol), open, high, low, close, volume]`; all five
preflights refuse with

```
ExecutionDataError: QUOTE_SIDE contract FX_MAJORS_1D_V1 requires ['bid', 'ask'];
a close-price fallback is not a valid execution basis
```

### Option A — synthesise a two-sided quote from a declared spread

At panel build, `bid = mid*(1 − s/2)`, `ask = mid*(1 + s/2)` with `s` a **declared, sourced,
per-pair** spread (not a fitted number), `mid` = the bar's close. `execution_returns` then charges the
spread once per entry and once per exit, which is the correct QUOTE_SIDE semantic.

Two things must move together or the cost is wrong:

1. **Double counting.** QUOTE_SIDE already charges the spread through `long_ret = bid₊₁/ask − 1`, and
   `simulate_positions` *also* charges `turnover × (taker_fee_bps + slippage_bps)`. With
   `slippage_bps: 0.5` retained, a synthesised 1.0 bps spread costs 1.0 + 1.4 = 2.4 bps round trip.
2. **You cannot rebase slippage to zero in YAML.** `contracts.py:75-78` rejects a non-positive
   `slippage_bps`, so a cost-neutral rebase is a *code* change too (`slippage_bps >= 0` for
   `QUOTE_SIDE`), which is an evaluator change and needs its own approval.

Why this is not just a data edit: `bid`/`ask` columns change the panel bytes, so
`data_snapshot_sha256` changes, so this is a **new contract version**, not a re-pin.

### Option B — `execution_mode: NEXT_BAR`

Matches all 24 other contracts in the repo and needs no synthetic data. The panel already has
everything NEXT_BAR needs.

**It is anti-conservative for FX.** NEXT_BAR fills `open₊₁/open` and charges only
`turnover × 0.7 bps`. For a book that turns over 0.4–6.8×/yr that is essentially free, so the spread
disappears from the model entirely and the Stage-3 Sharpe would be optimistic by roughly the spread
times turnover. If this route is chosen, raise `slippage_bps` to carry the half-spread explicitly
(e.g. 0.5 → 1.0 bps for the majors, more for GBPJPY) and record why in the manifest.

### Option C — source real quotes

No bid/ask source exists in the lake for FX. This is the only fully honest route and it needs a
provider decision, not an agent action.

### Recommendation

For 1D: **Option B, `NEXT_BAR`, on a panel rebuilt from Dukascopy daily (Option A0 for B2)**. It is
the only mode the lake can serve, it is the weaker model, and the fix is to carry the spread
explicitly in `slippage_bps` instead of leaving the half-spread free — not to synthesise quotes.

For any intraday FX contract: **do not fall back to `NEXT_BAR`**. At 1H a round trip is 29% of a
median bar move, so a model that omits the spread is not merely optimistic, it manufactures edge.
Those contracts stay blocked until real quotes exist. Option A (synthesised quotes + the
`slippage_bps >= 0` rebase) is the fallback only if the principal prefers quote-side semantics with a
declared spread to next-bar with a declared spread.

For XAUUSD only, Option C already exists: `forex_tick_XAUUSD` has `bidPrice`/`askPrice`.


### What the lake actually contains (measured 2026-10-28)

| Store | Provider | Schema | Coverage |
|---|---|---|---|
| `forex_daily_*` (13 pairs, all 8 majors) | Dukascopy | OHLCV, **no bid/ask** | 2014-01-01 → 2026-10-14, 3,999 rows, 312.8 bars/yr |
| `forex_minute_*` (8 majors) | LSE | OHLCV, **no bid/ask** | 2015-01 → 2026-10, this is what the panels were built from |
| `forex_tick_XAUUSD` | Dukascopy | `bidPrice`, `askPrice` | 2022-01-02 → 2026-06-12, 190.2M ticks |

So no quote data exists for any currency pair at any frequency. `QUOTE_SIDE` on FX majors is
unimplementable without inventing quotes; `NEXT_BAR` on a Dukascopy-daily panel is the only honest
1D model. It is the *weaker* model (it fills at the open mid and charges only the flat
`taker_fee + slippage = 0.7 bps` per turn, so the half-spread is free), which is why the spread must
be carried explicitly in `slippage_bps` rather than left at its current floor.

Measured on the one store that does have quotes (gold, 6 months of 2024, 14.3M ticks): median spread
**1.72 bps**, p90 2.28, p99 3.71. That is the magnitude a quote-side model charges and a next-bar
model does not. It is gold, not EURUSD, so it calibrates nothing for the majors — that gap needs a
provider spread sheet or real pair quotes.


---

## B2 — corrupt bars in the source minute store

Ten daily bars carry a wrong price level. Root cause is upstream in `ml4t-data`, not in
`salvage_multi_asset.py`: one corrupt minute poisons a whole day because the resample takes
`close = last` and `low = min`.

| Asset | Date | defect at minute level |
|---|---|---|
| USDCAD | 2024-12-20 | 21:59 `open 1.43271 → close 1.09843` |
| USDCAD | 2025-01-10 | 10.49% inside one minute |
| USDCAD | 2024-11-08 | 11.88% inside one minute |
| USDCAD | 2024-11-22 | 8.44% inside one minute |
| USDCAD | 2024-11-01 | 6.55% inside one minute |
| USDCAD | 2024-09-06 | 3.73% inside one minute |
| USDCAD | 2024-10-25 | 2.45% inside one minute |
| AUDUSD | 2022-10-09 | stub partition: 192 minute rows, 15.71% minute-to-minute jump |
| AUDUSD | 2024-11-29 | 2.79% inside one minute |
| AUDUSD | 2024-10-04 | 2.26% inside one minute |

Nine of the ten sit inside the 2023-01..2025-07 validation window.

### Option A0 — rebuild the 1D panel from Dukascopy daily, not LSE minutes (recommended)

`forex_daily_*` in the lake already carries all eight majors at daily frequency, sourced from
Dukascopy: 3,999 rows, 2014-01-01 → 2026-10-14, 312.8 bars/yr (the contract's `bars_per_year: 314`
is within 0.4%), one row per Mon–Fri plus the Sunday session, anchored 00:00 UTC.

It is **clean on every date where the LSE-minute panel is corrupt**:

| Asset | Date | LSE-derived panel close | Dukascopy daily close |
|---|---|---|---|
| USDCAD | 2024-12-20 | 1.09843 | **1.43735** |
| AUDUSD | 2022-10-09 | 0.73580 | **0.63601** |
| AUDUSD | 2024-11-29 | 0.63283 | **0.65068** |

Switching the 1D source to Dukascopy fixes B2 without touching the upstream minute store and without
an exclusion list, and it keeps `NEXT_BAR` (Dukascopy daily is OHLCV only — no quotes). Cost: the
panel is a different provider, so the exploratory window becomes 2014-01-01 → 2026-10-14 and the
contract is a new version either way.

### Option A — repair upstream, then re-salvage
 (recommended)

In `ml4t-data`: null the offending minute rows against a continuity rule (a minute whose
`|close/open − 1|` exceeds a hard bound is not a price), re-run `scripts/audit_store_quality.py` on
`forex_minute_USDCAD` / `_AUDUSD`, then re-salvage and pin the new digest. This keeps every genuine
move and every genuine minute.

### Option B — record an explicit exclusion list at panel build

If the source cannot be repaired, add the ten `(asset, date)` pairs to a declared exclusion list in
`salvage_multi_asset.py`, drop those daily bars, and say so in the manifest. Dropping is the only
consumer-side action that fabricates nothing: the series then carries the true multi-day move across
the gap. It loses one day of real return per bar, and that loss must be stated in the ledger.

### Option C — do nothing

Every FX Stage-3 Sharpe, drawdown, CVaR and DSR stays uninterpretable. A 30% fake USDCAD day inside
the validation window is larger than any plausible edge.

---

## Exact sequence once both are approved

```bash
# 0. governance: human authors contracts/fx_majors_1d_v2.yaml and the pinned manifest
cd /home/quant/dev/quant-lab
uv run warden generate-manifest --human-approve-manifest      # human-only flag

# 1. the five hypotheses are re-homed to V2, sharing the lineage budget (they never ran)
#    parent_hypothesis_id: HYP-2026-115..119 respectively, new ids HYP-2026-12N

# 2. one spec per hypothesis, retargeted to FX_MAJORS_1D_V2, run ids RUN-HYP12N-001
uv run python /tmp/preflight_trial.py experiments/active/fx_stage3/12N_*.yaml   # must print PREFLIGHT_OK

# 3. per hypothesis, in this order
uv run warden backtest experiments/active/fx_stage3/12N_*.yaml
uv run warden verify-causality --run-id RUN-HYP12N-001
uv run warden audit-check --run-id RUN-HYP12N-001

# 4. regenerate the ledger; the blocker cell flips to "none" only when the contract is executable
uv run python scripts/build_fx_stage3_closure.py
uv run python scripts/build_fx_stage3_closure.py --check
uv run pytest tests/experiments/test_fx_stage3_closure.py tests/test_contracts.py \
              tests/test_issue4_execution_alignment.py -q
```

`register_contract` refuses a changed `spec_hash` ("Registered contract is immutable; choose a new
contract_id"), so the V1 contract cannot be edited in place even by accident. The five V1 hypotheses
stay in the registry as the record of this blocked attempt and must not be deleted.

## Why not just re-pin V1

Because `data_snapshot_sha256` sits inside `spec_hash`, re-pinning is not a path edit — it is a new
contract. The repo rule is explicit: contract versions are for real data/window/cost changes only,
and repairing corrupt bars and adding an execution basis are exactly that.

## Tests that must pass after the change

| Test | What it protects |
|---|---|
| `tests/test_issue4_execution_alignment.py` | QUOTE_SIDE still fails closed without bid/ask |
| `tests/test_contracts.py` | the V2 contract loads and is immutable |
| `tests/test_multi_asset_salvage.py` | the new panel's shape, digest and provenance |
| `tests/experiments/test_fx_stage3_closure.py` | the ledger still matches the registry |
| `uv run warden check-worktree` | protected files and LOC budget intact |
