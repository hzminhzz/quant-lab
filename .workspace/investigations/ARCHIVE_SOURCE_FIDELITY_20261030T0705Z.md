# Archive source-fidelity review — 2026-10-30T07:05Z

## Scope and controls
- Read the archive shortlist and review priorities before research.
- VPS checks: `uv run warden check-worktree` passed; governance guard reported `READY_FOR_DISTINCT_RESEARCH` at `2026-10-30T07:05:19.221692+00:00`.
- No protected data path was opened. No publisher code was executed.
- No hypothesis registration, Warden verification, screen, backtest, contract activation, or trial occurred.

## Five distinct original-source leads

### 1. BTA — Best Days to Trade E-mini S&P 500 and NASDAQ
- URL: https://breakouttradingacademy.com/the-best-days-to-trade-emini-sp-500-and-nasdaq-part-1/
- Published: 2023-06-13. Archive current-capture SHA-256: `cf78481a6a78b40f1c0ae6b200d173ca903af3e3af2c830f44633134c9c04a67`.
- Original evidence: the author describes an hour-by-hour RTH study and a directional day-of-week map: Monday long, Tuesday short, Wednesday long with caveat, Thursday long, and NASDAQ Friday short.
- Classification: `NOT_A_STANDALONE_TRADE_RULE`.
- [MISSING] entry trigger, exit rule, stop, contract/CFD mapping, position sizing, and a fixed source-selected configuration. The source explicitly presents the map as a filter or sizing overlay for an independently validated core strategy. It cannot be preregistered as a standalone rules strategy.

### 2. BTA — Can You Create An Effective Strategy To Day Trade Gold?
- URL: https://breakouttradingacademy.com/can-you-create-an-effective-strategy-to-day-trade-gold/
- Published: 2022-08-10. Archive current-capture SHA-256: `2e40ea6548d6cef65ab33af811560b2dcf636e1f2538a114734cfd47b3adf90c`.
- Original evidence: it proposes alternatives: a 30- or 60-minute COMEX opening-range breakout; London-fix momentum; and macro-event continuation.
- Classification: `BLOCKED_INCOMPLETE_RULES`.
- [MISSING] selected range length, session calendar/DST conversion, exact stop, target, entry precedence, source-selected variant, and CFD costs. The alternatives are not one predeclared strategy.

### 3. BTA — Big Index Trading Secret
- URL: https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
- Published: 2022-11-30. Archive current-capture SHA-256: `a5c0671d0136297977aebd4e3b81bc708fdd46cb56a063e263827ec20d205b5a`.
- Original evidence: it gives “buy Monday, sell Friday” as a one-rule demonstration, on index futures, and recommends adding a breakout and stop.
- Classification: `BLOCKED_EXECUTION_SEMANTICS`.
- A daily `NEXT_BAR` contract cannot faithfully enact both the stated Monday-open entry and Friday-close exit. A futures-to-CFD substitution and a different close/next-open execution rule would be an adaptation, not the original rule. No registration.

### 4. Finetiq — Flush to AVWAP/EMA confluence
- URL: https://ideas.finetiqsystems.com/en/ideas/avwap-ema-flush/
- Current source capture: 2026-10-30; no author publication date was available from the capture.
- Original author concepts include AVWAP/EMA support confluence, a daily low-based stop, and an EMA exit. The publisher adds material numerical choices including ADR tolerances, lookbacks, and implementation paths.
- Classification: `BLOCKED_SOURCE_VS_PUBLISHER_RULE_MIX`.
- [MISSING] source instrument/universe and original numerical definitions. Finetiq labels several parameters as its own implementation, so using them would not be a faithful author-rule test. Equity universe logic cannot be silently transferred to CFD indices, FX, metals, or perps.

### 5. cloudQuant Backtrader — EURUSD breakout test
- URL: https://github.com/cloudQuant/backtrader/blob/884ea41d5fb0079cd7744525f835f3fc3c2986b1/tests/functional/strategies/trend_following/test_0044_0575_eurusd_breakout.py
- Archive reference: immutable commit-pinned test path.
- Classification: `BLOCKED_SOURCE_UNAVAILABLE`.
- The public source fetch failed before the pinned code could be reviewed. Regression-test metadata is not a source-faithful rule specification, and publisher code was not executed or reconstructed.

## Decision
No Warden-ready hypothesis emerged. The next candidate must supply a complete original entry/exit specification, a source date compatible with the validation period, and an execution model representable by a pinned contract without converting its instrument or fill semantics.
