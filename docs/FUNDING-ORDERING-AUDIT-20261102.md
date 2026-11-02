# Binance funding-open ordering audit — 2026-11-02

## Disposition

**Low-volatility remains STAGE1_PASS_UNQUALIFIED.** No new Warden screen,
backtest, protected holdout read, or live trading authorization occurred.

This is an independent, *read-only executable accounting sensitivity*
stacked on [quant-lab PR #41](https://github.com/hzminhzz/quant-lab/pull/41)
at commit `2dabd2f`, not an alteration of the frozen evaluator, contract,
positions, dataset, or historical trial ledger.

### What was uncertain

The existing `scripts/audit_lowvol_event_funding.py` aggregates historical
Binance funding events into the UTC hour, then applies funding to
**the position after a Warden NEXT_BAR open fill**. When settlement and a
hypothetical market fill occur at the exact same timestamp, the hourly
OHLCV panel cannot determine whether the old or new position bore the
funding payment. The conservative conclusion must not depend on a
silently chosen order.

`scripts/audit_funding_boundary_ordering.py` therefore takes the
**identical already-frozen 24h low-vol target positions**, the existing
`CRYPTO_PERPS_TOP100_1H_V1` contract and real archived Binance event
timestamps. It reuses Warden's existing one-way fees, open-to-open gross
returns and one-bar execution lag, and applies explicit terminal-flat
accounting at the validation cutoff. It computes separate funding cashflows:

- **Pre-open**: events within 1,000 milliseconds after the exact UTC
  open are charged to the position held **before** that open.
- **Post-open**: those events are charged to the position filled at
  that open (the earlier diagnostic's assumption).
- **Optimistic/adverse per-event bounds**: the lower/higher signed
  funding cashflow for each ambiguous event. These are sensitivity
  bounds, **not** a possible independent fill policy that can select
  ordering after observing rates.
- Events later than one second after open use the contemporaneously
  held position in both cases.

The one-second uncertainty window is an **explicitly disclosed
research assumption**, not an observed broker-fill timestamp; the
Warden NEXT_BAR model assumes an open fill without queue or spread
timestamps. No assumption of future settlement knowledge enters the
strategy's targets.

## Real observed-source results

The exploratory hourly Binance price panel was independently
verified by SHA256 `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc`.
Only archived funding shards prior to 2025-07-01 UTC were opened.
The 1,200 source shards contain **115,959** unique observed funding
events over 100 original coins in the frozen 2024-07 through 2025-06
validation window. All events fall within one second of their hourly
open, including **28,846 with a nonzero millisecond offset**.
The base policy has just **26 events** for which old/new positions differ;
the +one-hour delay policy has **29**.

| Net Sharpe | Frozen fixed 8h mask | Observed pre-open | Observed post-open | Per-event worst bound |
|---|---:|---:|---:|---:|
| 1x trading cost | 0.90835 | **0.78882** | **0.78524** | **0.78487** |
| 2x trading cost | 0.14866 | **0.02917** | **0.02562** | **0.02526** |
| 1x cost, extra +1h execution delay | 0.76144 | **0.64433** | **0.64126** | **0.64072** |

The largest one-second ordering ambiguity changes 1x net
Sharpe by approximately **0.0043** between best and worst bounds.
The much larger difference is between Warden's assumed eight-hour
funding cadence and observed per-asset event timestamps.

**None of these results satisfies the frozen full Stage-3 economic
thresholds.** This is not a new trial or independent predictive test.
The 24h low-vol policy's original Stage-1 predictive IC still depends
on a hindsight-selected Top-100 universe; PR #41 now has a separate
**unsigned** 364-symbol PIT dataset proposal. Its source availability
audit finds **14.39% rows with missing observed funding**.
Unobserved funding events remain **UNKNOWN**, even if the observed-only
cashflow vector has zero in that hour. The earlier adverse missing-event
stress is a distinct *hypothetical* scenario, not observed payments.

## Reproducibility and evidence

The immutable JSON is
`docs/evidence/arxiv_stage1_20261102/funding_boundary_ordering_20261102.json`
with SHA256:

`d3c5853787f7805a7106cd3cb692fd35164d9a29244341f4794276a2ca20c5c4`

Run the script **only against the original pre-cutoff SHA-verified
exploratory panel** (never the protected holdout):

```bash
uv run --frozen python -m scripts.audit_funding_boundary_ordering \
  --panel-path /path/to/sha-verified/crypto_perps_top100_1h.parquet \
  --output docs/evidence/arxiv_stage1_20261102/funding_boundary_ordering_20261102.json
```

The output is write-once and will refuse an existing path. To
reproduce, use a **fresh isolated worktree** and confirm the dataset SHA
before running. Historical source settlement events are read from the
pre-2025-07 `ml4t-data` funding archive, whose source shard digests
are included in the output receipt. Original PR #41 evidence is
unchanged. The new checker refuses duplicate clocks, shifted timestamps,
unknown positioning, unsupported event-rate records and unexpected
panel/contract digests.

## Verification

- 12 new direct timing/causality unit tests passed.
- 9 existing pinned-evidence and source-funding tests passed (21 total).
- Ruff passed on the two new Python files.
- `uv run --frozen warden check-worktree` passed.
- No protected evaluator, contract, manifest, source market archive,
  registry, sealed holdout or live execution component was mutated.

**Next governed gate:** Confirm historical asset coverage and
true funding-event/position ordering on an approved point-in-time
contract. The old low-vol strategy must not be rescreened on its
consumed validation window merely to search for a better result.
