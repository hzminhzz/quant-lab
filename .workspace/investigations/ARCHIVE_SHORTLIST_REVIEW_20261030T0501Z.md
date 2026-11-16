# Archive shortlist source-fidelity review — 2026-10-30T05:01Z

## Scope and controls
- Source inventory first: reviewed the archive shortlist and its priority rubric before evaluating any Warden candidate.
- Verified Warden worktree integrity and governance state `READY_FOR_DISTINCT_RESEARCH`.
- No Warden registration, screen, backtest, contract activation, holdout access, or publisher-code execution occurred.

## Reviewed distinct leads

### 1. BTA NASDAQ & S&P Ratio Breakout — BLOCKED_RULE_FIDELITY
- Original URL: https://breakouttradingacademy.com/nasdaq-sp-ratio-breakout-strategy-money-machine/
- Published: 2024-02-29T04:00:24Z; captured: 2026-10-30T04:22:05.507341Z.
- Archive current-capture SHA-256: `4db4a22ba9b67748b6bdeb961c7a35fb9f771702e509aadc8225520e8e5c0d6a`.
- Author rule: ES and NQ futures, 30-minute 24-hour bars; RTH entry window roughly 09:30–15:00 Chicago; EOD exit; fixed $1,000 futures stop; no filters. The source describes space as an absolute difference but its later formula omits the absolute-value operator.
- Author parameter search: N=20..35 step 1 and ratio=2.0..3.0 step 0.1.
- Mechanism: index momentum breakout conditioned on compression between short- and longer-window highs.
- Blocking gaps: [MISSING] unambiguous sign/absolute-value convention; CFD-equivalent fixed-stop translation; exact same-bar stop/entry priority; PIT CFD session/roll construction. A silent NQ/ES futures-to-CFD substitution is prohibited.

### 2. BTA Gold Aftermarket Breakout — BLOCKED_AUTHOR_SELECTION_AND_CONTRACT
- Original URL: https://breakouttradingacademy.com/my-secret-gold-breakout-strategy/
- Published: 2025-12-11T17:36:10Z; captured: 2026-10-30T04:22:04.775279Z.
- Archive current-capture SHA-256: `0c2c3f93e30f11320688beb2a472892ff4c06ef25a36e6b2b309e67d04c4dfc7`.
- Author final rule: long-only gold futures; Monday/Thursday; 16:05–23:50 Chicago; exit 23:50; MA(20) point of initiation; ATR(14) breakout distance with 2.5 multiplier.
- Author-side search: selected session, weekday, initiation point, and space multiplier after exploration; reports 2.25 and 2.75 neighbours.
- Mechanism: thinner aftermarket participation following US-session liquidity decline.
- Blocking gaps: [MISSING] contract-roll and CFD spread mapping, protective-stop rule, DST/calendar treatment, and an approved metals parent contract. The source is published after any earlier validation period and cannot claim historical OOS status.

### 3. QuantConnect Dual Thrust — BLOCKED_INSTRUMENT_ADAPTATION
- Original URL: https://www.quantconnect.com/research/15258/dual-thrust-trading-algorithm/
- Current capture source type: original publisher page; source date not established by the static page.
- Author setup: SPY equity at hourly resolution; range derived from prior daily high/low/close and current-day open; article labels the research pending review/draft.
- Mechanism: session-open range breakout.
- Blocking gaps: [MISSING] full fixed parameter set in the captured page and a source-faithful CFD translation. SPY equity cannot silently become US100/US500 CFD.

### 4. Algomatic Gold Stochastic Extremes — BLOCKED_INCOMPLETE_PARAMETERS
- Original URL: https://www.algomatictrading.com/post/a-mean-reversion-gold-system-using-stochastic-extremes
- Published: 2025-08-24T19:07:55.716Z; captured: 2026-10-30T04:22:12.913216Z.
- Archive current-capture SHA-256: `ae5d913525f815b0bbeb24237c80487573e3a21eb79d98f2c30231edb4d87d64`.
- Author description: Gold CFD/futures, 2-hour bars, stochastic crossover plus trend filter; avoids low-liquidity hours; ATR sizing; fixed-percent and ATR-trailing stops.
- Blocking gaps: [MISSING] stochastic periods/thresholds, trend filter, liquidity hours, ATR windows/multipliers, fixed stop percentage, and entry/exit precedence. The post’s claimed PF is not evidence for Warden.

### 5. Financial Hacker Market Meanness Index — NOT_A_STANDALONE_TRADE_RULE
- Original URL: https://financial-hacker.com/the-market-meanness-index/
- Source defines a rolling median-based regime statistic and positions it as a trend-system filter.
- Blocking gaps: no entry, exit, instrument, execution horizon, or standalone counterparty/friction. It may be assessed only as a predeclared filter for an independently complete source strategy.

## Decision
No hypothesis was created. These five leads are not interchangeable variants; each has its own source-fidelity/data blocker. The next screening candidate must have complete executable rules, a source date compatible with the validation split, and an approved matching Warden contract before any registry mutation.
