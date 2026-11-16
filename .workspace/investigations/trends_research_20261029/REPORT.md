# trends-research (Lucas Joly, MSc) — investigation

Clone: `data/external/trends-research` @ 0426bac, 559 files, ~30k LOC Python + C++.
Read-only showcase: the NorgateData futures panel, `Data/`, and `shared_config.py`
(the primitives every alpha imports) are **absent** — nothing here runs end to end.

## Verdict

The **execution layer is honest** and the **statistical layer is partly decorative**.
A port into quant-lab was not worth a trial budget before it was run: the dominant sleeve
(EWMAC trend, 25% of the composite plus the TS/XS split) is **already falsified at
Stage 1 on our own panel**, and the DSR hurdle on that contract is netSR ≥ 1.79.
What is worth taking is the *test harness*, not the signals.

## 1. What the claims rest on

| Claim | Value | Basis |
|---|---|---|
| Raw annualised Sharpe (excess T-bill) | 0.97 | 62 frozen futures, 1990–2026, 9,341 days |
| Harvey–Liu BHY-corrected | 0.78 | K = 118 "audited trials" |
| Deflated Sharpe | z = 4.28 | Bailey–López de Prado |
| "Zero parameters optimised on the backtest" | — | all params derived or cited |
| Paper trading Jan–May 2026 | Sharpe +0.44, vol 8.56%, DD −4.33% | 145 days, out-of-sample |

Composite = equal-weight 4 sleeves (trend 25% = TS+XS over 3 EWMAC speeds 16/64,
32/128, 64/256; carry 25%; skew 25%; vol-of-vol 25%), Carver forecast→position,
universe-pooled FDM capped at √4, sigmoid vol and drawdown overlays.

## 2. Independent check of the published track

I recomputed the equity series from `docs/data/pnl/*.json` (`recompute_track.py`).
Their own `latest.json` meta: net Sharpe **1.7427**, ann 15.08%, vol 8.65%, maxDD
−5.60% over 213 sessions, $100M → $116.5M. My arithmetic-mean Sharpe is 2.15; the
gap is convention (they divide annualised return by annualised vol, I use
mean/sd·√252). **Their number reproduces.**

Two claims do not hold:

| Check | README says | Repo data says |
|---|---|---|
| Jan 2 – May 26 2026 window | Sharpe +0.44, vol 8.56%, DD −4.33% (145 "days" = calendar days) | **Sharpe 3.36**, vol 7.40%, DD −1.87% (103 sessions) |
| Publication provenance | "genuinely out-of-sample … did not exist when the model was frozen" | Repo inception **2026-08-30** (`c199db6`, 115 commits). That first commit adds **171 day-files covering 2026-01-02 → 2026-08-28 in one go**; the remaining **42 sessions** (Aug 31 – Oct 27) were added one per session |

The README's paper-trading table cannot be reproduced from the repo's own published
series — off by an order of magnitude on Sharpe for the window it names.

Provenance, stated carefully: git history establishes only *when a day-file entered the
repository*. The pre-inception 171 sessions are a bulk backfill of a window that already
existed at publication time, so they cannot demonstrate a forward record. The 42
post-inception sessions are consistent with one but do not prove it — a commit dated after
the session is equally consistent with a next-morning simulation. **Execution provenance
is UNVERIFIED** in both directions; there is no evidence the series was retro-fitted and
none that orders were placed. What can be said: Sharpe 1.56 over the backfilled span and
4.18 over the 42 post-inception sessions sit 0.5 SE and 1.3 SE from the 36-year 0.97
(SE = √(252/n)), so neither is evidence of anything.

## 3. Code audit

### 3.1 Execution: clean (the strongest part of the repo)

- Decision at `close_t`, fill at `open_{t+1}`: old leg earns `close_t→open_{t+1}`,
  new leg `open_{t+1}→close_{t+1}` (`engine/fast_backtest.cpp:267-271`, mirrored in
  `ig_shared_config.py:594-604`). No same-bar P&L.
- Sizing uses NAV at `close_t`, point-in-time instrument count for IDM, rolling
  blended vol, buffered integer contracts. No full-sample statistic in the sizing
  chain.
- Panama-differenced P&L correctly excludes the roll gap (roll *costs* are never
  charged, ≈0.01 Sharpe).
- "98.1% retained under T+1" is measured by shifting forecasts one *extra* day on
  top of the already-lagged fill — a T+2 test, conservative, not a defence.

### 3.2 Statistics: partly theatre

| Claim | Reality | Severity |
|---|---|---|
| DSR z = 4.28 | Fed `daily_ret`, the **total** return incl. T-bill carry. On the 0.97 excess series the code's own formula gives **z = 3.28** (K=118) / **3.14** (K=177) | HIGH |
| K = 118 trials | Hardcoded literal; the same file's registry is 106 DM + 70 ablation + 1 = **177**; neither counts the **44-signal library search** or the 8-generation lineage | HIGH |
| BHY 0.78 | Correctly implemented and reproducible — but insensitive to K (0.88 at K=10, 0.76 at K=177). With t = 5.88 on 9,341 days it cannot bind | MED |
| "MC: realised SR in top 1.8%" | That percentile is the test's **own failure condition** ("would not survive any small perturbation"). README cites it as a credential | HIGH |
| "signal shuffle 24–33σ" | Shipped artifact: **23.2σ**, and the null is centred at **−2.44 SR** because shuffling maximises turnover cost. It measures distance from a cost-dominated null, not from zero skill | MED |
| "WGAN re-runs the strategy on synthetic paths" | It back-solves `exposure = pnl / price_diff` and replays it. The signal→position map is never exercised; the WGAN is trained on the full 1990–2026 panel, not pre-2026 | HIGH |
| "strict 5-year per-instrument OOS gate, no survivorship bias" | It is a 1,280-bar *warm-up*, point-in-time. The universe is a frozen **current** mapping: SR3/SO3 (2018), BTC (2017), ETH (2021) in; the Eurodollar strip out | HIGH |
| "zero parameters optimised on the backtest" | Contradicted in the code's own comments: `smooth_span=3` "ablation-selected optimum", `sigmoid_steepness=10.0` "ablation-measured optimum", 3-speed triple retained on dSR | MED |
| Costs | Commission+spread only, static 2026 table on 1990–2026, no impact in the P&L: 0.4–0.8%/yr ≈ **0.05 Sharpe**. 0.97 is effectively gross | MED |

Honest range after universe + multiplicity: **~0.80–0.90**, with 0.78 a reasonable
floor. The headline is not inflated by leakage; it is inflated by *selection*.

Also note: the library itself is 44 signals, 30 scored, median SR +0.14, mean +0.00,
best +0.57; the top 5 are one trend cluster correlating up to **0.97**. An
equal-weight 4-leg composite using the library's own correlation matrix implies
**≈0.77**, not 0.97 — the gap is the overlays, i.e. exactly the part that is fitted.

## 4. Portability to our lake

34 of 44 alphas are pure functions of daily OHLCV (7 need a multi-instrument panel,
3 need volume, 3 need daily O/H/L/C). 5 need futures **term structure**, 5 need
COT/OI/macro. None need 1h data.

| S183 leg | Implementable here | Why |
|---|---|---|
| Trend (TS+XS, 3 EWMAC speeds) | **yes** | pure price; `strategies/candidates/carver_daily/s09_ewmac_multi.py` is the same family |
| Skew | **yes** | −rolling 256d skew of returns |
| Vol-of-vol | **yes** | vol of realised vol, price-only |
| **Carry** | **no** | needs front/next contract. The lake has **no futures term structure** (no multi-contract curves, no OI), only single continuous series. 5 of 44 alphas blocked by this |

Our panel: `MULTI_ASSET_1D_V1`, 17 instruments (6 indices, 8 FX, 2 metals, CL),
2015-01 → 2025-06, ~3.1k daily bars each. Theirs: 62 instruments over 36 years.
That is a 10× smaller cross-section and a quarter of the history.

## 5. Prior lab evidence already covers the dominant sleeve

The trend sleeve is not untested here. Stage-1 screens on `MULTI_ASSET_1D_V1`
(`selection_attempts`, kind `standalone_screen`):

| Hypothesis | Module | Outcome | Profit factor | Conditional edge |
|---|---|---|---|---|
| HYP-2026-103 | `s09_ewmac_multi` (**the S183 trend core**) | **REJECT** | 1.020 | 0.45 bps/bar |
| HYP-2026-108 | `s19_xs_momentum` (XS trend) | REJECT | 1.009 | 0.45 bps/bar |
| HYP-2026-104 | `s12_adjusted_trend` | REJECT | 1.030 | 0.62 bps/bar |
| HYP-2026-099 | `s05_slow_trend_long_only` | PASS | 1.154 | 1.70 bps/bar |
| — | `s05` at Stage 3 (RUN-HYP099-001) | REVISE | netSR 0.879, DSR 0.314 | — |

The multi-speed EWMAC ensemble — 25% of S183 by construction and the source of
almost all of its standalone Sharpe — is **already rejected at the signal gate** on
the panel a port would use. Only long-only slow trend survives Stage 1, and it
cannot clear Stage 3.

## 6. What a port would have to clear

Warden's gate (`src/warden/registry.py:69-113`) with the recorded family state
(`contract_search_state`), n ≈ 2,700 daily bars, skew −0.2, kurt 3.5:

| Contract | k_eff gate | noise (ann SR) | required netSR (DSR 0.90 / 0.95) |
|---|---|---|---|
| `MULTI_ASSET_1D_V1` | **45.61** | 1.394 | **1.79 / 1.91** |
| `MULTI_ASSET_1H_V1` | 22.50 | 1.224 | 1.39 / 1.44 |
| `FX_MAJORS_1D_V2` | 1.62 | 0.195 | 0.59 / 0.70 |
| `INDICES_1D_V1` | 1.13 | 0.000 | 0.39 / 0.50 |

`MULTI_ASSET_1D_V1` is the only contract with the breadth for a 17-instrument
cross-asset book, and its family floor is 45.6 because of 10 prior trials — the
best of which (netSR 1.317, a long-only buy-and-hold) still scored DSR 0.663.
A trend composite would need netSR ≥ 1.79 to qualify, against a Stage-1-rejected
trend sleeve. `INDICES_1D_V1` and `FX_MAJORS_1D_V2` have low hurdles but:
indices_1d's holdout was read three times (HYP-112/113/114, all rejected), and FX
trend was already run to netSR 0.532–0.656 with audit check 2 failing.

## 7. Decision

**Reproduction attempted and rejected — see `REPRODUCTION.md`.**

The testable core (trend + skew + vol-of-vol, carry unbuildable) was ported to
`strategies/candidates/s183_composite.py` and pre-registered as `HYP-2026-126` on
`MULTI_ASSET_1D_V1`, then run through the harness:

- `warden screen` → **REJECT**, profit factor **1.1119** (gate 1.15), conditional edge
  **1.2 bps/bar**, bootstrap p **0.12** (gate < 0.05). Recorded as
  `ATT-d107ad4f9fcac118-002`, `STAGE_1_SIGNAL`. Zero trials consumed (0/3).
  The first attempt (`-001`) ran on a pipeline with ungrouped windows (cross-asset
  contamination) and is superseded; after fixing the grouping, `verify_prefix_invariance`
  and `verify_asset_independence` both pass and the metrics move in the fourth decimal.
- That module was a **reconstruction**, not a port: the repo ships its reference
  implementation in `Live/2_Engine/trading_book.py` and `Live/3_Portfolio/portfolio.py`.
  `HYP-2026-127` (`s183_reference.py`) calls those functions directly and screens at
  **profit factor 0.9276, edge −1.6 bps/bar** (`ATT-453bfc8cee3e3c7c-002`). Same verdict,
  opposite sign. No isolated cause is established: the builds differ in six components at
  once and no ablation was run. `s183_composite.py` was rewritten in place and restored
  functionally, so it is not the file behind `-001/-002`; no strategy-code hash was recorded
  for those screens and the original bytes are unrecoverable.
- The same signals through `ml4t.backtest`'s event-driven engine (an unregistered
  diagnostic, disclosed; corrected to long/short): net Sharpe **0.39–0.45**, vol 5.2–7.6%,
  maxDD −8.7/−13.1%,
  PSR 0.992–0.995 — and **DSR 0.000** against this contract's search floor (k_eff 45.61,
  expected max Sharpe 1.394 under the null). `ml4t-diagnostic` puts the trend sleeve at
  21-bar IC 0.037 — naive t 5.83, but HAC t 1.76 (p 0.08) once the 21-bar label overlap
  is priced in — with IC_IR 0.11, monotonicity 0.30 and 59% turnover. Only the 1-bar
  horizon is nominally significant (HAC t 2.13, p 0.03) before multiplicity.

So the published Sharpe is roughly reproducible in *magnitude* on a much narrower panel,
while the statistical and economic claims are not: the Sharpe is above zero at single-test
confidence (PSR 0.99) but scores DSR 0.000 against the contract's search floor, and the
edge per bar (−1.6 bps for the reference port) does not clear cost. Which input binds is
not established: the port omits carry, the drawdown gate, integer sizing and roll costs, and
the panel is 17 instruments against 62 — breadth is one candidate, untested against the rest.

Stage 3 was run twice on explicit request, both **REVISE**:

| run | config | gross SR | net SR | 2× | +1 bar | DSR | audit |
|---|---|---|---|---|---|---|---|
| `RUN-RU-001` | source buffer 0.10 | −0.353 | −0.455 | −0.557 | −0.486 | 0.0011 | 3/6 |
| `RUN-RU-002` | buffer 0.50 (deviation) | −0.234 | −0.300 | −0.367 | −0.438 | 0.0021 | 3/6 |

Two of three trials spent; the contract's k_eff gate rose 45.61 → 56.11, which now needs
netSR **2.27** for DSR 0.90. Both audits reject on the same three checks (outlier
sensitivity, cost/latency survival, selection accounting) and pass the other three;
causality passes over 5 cut points in both.

**Why no pass is reachable from here.** The reproduction is faithful to the data it has —
assembly, IDM, FDM and the sizing identity are differentially verified against the upstream
code — but three inputs the original used are absent or unmodelled: carry (no futures term
structure in the lake), integer contract sizing, and roll costs. At the contract's mandated
one-bar delay every measured configuration is negative; with the delay removed the same
matrix scores +0.310, so the result is latency-conditional rather than evidence of no edge.

**What I did not do.** I did not edit `gates.py`, the contracts, or the trial ledger to turn
a REVISE into a candidate. A pass produced that way would be indistinguishable from a real
one in every downstream report, which is the failure mode the whole harness exists to prevent.

**Do not register a further S183 port.** A fourth trend-flavoured hypothesis on the same
contract pays the same DSR floor (netSR 1.79 for DSR 0.90) for a mechanism already
rejected twice (`HYP-2026-103` EWMAC ensemble, `HYP-2026-126` composite).


Adopt the **harness ideas** instead — these are gaps in our lab, not theirs:

1. **FDM pooling.** Universe-pooled forecast diversification, EWM(512) on the
   correlation of mean forecasts, capped at √N. We have IDM; FDM was recorded as a
   design gap (`AF30` allocator uses forecast sign only).
2. **Neutral-condition overlay arithmetic.** Their sigmoid gates give
   `0.75 × 0.866 = 0.65` at *neutral* conditions — a permanent 35% risk cut that
   explains 12.9% realised vol against a 20% target. Any overlay we ship should
   report its neutral multiplier.
3. **Forecast-shuffle null.** Permute per-instrument forecasts (not returns) and
   re-run. Cheap, and it detects a signal→P&L wiring error that a return-shuffle
   cannot. Must report the null *mean*, not just the σ.
4. **T+2 delay test.** Our contract injects +1 bar; a second lag is a stronger
   sensitivity check and costs one extra run.
5. **Cost-insensitivity check.** Report Sharpe at 1x/2x/5x costs and state the
   breakeven; a strategy whose Sharpe barely moves is gross, not robust.

If a trial is ever spent on the untested sleeves, it should be **skew + vol-of-vol
as portfolio-construction overlays on the existing `carver_daily` book**, on
`MULTI_ASSET_1D_V1`, pre-registered with the 1.79 hurdle stated up front — and with
the honest expectation that it will not clear it.

## 8. Reproduction

```bash
git clone https://github.com/Lucas-Joly-GH/trends-research data/external/trends-research
uv run python .workspace/investigations/trends_research_20261029/recompute_track.py

# publication provenance (must be a full clone: --depth makes the graft boundary
# look like the initial commit and every file look newly added)
cd data/external/trends-research
git rev-list --count HEAD                                   # 115
git log --format='%ad %h %s' --date=short | tail -1          # 2026-08-30 c199db6 .
git show --name-only --format='' c199db6 | grep -c 'docs/data/pnl/'   # 171
git log --diff-filter=A --name-only --format='' -- docs/data/pnl/ | grep -c 'pnl/2026'  # 213
```

Audit detail (file:line for every row in §3) is in the three scout reports:
`agent://S183ExecAudit/report`, `agent://S183StatsAudit/report`,
`agent://AlphaPortability/report`.
