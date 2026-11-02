# CFD / OHLCV proxy admission — Stage 0/1 and proxy-only Stage 3

**Decision (2026-11-01):** The principal authorizes Dukascopy CFD price history as a **historical research proxy** when exact exchange-futures data or bid/ask quotes are unavailable. The target is mid-frequency strategy discovery and proxy-only robustness testing (5m, 15m, 1h, swing), not high-frequency execution. **Futures market data is not a mandatory input for exploratory screens or separately labeled proxy robustness. Future-dated information remains forbidden.**

This guide is invoked by [BLOCKER-RECOVERY.md](BLOCKER-RECOVERY.md) whenever a dataset, quote, or futures-source requirement would otherwise block a research campaign.

## Contract-by-purpose, not blanket rejection

| Stage / claim | Eligible data | Required treatment |
| --- | --- | --- |
| Stage 0: mechanism and feasibility | Historical OHLCV from a reputable real feed, including Dukascopy CFD | Document instrument mapping, target market, horizon and estimated round-trip friction; no target-venue PnL claim |
| Stage 1: preliminary signal screen | Audited CFD OHLCV with valid open timestamps and history covering the validation window | **Separately versioned `NEXT_BAR` proxy contract** with pinned data manifest/SHA, positive spread/fees/slippage, execution lag and causality checks; Warden logs every attempt |
| Exploratory extensions / research diagnostics | Same verified proxy, with temporal splits and full attempt accounting | Clearly label as proxy; run cost/latency/parameter sensitivity; do not silently relabel as live-venue evidence |
| Stage 3: **proxy-only robustness**, not venue qualification | The same separately pinned audited CFD OHLCV lineage under `NEXT_BAR` | Run Warden stress, DSR/selection accounting, +1 bar latency and parameter neighbors without relaxing thresholds; label results `PROXY_STRESS_ONLY` even if Warden records `CANDIDATE_FOR_QUALIFICATION` |
| Target-venue economic/execution qualification | Executable price basis and costs appropriate to the intended broker/venue | Match product, broker rules, financing, spread, fill latency, liquidity and order model; assess basis risk and slippage; never promote CFD proxy results directly into venue-qualified claims |
| Protected holdout / live capital | Separately authorized, valid evidence and risk approvals | Warden protected one-shot holdout and principal authorization unchanged; no automatic order placement |

An exchange-futures strategy may be **screened on a correlated CFD** if the thesis is about directional price dynamics and a mapping is defended. This does **not** establish futures-specific carry, roll, order-book, funding, spread or liquidity edge. If the thesis requires those mechanics, classify it as `BLOCKED_REQUIRED_FEATURE` and continue other candidate mechanisms. Stage-3 *proxy* robustness remains permissible for price-only hypotheses; it is not target-venue execution qualification.

## Existing code and sources (verify live state)

- The canonical lake is `/home/quant/dev/quant/ml4t-data/.data/store`, with provider/authority/quality records under `.catalog/research-dataset-catalog.json`, `research-readiness.json` and the source repository's documented audit reports. Search the catalog for the **actual** instrument/provider and date window; never infer eligibility from the store's name alone.
- Candidate index CFD histories include `index_minute_NQ`, `index_minute_ES`, `index_minute_DAX`, `index_minute_NIKKEI225`, `index_minute_HK33` and `index_minute_EUSIDXEUR`. FX candidates include `forex_minute_*` and `forex_daily_*`. These are candidate paths, **not** assertions that every symbol/window passes quality checks today.
- `scripts/salvage_multi_asset.py` contains native normalization/resampling functions. Inspect live code and catalog before reuse; validate source IDs, timeframe boundaries, gaps, bar timestamp convention, abnormal prices and quality audits. Do not simply forward-fill missing trading bars or sum meaningless tick-volume proxies.
- Warden `src/warden/runner.py` already implements delayed `NEXT_BAR` open-to-open returns and positively modeled `taker_fee_bps + slippage_bps`. `QUOTE_SIDE` explicitly requires real `bid` and `ask`. `src/warden/provenance.py` enforces exact file SHA/manifest matching; no silent source substitution is permitted.
- Example: `INDICES_1H_V2` is a `NEXT_BAR` contract backed by an already pinned OHLCV panel. The existing `FX_MAJORS_15M_V1` is `QUOTE_SIDE`: an OHLCV-only proxy needs a **new** `FX_MAJORS_15M_CFD_PROXY_V1`-style contract, not an in-place change to `FX_MAJORS_15M_V1`. The example name is not a created or approved contract.

## Procedure for a missing-futures / missing-quotes blocker

1. Determine whether the **hypothesis** needs a derivative-specific observable (funding, carry, basis, roll, order flow, limit-book microstructure) or merely a directional price history. If derivative-specific, acquire the required feature or defer that thesis; continue other supported work.
2. Query the canonical catalog and audit record. Require the target symbols, historical validation window, appropriate trading calendar, valid non-null OHLC and strictly ordered timestamps. Check off-grid bars, duplicates, stale/missing periods, extreme jumps, source conflicts and as-of availability. Do not read protected holdout partitions as a convenience for inspection.
3. For a directional thesis, choose an existing **frozen** `NEXT_BAR` contract with matching market, cadence, exact pinned dataset and horizon; if none fits, prepare a **new versioned proxy study** with independent panel/digest, provenance manifest, train/validation windows and cost assumptions. Respect the principal's control over contract approval and existing hashes. Do not patch old contract IDs or use another study's selection ledger.
4. Model explicit positive one-way costs including an estimate for the spread **not already represented by OHLCV**, commission and slippage. `NEXT_BAR` fills use next-open, not bar-close or the observed midpoint. Consider a spread/cost band and stress at 1.5x/2x rather than claiming a precise quote. **The current salvage normalizer inserts `funding_rate=0.0` when absent; that is a schema placeholder, not evidence of zero CFD swap/financing.** For overnight positions, add justified financing economics in a new approved research model or label the result unqualified for overnight economics. Never treat unknown perpetual funding, financing, collateral or roll as observed zero.
5. Run Warden verification when the strategy exposes its supported feature interface, then Stage 1 screening on the **new approved lineage**; preserve all variants in selection accounting. On an actual statistical reject, move to the next distinct mechanism; on an interface or data failure, RECOVER again. A promising screen may proceed to **proxy-only Stage 3 stress** with all normal gates, but it cannot be called venue-qualified. Report `PROXY_SCREEN_PASS`, `PROXY_SCREEN_REJECT` or `PROXY_STRESS_ONLY`, plus dataset SHA, market mapping, date window, cost assumptions, Warden attempt ID and limitations. These report labels are **not** actual Warden DB verdict strings.
6. If a candidate survives, validate against target-venue quotes/fills/tick data **at the final execution-oriented qualification stage**, not as a prerequisite for exploring every thesis. The first executable fill and time-of-knowledge must be verified; even a proxy Stage 3 pass does not authorize a protected target-venue holdout read or live trading.

## Non-negotiable controls

- No lookahead: at decision time, features may use only already closed/available bars. A bar's interval-start timestamp is **not** its close/availability time. Warden's one-bar execution lag is mandatory; its presence does not excuse future features.
- No fabricated bid/ask, synthetic *observations*, guessed provider timestamps or frozen-file rewrites. Modeled spread/fees are stated assumptions, not actual quotes.
- No approval laundering: the principal approved **research use** of Dukascopy CFD proxies; Warden contract authorizations, frozen historical decisions, protected holdout reads, venue-specific qualification, production deployment and live orders remain separate.
- Do not treat missing bars **after** the requested `val_end` as a reason to block the fixed historical Stage 1 screen. Equally, do not pull unseen data from the holdout to improve the screen.
- Source substitution changes the population and sometimes the underlying exposure. Keep original-futures and proxy lineages, DSR/search attempts and result labels distinct, with traceable parent/derivative links where appropriate.

## Evidence checklist for the agent

Before writing `BLOCKED_DATA`, record: exact dataset and contract looked up, full available history, needed study window, whether the missing part lies *inside* validation, alternative Dukascopy CFD source and quality results, which thesis mechanisms are proxy-compatible, the selected remedy and the next Warden action. A status-only answer that says “futures or bid/ask unavailable” without evaluating the approved CFD path fails the recovery procedure.
