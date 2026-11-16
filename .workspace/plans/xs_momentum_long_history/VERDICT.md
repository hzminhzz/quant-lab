# Verdict — weekly cross-sectional momentum on crypto perps

Date: 2026-10-28. Hypothesis: HYP-2026-125 (0/3 trials spent, 1 screen attempt).
Question: does "long last week's biggest winners, short last week's biggest losers, rebalance weekly"
survive the contract's own gates on the best data we can obtain?

**Answer: no. It fails Stage 1 on the cross-section, fails the audit's outlier check catastrophically,
and what positive Sharpe it shows is carried by a handful of hours, not by a diversified edge.**

Nothing below was registered as a trial; every number comes from disclosed unregistered diagnostics
over the broad panel. That means the *selection count* is larger than the registry knows, which makes
these results optimistic, not conservative.

## Data

| item | value |
|---|---|
| panel | `data/exploratory/crypto_perps_broad_1h.parquet` |
| sha256 | `19a225c9fde6dc933b142fc4fc67f9a1de5ac0473adac1f44cc4e2f71866a268` |
| symbols | 863 (510 trade inside the window; 30 delist inside it) |
| span | 2021-01-01 → 2025-06-30, 39,408 hourly bars |
| funding | observed for 530 symbols; the other 333 charged the declared 1.0 bps/8h |
| validation | 2021-07-01 → 2025-07-01, **208 weekly rebalances** |
| universe | point-in-time top-25 by trailing 168-bar quote volume |
| costs | 4 bps taker + 2 bps slippage per side, funding per contract, NEXT_BAR |

## Formation ladder, 208 weekly rebalances

| formation | Pearson IC | HAC t | Rank IC | HAC t | gross SR | net 1x | net 2x | max DD | turn/yr | DSR family | DSR clean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1w (the source's rule) | -0.0076 | -0.32 | -0.0252 | -1.22 | +0.923 | **+1.044** | +0.928 | 46.2% | 86x | 0.210 | 0.982 |
| 2w | -0.0220 | -1.00 | -0.0360 | -2.14 | +0.788 | +0.973 | +0.881 | 33.9% | 66x | 0.172 | 0.974 |
| 2w skip 1w | -0.0251 | -1.35 | -0.0307 | -2.10 | -0.599 | -0.524 | -0.612 | 86.1% | 67x | 0.000 | 0.155 |
| 4w | -0.0040 | -0.19 | -0.0284 | -1.92 | +1.081 | **+1.258** | +1.183 | 44.8% | 52x | 0.353 | 0.994 |
| 4w skip 1w | -0.0210 | -1.27 | -0.0335 | -2.17 | -0.520 | -0.381 | -0.466 | 78.8% | 55x | 0.000 | 0.223 |
| 8w | -0.0212 | -1.02 | -0.0459 | -2.43 | +0.519 | +0.703 | +0.636 | 64.7% | 44x | 0.068 | 0.920 |
| 12w | -0.0126 | -0.65 | -0.0435 | -2.50 | +0.110 | +0.244 | +0.186 | 64.0% | 42x | 0.007 | 0.691 |

Two things to read off it:

1. **The cross-section is flat to negative at every formation.** No formation clears Stage 1
   (mean IC ≥ 0.015 **and** HAC t ≥ 2.0); the rank IC is significantly *negative* at 1w/2w/4w/8w/12w.
2. **The skip-week variant, which won on the narrow panel, loses badly here** (-0.52 to -0.38). The
   sign of the skip effect flips between panels and periods — the opposite of a stable mechanism.

## Why the positive Sharpe is not an edge

| 1w spec | value |
|---|---|
| mean long-minus-short forward return | +2.876% per week |
| share of weeks that are positive | 51% |
| corr(weekly IC, weekly long-short return) | +0.464 |
| total net PnL over 208 weeks | +1.86 units of NAV |
| contribution of the best 5% of hourly bars | +19.98 (1074% of total) |
| contribution of the worst 5% of hourly bars | -18.69 |
| **net SR after deleting the best 5% of bars** | **-13.09** |

The book is roughly symmetric in the tails: +20 from the best 5% of hours, -19 from the worst 5%. The
net drift is the small residual. That is a lottery profile, not a momentum edge, and it is the exact
failure the six-check audit's second check exists to catch. The 4w variant is the same story
(-13.17 after the trim, 909% of PnL from the best 5% of bars).

Half-year breakdown (net SR, 1w / 4w):

| period | 1w | 4w |
|---|---|---|
| 2021H2 | +1.48 | +4.08 |
| 2022H1 | +0.08 | -0.42 |
| 2022H2 | +1.26 | -0.50 |
| 2023H1 | -1.24 | -0.60 |
| 2023H2 | +2.21 | +2.13 |
| 2024H1 | -0.76 | +1.52 |
| 2024H2 | +1.94 | +2.23 |
| 2025H1 | +2.62 | +0.36 |

Four negative half-years out of eight for both. The 4w "winner" owes its headline to 2021H2
(+164% in six months, the meme-coin dispersion year) and has been flat-to-negative in half the sample
since.

## Selection accounting

Across this session the momentum family has been examined at **~30 configurations**: 8 formations x 2
windows x 3 liquidity tiers on the narrow panel, then 9 formations on the broad panel. Warden's
`k_eff` sees none of that. At the honest count (k = 30) the DSR is **0.210 (1w) / 0.353 (4w)** — the
same as at the inherited family floor, because the noise floor dominates either way. Only a
single-trial accounting (k = 1, no noise) reaches 0.98, and that number is not available to us.

## Decision

**Park the lineage.** HYP-2026-125 keeps 0/3 trials and one recorded screen attempt; no trial was
spent chasing a result that fails the cross-section, the outlier check and the selection accounting.
Post-mortem recorded in `.workspace/memory/LESSONS.md`.

## What would change the verdict

1. **Venue, not formation.** The one remaining untested explanation is that the source's edge is
   Hyperliquid-specific: a smaller book, more retail taker flow, thinner depth, and a listing set that
   our Binance panel cannot represent. Testing it needs HL history: the lake has only recorder-era
   second/tick/L2 for ~15 symbols, and 0xArchive sells HL minute data from 2025-03, which lands inside
   our sealed holdout window. Both paths are the principal's call, not an agent's.
2. **A tail-robustness gate at Stage 1.** A strategy whose PnL is 1000% concentrated in the best 5% of
   bars should be rejected before Stage 3, not after. Adding "net SR after trimming the best 5% > 0"
   to the Stage-1 branch would have killed this family in one screen instead of thirty runs.
3. **Accept the profile and size for it** — if the tails are the edge, the honest form is an
   option-like book with explicit sizing against a 45% drawdown, not a Sharpe-ranked strategy. That is
   a different hypothesis with a different mechanism, and it would need its own trials.

## Robustness cuts (2026-10-28, after the first draft)

Three checks that could have overturned the verdict. None did.

**A. Independent construction.** Re-computed the long-short return straight from ranks at each
rebalance — no simulator, no carried positions, equal weight, forward open-to-open:

| formation | n | mean | median | hit rate | mean after dropping the best 5% of weeks |
|---|---|---|---|---|---|
| 1w | 222 | +2.80%/wk | +0.14% | 0.51 | **-0.76%** |
| 4w | 222 | +2.37%/wk | +0.33% | 0.50 | **+0.14%** |

The simulation and the independent calculation agree to 3 basis points on the mean, so the earlier
number is not a book-builder artefact. What the median says is the real story: a coin flip with fat
tails.

**B. Dropping 2021.** Removing the meme-coin dispersion year:

| formation | net SR full | net SR 2022+ | net SR after trimming the best 5% of bars |
|---|---|---|---|
| 1w | +1.044 | +0.976 | -13.13 |
| 4w | +1.258 | **+0.754** | -13.42 |

The 1w result is not a 2021 artifact; the 4w result halves without it (weekly long-short mean 2.21% →
1.05%, hit rate 0.50 → 0.48).

**C. Liquidity tier** (weekly long-short mean, 2021-07..2025-07):

| universe | 1w | 4w |
|---|---|---|
| top-10 by dollar volume | **+0.05%**/wk (hit 0.47) | +1.27%/wk (trim -0.29) |
| top-25 | +2.80%/wk (trim -0.76) | +2.37%/wk (trim +0.14) |
| top-50 | +1.67%/wk (trim -0.89) | +2.01%/wk (trim -0.25) |

Among the ten most liquid perps there is **no** weekly momentum at all (+0.05%/wk, sub-coin-flip hit
rate). The effect appears in the semi-liquid middle, which is also the population where a 6 bps
round-trip assumption is most optimistic. A strategy that only works 25 names deep, with a 45%
drawdown and a median week of zero, is not the 2.43-Sharpe object in the claim.

**Conclusion unchanged: park it.** The three cuts that could have rescued the thesis — an
implementation error, a single-regime dependence, a liquidity premium — all come back negative or
inconclusive.

## Does the long leg just buy pump-and-dumps?

The principal's hypothesis: Binance lists far more tokens with institutional market-maker volume, so the
long leg ends up holding pumped junk that reverses. Tested directly — the share of long-leg slots whose
trailing formation return exceeded +50%, and what those names did next:

| formation | long-leg slots with >+50% trailing | forward mean, pumped names | forward mean, non-pumped winners |
|---|---|---|---|
| 1w | 26% | **+5.15%** | **-1.22%** |
| 4w | 57% | **+2.12%** | **-0.25%** |

**At 1w the intuition is backwards: the pumps are the entire edge.** Names that already ran >50% in the
week keep running (+5.15% mean next week) while the merely-above-average winners bleed (-1.22%). Remove
the pumps and the mean collapses from +2.89%/wk to +0.12%/wk. The rule is, mechanically, a
pump-continuation strategy — which is exactly why it looks like a lottery: it is short-volatility on
meme-coin mania.

**At 4w the intuition is partly right, but only at the extreme.** Excluding names that ran >+100% over
the four-week formation window (not >+50%):

| spec | net SR | 2x cost | max DD | turn | median week | DSR @ k=30 |
|---|---|---|---|---|---|---|
| 4w baseline | +1.258 | +1.183 | 44.8% | 52x | +0.15% | 0.353 |
| **4w, exclude >+100%/4w** | **+1.409** | **+1.310** | **33.0%** | 58x | **+1.42%** | 0.471 |
| 4w, exclude >+150%/4w | +1.172 | +1.080 | 30.5% | 56x | +1.26% | 0.292 |
| 4w exclude >+100%, 2022-01 onward | **+1.508** | +1.411 | 33.0% | 57x | — | — |

This is the best-behaved configuration in the whole study: it improves when 2021 is removed (+1.409 →
+1.508, so it is not a meme-year artifact), it cuts drawdown by twelve points, it survives 2x costs, and
its median week turns positive for the first time. Dropping the pump filter's threshold to +50% destroys
it (+0.12%/wk at 1w), so the effect is specifically "the 4-week window's most extreme winners revert",
not "all strong winners revert".

It still fails the contract: DSR 0.471 against the 0.90 gate, and the top-5%-of-bars trim remains
negative (-12.79), so audit check 2 would reject it. The residual PnL is still concentrated in a few
hours even after the extreme names are removed.

**What this changes.** The family is not a null result — it is a tail-concentrated pump-continuation
effect that improves when you exclude the most extreme 4-week winners. That is a more useful answer than
"no edge", and it is the strongest reason to believe the Hyperliquid version could be real in a different
form: a smaller venue with fewer listings would give the same rule less junk to sort through.

## Should the contract move to Hyperliquid? (decision, 2026-10-29)

**No — not for qualifying this hypothesis.** The binding constraint is the number of independent weekly
bets, and Hyperliquid has *less* history than Binance, not more.

What we actually own on HL:

| family | stores | span |
|---|---|---|
| `crypto_futures_second_HYPERLIQUID_*_PERP` | 9 | 2026-01-15 → 2026-09-11 |
| `crypto_futures_l2_HYPERLIQUID_*_PERP` | 9 | 2026-01-15 → 2026-09-10 |
| `crypto_futures_tick_HL_*_PERP` | 6 | 2026-01-01 → 2026-02-28 |
| `crypto_futures_ohlcv_1m_HYPERLIQUID_XYZ_PERP` | 1 | 2026-04-14 → 2026-10-08 (a synthetic index, not a coin) |

Nine symbols over eight months cannot host a point-in-time top-25 universe, and there is no pre-2026 HL
history at all. The best purchasable alternative (0xArchive minute candles from 2025-03-22, Build $49/mo
plus export fees) yields at most **~82 weekly rebalances**, and its window sits inside our sealed crypto
holdout (2025-07-01 → 2026-10-26).

Power arithmetic, using the best Binance configuration (4w, exclude >+100%) truncated to each sample
size — i.e. the *optimistic* assumption that the venue switch changes nothing:

| sample | weeks | net SR | DSR @ k=1 | DSR @ k=30 (honest) |
|---|---|---|---|---|
| our Binance panel | 208 | +1.42 | 0.998 | **0.478** |
| HL from 2025-03 (purchased) | 82 | +2.50 | 0.999 | **0.819** |
| the claim's own window | 61 | +2.45 | 0.996 | **0.663** |

Truncating raises the Sharpe (the recent stretch is the good one) but the gate still is not met at any
honest selection count. Switching venue trades 208 weekly bets for 82 and buys a claim we could not
certify even if it reproduced exactly.

And the failure mode we measured — PnL concentrated in a few hours (top-5%-of-bars trim -12.8 even after
the pump filter) — is a property of how a pump-continuation rule earns, not obviously of Binance's
listing count. Whether a thinner venue softens it is a question for the HL data we already own, not for
a purchase.

### What Hyperliquid data is good for, in order

1. **Cost realism, free, today.** Nine names with L2 + tick from 2026-01 gives real HL spread, depth and
   funding versus our assumed 6 bps/side. That is the one input the Binance study cannot falsify, and it
   needs no purchase.
2. **A paired venue test of the mechanism.** HL's own public API serves historical candles
   (`candleSnapshot`, paginated) at no cost; fetched into `ml4t-data` (data lives in the lake, never in a
   consumer) it gives ~80 weeks overlapping Binance. Comparing *same-day* cross-sectional momentum
   coefficients across venues is a paired test with far more power than either series alone — and it
   answers "is momentum stronger where flow is retail?" without pretending to produce a tradeable Sharpe.
   That is a mechanism check, not a qualification.
3. **Only if both point the right way**, consider the paid minute archive — and only with the crypto
   holdout decision made explicitly first, because the paid window overlaps it.
