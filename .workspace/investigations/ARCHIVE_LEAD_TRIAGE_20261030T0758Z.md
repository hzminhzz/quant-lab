# Independent archive lead triage — 2026-10-30T07:58Z

## Controls and checkpoint reconciliation
- `uv run warden check-worktree` passed.
- Governance guard status was `READY_FOR_DISTINCT_RESEARCH` at `2026-10-30T07:57:47.839429+00:00`.
- Reconciled completed batch only: 139 hypotheses, 71 full trials. `HYP-SOC-BTA-NQMON-20261030` and `HYP-SOC-QC-TOM-ES-20261030` are immutable Stage-1 rejections and were not replayed, inverted, tuned, or used as templates.
- No Warden command, contract activation, protected-data access, or publisher-code execution occurred in this triage.

## Newly reviewed independent leads

### 1. Finetiq / Marc Malek — Always-in N-day reversal
- URL: https://ideas.finetiqsystems.com/en/ideas/always-in-breakout/
- Archive capture SHA-256: `8ebdfea32a8bae2f6eb77373f1a80e8f9aa907bcd707c25a66cec2929c3f8f73`.
- Source market/timeframe: diversified futures, FX, bonds, indices and commodities; daily bars.
- Original rule: remain long until a new N-day low, reverse short; remain short until a new N-day high. Source says Malek searched N=1..200 and chose approximately N=70.
- Expected hold: source states approximately 1.5×N, or 105 days.
- Disposition: `BLOCKED_LABEL_HORIZON_AND_SOURCE_AMBIGUITY`.
- [MISSING] author-specified stop-order versus close/next-open reversal timing and sizing. The publisher supplies both readings and a sizing method. Existing short-label contracts cannot stand in for a 105-day source horizon; the source search must remain counted if a future dedicated contract is proposed.

### 2. QuantConnect — Forex Carry Trade
- URL: https://www.quantconnect.com/research/15264/forex-carry-trade/
- Archive capture SHA-256: `8ae540e04f329d7f1981f5cf8c891e19dc9db081cd8c9738e6ee23d0fe2864ec`.
- Original rule: monthly long highest policy-rate currency and short lowest among nine specified FX pairs.
- Disposition: `BLOCKED_MISSING_PIT_RATES`.
- Source states that its central-bank-rate dataset was discontinued in 2016. Existing OHLC-only FX contracts do not establish the required point-in-time rate series; do not substitute retrospective rates.

### 3. QuantConnect — Low-frequency-component FX momentum
- URL: https://www.quantconnect.com/research/15374/the-momentum-strategy-based-on-the-low-frequency-component-of-forex-market/
- Archive capture SHA-256: `89af51abf4bb47a466d05153335b3cd3d326c82e47f5ed6c34085aedaf428589`.
- Source method: HP-filter exchange rates then apply MA(1,2) to the estimated trend component.
- Disposition: `BLOCKED_NONCAUSAL_OR_UNSPECIFIED_FILTER`.
- The extracted source describes a full-sample HP optimization but does not provide a causal, endpoint-safe real-time implementation or frozen parameters; it also labels performance parameter-sensitive. A causal replacement would be a new strategy, not a faithful replication.

### 4. QuantConnect — Dynamic Breakout II
- URL: https://www.quantconnect.com/research/15373/the-dynamic-breakout-ii-strategy/
- Archive capture SHA-256: `c32b636df3c5071720ce45c22da424e291ca95740e8d1a3ba331c54f37bbb15d`.
- Source market: EURUSD/GBPUSD daily; dynamic volatility-derived lookback, Bollinger band, breakout and dynamic moving-average exit.
- Disposition: `BLOCKED_ADAPTIVE_PARAMETER_SELECTION`.
- Source marks the post pending review and makes the lookback change with historical performance/volatility. Exact parameter-update timing, bid/ask execution, and precommitted adaptation logic must be specified before a PIT-valid Warden candidate exists.

### 5. QuantConnect — Intraday dynamic pairs
- URL: https://www.quantconnect.com/research/15347/intraday-dynamic-pairs-trading-using-correlation-and-cointegration-approach/
- Archive capture SHA-256: `2266f9a3417a5f06d60babf9176b0df921661780c14a9bf422508184c9d318de`.
- Source market: US bank-sector equities at 10-minute cadence.
- Disposition: `BLOCKED_INSTRUMENT_AND_SELECTION_MISMATCH`.
- The article explicitly permits optimising correlation/cointegration windows, data interval, and thresholds. No verified ETF/equity universe or 10-minute PIT panel exists in the current CFD/perp research contracts; an FX or index-CFD recast would not test the source strategy.

## Decision
No complete source-faithful, Warden-compatible candidate was found. Do not manufacture a long-horizon proxy or transform a stock/equity source into a CFD/perp strategy. Next discovery should favor complete rules with stated market, causal execution, and a holding horizon already covered by a verified contract.
