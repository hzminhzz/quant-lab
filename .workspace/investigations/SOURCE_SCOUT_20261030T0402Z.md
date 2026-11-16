# Source scout — 2026-10-30T04:02Z

## Scope
Original-source review only. No Warden registration, screen, backtest, contract activation, holdout access, or publisher-code execution occurred.

## Evidence reviewed
- Breakout Trading Academy, “7 Breakout Trading Strategies That Work (With Exact Rules),” author not stated in captured text, published 2026-10-24T11:25:29Z, observed 2026-10-30T04:02Z.
  URL: https://breakouttradingacademy.com/breakout-trading-strategies-examples/
- Breakout Trading Academy, “How to Build a Short Opening Gap Strategy,” author not stated in captured text, published 2026-10-14T17:37:24Z, observed 2026-10-30T04:02Z.
  URL: https://breakouttradingacademy.com/how-to-build-a-short-opening-gap-strategy/
- Quantified Strategies, “A GLD Day Trading Strategy With Long Signals,” author not stated in captured text, published 2026-10-08T08:28:56Z, observed 2026-10-30T04:02Z.
  URL: https://quantifiedstrategies.substack.com/p/a-gld-day-trading-strategy-with-long

## Candidate proposals

### BTA one-bar EMD continuation — DEFERRED
Original rule trace: E-mini S&P MidCap 400; 30-minute 24-hour bars; yesterday close > open; today high already > yesterday close; long-stop at last close + 7 points, entry only 08:00–15:00 Chicago; $1,000 stop; exit at next-bar open.
Economic mechanism: short-horizon continuation after an up session and early confirmation.
Friction/counterparty: intraday liquidity provision after an opening continuation trigger.
Required market data: EMD 30-minute session-aware OHLC, stop-fill semantics, and transaction-cost model.
[MISSING] source-defined rollover handling, contract-expiry construction, exact order priority when entry and stop are both reachable in one bar, and size/risk scaling.
Disposition: no approved reviewed EMD 30-minute Warden contract or source-contemporaneous validation split was established. The post is dated 2026-10-24, so it cannot be called unseen against an earlier 2024–25 validation panel. Do not register until a PIT-valid post-publication contract and all missing execution rules are resolved.

### BTA Nasdaq Hitter — DEFERRED
Original rule trace: NQ 30-minute 24-hour bars; ADX > 17.5; long stop at highest close of last N bars; entries 08:00–15:00 Chicago; $1,000 stop; exit end of day.
Economic mechanism: intraday trend continuation after a rolling-close breakout in strong-trend state.
[MISSING] N, ADX construction details, contract-roll treatment, and same-bar stop/entry ordering.
Disposition: incomplete source rule set; no registration.

### BTA short opening-gap fade — REJECTED AS A SOURCE CANDIDATE
Original text explicitly reports a 3×3×25 prototype search and a post-selection Thursday refinement. It therefore cannot seed an independent clean hypothesis, and its execution rules lack a frozen gap/ATR/multiplier specification.

### Quantified Strategies GLD long day trade — BLOCKED
The original post states daily-signal/next-session execution and 5-minute intraday testing, but the actual long rules are paywalled.
[MISSING] every entry, exit, stop, and threshold rule.
Disposition: no registration.

## Program implication
Breakout Trading Academy is a high-value original source for rules, but its available posts are currently a proposal pool rather than testable Warden candidates. The next candidate must use a source published before its validation window, a reviewable pinned contract, and a fully disclosed rule set.
