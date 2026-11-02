# Recovery registry reconciliation — 2026-11-02

Generated from the read-only **source-checkout** Warden registry; this inventory is a **triage register, not a finding that all entries were data-blocked**. The separately created worktree has an empty registry; do not run studies there without a WAL-consistent independent registry/database and pinned data. No protected holdouts were opened.

**Source:** `/home/quant/dev/quant-lab/registry/registry.db`; live Warden integrity passed 2026-11-02 04:04 UTC. **149 hypotheses, 75 historical trials.** Current status is workflow state, not scientific verdict. Counts in the `screens` column are selection-attempt records (including grid attempts), not necessarily distinct scientific screens. Existing selection and full-trial evidence remains immutable.

**Classification:** NO_ATTEMPT = lacks a recorded selection attempt or full trial, not necessarily a data failure; STAGE1_REJECT_OR_REVIEW = recorded rejection, do not rerun automatically; STAGE3_REVISE = full-trial negative/insufficient evidence; OTHER = inspect case details. Original data-blocking cause and required market observables remain **UNVERIFIED** until source-level per-hypothesis inspection. No category below authorizes a trial.

**Live reconciliation:** HYP-2026-096/097/098 now each have a Stage-3 REVISE trial. Their respective net Sharpes are 1.2901, 1.1653, 1.1651 and DSR probabilities 0.3588, 0.2763, 0.2632. They are not Stage-3 passes. Earlier 2026-11-01 audit counts (72 trials) are superseded for registry totals only. Source-checkout uncommitted work must be preserved.

## All registered identities

| Hypothesis | Contract | Workflow | Trials | Attempts | Attempt outcomes | Last trial | Triage | Strategy |
|---|---|---|---:|---:|---|---|---|---|
| HYP-2026-001 | CRYPTO_PERPS_1H_V1 | ACTIVE | 3 | 42 | ERROR | REVISE | STAGE3_REVISE | Intraday Volatility-Breakout Trend Continuation on Major Crypto Perps |
| HYP-2026-002 | CRYPTO_PERPS_1H_V1 | ACTIVE | 2 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Funding-Rate & Short-Horizon Reversal Model |
| HYP-2026-003 | INDICES_1H_V1 | REJECTED | 3 | 0 | - | REVISE | STAGE3_REVISE | Short-Horizon RSI Pullback in Rising EMA Trend with 10-Bar Rolling VWAP Discount |
| HYP-2026-003B | INDICES_1H_V2 | REJECTED | 3 | 0 | - | REVISE | STAGE3_REVISE | Short-Horizon RSI Pullback in Rising EMA Trend with 10-Bar Rolling VWAP Discount (10-Yr Du |
| HYP-2026-004 | CRYPTO_PERPS_1H_V1 | REJECTED | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Amihud Illiquidity Premium Basket on Major Crypto Perps |
| HYP-2026-005 | CRYPTO_PERPS_1H_V1 | REJECTED | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Betting Against Beta Factor Model on Crypto Perpetuals |
| HYP-2026-006 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | 90-Day High-Water Mark Anchoring Momentum on Crypto Perps |
| HYP-2026-007 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 2 | 2 | GRID | REVISE | STAGE3_REVISE | Post-Liquidation Cascade Mean Reversal on Crypto Perps |
| HYP-2026-008 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | London Opening Range Breakout (ORB) on Crypto Perps |
| HYP-2026-009 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 3 | 0 | - | REVISE | STAGE3_REVISE | Rolling 24-Hour VWAP Institutional Benchmark Pullback on Crypto Perps |
| HYP-2026-010 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Spot-Perpetual Funding Basis Dislocation Mean Reversal on Crypto Perps |
| HYP-2026-011 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | US Session Intraday Momentum Surge on Crypto Perps |
| HYP-2026-012 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Volatility-Managed Time-Series Momentum on Crypto Majors |
| HYP-2026-013 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Squeeze Volatility Compression Breakout on Crypto Majors |
| HYP-2026-014 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Volume-Anchored Post-Spike Drift on Crypto Majors |
| HYP-2026-015 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Weekend Liquidity Dislocation Reversal on Crypto Majors |
| HYP-2026-016 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Systematic Funding Beta and Deleveraging Risk Premium Basket |
| HYP-2026-017 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Funding Carry and Crowding Exhaustion Basket |
| HYP-2026-018 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Funding Convexity and Skewness Basket |
| HYP-2026-019 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Unanticipated Funding Rate Innovation Shock Basket |
| HYP-2026-020 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Funding Rate Kurtosis & Jump-Tail Basket on Crypto Perps |
| HYP-2026-021 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Funding Memory Persistence Basket on Crypto Perps |
| HYP-2026-022 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Funding Rate Velocity Dislocation Basket on Crypto Perps |
| HYP-2026-023 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional 90-Day High-Water Mark Anchoring Basket on Crypto Perps |
| HYP-2026-024 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Amihud Illiquidity Asymmetry Basket on Crypto Perps |
| HYP-2026-025 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Realized Bipower Jump Variation Basket on Crypto Perps |
| HYP-2026-026 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Capital Gain Overhang & Disposition Effect Basket on Crypto Perps |
| HYP-2026-027 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Corwin-Schultz Effective Spread Basket on Crypto Perps |
| HYP-2026-028 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Downside Correlation Asymmetry Basket on Crypto Perps |
| HYP-2026-029 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Extreme Left-Tail Beta Crash Risk Basket on Crypto Perps |
| HYP-2026-030 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Hill Tail Index Crash Risk Basket on Crypto Perps |
| HYP-2026-031 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Idiosyncratic Volatility Discount Basket on Crypto Perps |
| HYP-2026-032 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Amihud Illiquidity Innovation Shock Basket on Crypto Perps |
| HYP-2026-033 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Information Diffusion Delay and Lead-Lag Catch-Up Basket on Crypto Perps |
| HYP-2026-034 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Information Discreteness and Frog-in-the-Pan Momentum Basket on Crypto Per |
| HYP-2026-035 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | Cross-Sectional Kyle's Lambda Order Flow Price Impact Basket on Crypto Perps |
| HYP-2026-036 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Liquidity Beta and LCAPM Premium Basket on Crypto Perps |
| HYP-2026-037 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 2 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Maximum Return Salience Overpricing Basket |
| HYP-2026-038 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Momentum Life Cycle & Volume Turnover Exhaustion Basket |
| HYP-2026-039 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Pairwise Correlation Integration & Diversification Risk Premium Basket |
| HYP-2026-040 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Quadratic Market Beta Curvature Basket |
| HYP-2026-041 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Range Volatility Efficiency Basket |
| HYP-2026-042 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 1 | 0 | - | REVISE | STAGE3_REVISE | Cross-Sectional Realized Kurtosis & Jump-Tail Risk Premium Basket |
| HYP-2026-043 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Realized Quarticity & Volatility Estimation Uncertainty Risk Premium Baske |
| HYP-2026-044 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Realized Concordant Semibeta Asymmetry Basket |
| HYP-2026-045 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Realized Skewness & Lottery Overpricing Basket |
| HYP-2026-046 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Relative Downside Beta Risk Premium Basket |
| HYP-2026-047 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Return Synchronicity & Information Content Basket |
| HYP-2026-048 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Session Drift Divergence Basket |
| HYP-2026-049 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Signed Jump Variation & Asymmetric Jump Momentum Basket |
| HYP-2026-050 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | Cross-Sectional Trade Size Granularity & Institutional Stealth Trading Basket |
| HYP-2026-051 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Systematic Cokurtosis Tail Contagion Risk Premium Basket |
| HYP-2026-052 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Systematic Coskewness Risk Premium Basket |
| HYP-2026-053 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Systematic Jump Beta and Discontinuous Market Risk Premium Basket |
| HYP-2026-054 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | Cross-Sectional Taker Order Flow Imbalance and Aggressive Volume Persistence Basket |
| HYP-2026-055 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Lo-MacKinlay Variance Ratio and Multi-Scale Autocorrelation Persistence Ba |
| HYP-2026-056 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Volatility-of-Volatility Risk Premium Basket |
| HYP-2026-057 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 1 | 0 | - | REJECTED_EARLY | OTHER | Cross-Sectional Volatility Asymmetry and Realized Semivariance Basket |
| HYP-2026-058 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Volatility Feedback Asymmetry and Downward Risk Premium Basket |
| HYP-2026-059 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Volatility of Liquidity Risk Premium Basket on Crypto Perps |
| HYP-2026-060 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Return-Volume Rank Correlation Basket on Crypto Perps |
| HYP-2026-061 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Trading Volume Volatility and Activity Uncertainty Basket on Crypto Perps |
| HYP-2026-062 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | Cross-Sectional Volume-Synchronized Probability of Toxicity Basket on Crypto Perps |
| HYP-2026-063 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 2 | 2 | GRID | REVISE | STAGE3_REVISE | Cross-Sectional Intraday VWAP Tracking Error and Execution Risk Premium Basket on Crypto P |
| HYP-2026-064 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Cross-Sectional Candlestick Shadow Absorption and Wick Asymmetry Basket on Crypto Perps |
| HYP-2026-065 | INDICES_1H_V2 | PROMOTED | 3 | 5 | PASS,GRID | REVISE | STAGE3_REVISE | 10-Year Global Index 24h Rolling VWAP Trend Pullback |
| HYP-2026-066 | CRYPTO_MAJORS_1H_V1 | REJECTED_ON_HOLDOUT | 3 | 0 | - | CANDIDATE_FOR_QUALIFICATION | OTHER | Multi-Asset 24h Rolling VWAP Trend Pullback on Major Crypto Perpetuals |
| HYP-2026-067 | INDICES_1H_V2 | ACTIVE | 3 | 12 | REJECT,GRID | REVISE | STAGE3_REVISE | AF30 Strategy 9 Multi-Speed EWMAC Trend Following on Global Index Futures |
| HYP-2026-068 | INDICES_1H_V2 | ACTIVE | 3 | 10 | REJECT,GRID | FAILED | STAGE1_REJECT_OR_REVIEW | AF30 Strategy 21 Donchian Channel Breakout on Global Index Futures |
| HYP-2026-069 | INDICES_1H_V2 | PROMOTED | 1 | 3 | PASS,GRID | REVISE | STAGE3_REVISE | 10-Year Global Index 24h Rolling VWAP Trend Pullback with Entry-Time Volatility Sizing |
| HYP-2026-070 | CRYPTO_MAJORS_1H_V1 | ACTIVE | 1 | 5 | REJECT,GRID,ERROR | REVISE | STAGE3_REVISE | AF30 Strategy 21 Donchian Channel Breakout on Major Crypto Perpetuals |
| HYP-2026-071 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S01 Buy & Hold Single on 17-instrument multi-asset 1h panel |
| HYP-2026-072 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S02 Buy & Hold Risk Scaled on 17-instrument multi-asset 1h panel |
| HYP-2026-073 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S03 Buy & Hold Variable Risk on 17-instrument multi-asset 1h panel |
| HYP-2026-074 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S04 Buy & Hold Portfolio Scaling on 17-instrument multi-asset 1h panel |
| HYP-2026-075 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S05 Slow Trend Long Only on 17-instrument multi-asset 1h panel |
| HYP-2026-076 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S06 Slow Trend Long Short on 17-instrument multi-asset 1h panel |
| HYP-2026-077 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S07 Slow Trend Strength on 17-instrument multi-asset 1h panel |
| HYP-2026-078 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S08 Fast Trend Strength on 17-instrument multi-asset 1h panel |
| HYP-2026-079 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S09 Multi-Speed EWMAC on 17-instrument multi-asset 1h panel |
| HYP-2026-080 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S12 Adjusted Trend on 17-instrument multi-asset 1h panel |
| HYP-2026-081 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S13 as ported (trend + vol regime, no carry term) on 17-instrument multi-asset 1h pan |
| HYP-2026-082 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S14 Spot Trend on 17-instrument multi-asset 1h panel |
| HYP-2026-083 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S17 Normalised Trend on 17-instrument multi-asset 1h panel |
| HYP-2026-084 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S18 Trend Asset Classes on 17-instrument multi-asset 1h panel |
| HYP-2026-085 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S19 Cross-Sectional Momentum on 17-instrument multi-asset 1h panel |
| HYP-2026-086 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S21 Donchian Breakout 20 on 17-instrument multi-asset 1h panel |
| HYP-2026-087 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S22 Value Reversal on 17-instrument multi-asset 1h panel |
| HYP-2026-088 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S23 Acceleration on 17-instrument multi-asset 1h panel |
| HYP-2026-089 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S26 Fast Mean Reversion on 17-instrument multi-asset 1h panel |
| HYP-2026-090 | MULTI_ASSET_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S27 Safer Fast Mean Reversion on 17-instrument multi-asset 1h panel |
| HYP-2026-091 | MULTI_ASSET_1H_V1 | ACTIVE | 2 | 3 | GRID | REVISE | STAGE3_REVISE | AF30 handcrafted book of 20 instrument-neutral strategies on MULTI_ASSET_1H_V1 |
| HYP-2026-092 | MULTI_ASSET_1D_V1 | ACTIVE | 3 | 10 | GRID | REVISE | STAGE3_REVISE | AF30 Handcrafted Book of 20 Strategies on Daily Multi-Asset Panel |
| HYP-2026-093 | MULTI_ASSET_1D_V1 | ACTIVE | 2 | 0 | - | REVISE | STAGE3_REVISE | AF30 Strategy 21 Donchian Channel Breakout on Daily Multi-Asset Panel |
| HYP-2026-094 | MULTI_ASSET_1D_V1 | ACTIVE | 2 | 4 | GRID | REVISE | STAGE3_REVISE | Robert Carver AF30 Strategy 21 Breakout 80 on Daily Multi-Asset Panel |
| HYP-2026-095 | MULTI_ASSET_1D_V1 | ACTIVE | 2 | 1 | PASS | REVISE | STAGE3_REVISE | AF30 S01 Buy and Hold, Single Contract |
| HYP-2026-096 | MULTI_ASSET_1D_V1 | ACTIVE | 1 | 1 | PASS | REVISE | STAGE3_REVISE | AF30 S02 Buy and Hold with Risk Scaling |
| HYP-2026-097 | MULTI_ASSET_1D_V1 | ACTIVE | 1 | 1 | PASS | REVISE | STAGE3_REVISE | AF30 S03 Buy and Hold with Variable Risk Scaling |
| HYP-2026-098 | MULTI_ASSET_1D_V1 | ACTIVE | 1 | 1 | PASS | REVISE | STAGE3_REVISE | AF30 S04 Buy and Hold Portfolio with Scaling |
| HYP-2026-099 | MULTI_ASSET_1D_V1 | ACTIVE | 1 | 4 | PASS,GRID | REVISE | STAGE3_REVISE | AF30 S05 Slow Trend Following, Long Only |
| HYP-2026-100 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S06 Slow Trend Following, Long and Short |
| HYP-2026-101 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S07 Slow Trend with Trend Strength |
| HYP-2026-102 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S08 Fast Trend with Trend Strength |
| HYP-2026-103 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S09 Multiple EWMAC Trend Rules |
| HYP-2026-104 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S12 Adjusted Trend Forecasts |
| HYP-2026-105 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S14 Spot Trend |
| HYP-2026-106 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S17 Normalised Trend |
| HYP-2026-107 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S18 Trend Following Asset Classes |
| HYP-2026-108 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S19 Cross-Sectional Momentum |
| HYP-2026-109 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S21 Breakout 80 |
| HYP-2026-110 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S22 Value |
| HYP-2026-111 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | AF30 S23 Acceleration |
| HYP-2026-112 | INDICES_1D_V1 | REJECTED_ON_HOLDOUT | 3 | 2 | GRID | CANDIDATE_FOR_QUALIFICATION | OTHER | AF30 SBuy and Hold with Risk Scaling on Global Index Futures |
| HYP-2026-113 | INDICES_1D_V1 | REJECTED_ON_HOLDOUT | 3 | 2 | GRID | CANDIDATE_FOR_QUALIFICATION | OTHER | AF30 SBuy and Hold with Variable Risk Scaling on Global Index Futures |
| HYP-2026-114 | INDICES_1D_V1 | REJECTED_ON_HOLDOUT | 3 | 2 | GRID | CANDIDATE_FOR_QUALIFICATION | OTHER | AF30 SBuy and Hold Portfolio with Scaling on Global Index Futures |
| HYP-2026-115 | FX_MAJORS_1D_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | AF30 S01 Buy and Hold, Single Contract, on FX Majors |
| HYP-2026-116 | FX_MAJORS_1D_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | AF30 S02 Buy and Hold with Risk Scaling on FX Majors |
| HYP-2026-117 | FX_MAJORS_1D_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | AF30 S03 Buy and Hold with Variable Risk Scaling on FX Majors |
| HYP-2026-118 | FX_MAJORS_1D_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | AF30 S04 Buy and Hold Portfolio with Scaling on FX Majors |
| HYP-2026-119 | FX_MAJORS_1D_V1 | ACTIVE | 0 | 0 | - | NONE | NO_ATTEMPT | AF30 S05 Slow Trend Following, Long Only, on FX Majors |
| HYP-2026-120 | FX_MAJORS_1D_V2 | ACTIVE | 1 | 2 | GRID | REVISE | STAGE3_REVISE | AF30 S01 Buy and Hold, Single Contract, on FX Majors (Dukascopy daily, V2) |
| HYP-2026-121 | FX_MAJORS_1D_V2 | ACTIVE | 1 | 2 | GRID | REVISE | STAGE3_REVISE | AF30 S02 Buy and Hold with Risk Scaling on FX Majors (Dukascopy daily, V2) |
| HYP-2026-122 | FX_MAJORS_1D_V2 | ACTIVE | 1 | 2 | GRID | REVISE | STAGE3_REVISE | AF30 S03 Buy and Hold with Variable Risk Scaling on FX Majors (Dukascopy daily, V2) |
| HYP-2026-123 | FX_MAJORS_1D_V2 | ACTIVE | 1 | 4 | GRID | REVISE | STAGE3_REVISE | AF30 S04 Buy and Hold Portfolio with Scaling on FX Majors (Dukascopy daily, V2) |
| HYP-2026-124 | FX_MAJORS_1D_V2 | ACTIVE | 1 | 4 | GRID | REVISE | STAGE3_REVISE | AF30 S05 Slow Trend Following, Long Only, on FX Majors (Dukascopy daily, V2) |
| HYP-2026-125 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Weekly cross-sectional momentum on point-in-time liquid perps (long top fifth, short botto |
| HYP-2026-126 | MULTI_ASSET_1D_V1 | ACTIVE | 0 | 2 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | S183 four-sleeve composite, carry leg removed (trend + skew + vol-of-vol) on price-only mu |
| HYP-2026-127 | MULTI_ASSET_1D_V1 | ACTIVE | 2 | 2 | REJECT | REVISE | STAGE3_REVISE | S183 reference port - repo's own sleeve and portfolio code, carry sleeve absent |
| HYP-2026-128 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 2 | 1 | PASS | REVISE | STAGE3_REVISE | Residual reversal on liquid perps — short idiosyncratic winners, long idiosyncratic losers |
| HYP-2026-129 | FX_MAJORS_1D_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Five-session FX shock reversal after volatility-standardized moves |
| HYP-AIR-20261029-001 | CRYPTO_PERPS_1H_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance USDT perpetuals (8-asset frozen panel): 1h: HYP-AIR-20261029-001 |
| HYP-AIR-20261029-102247 | CRYPTO_PERPS_1H_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance eight USDT perpetuals: 1h: HYP-AIR-20261029-102247 |
| HYP-AIR-20261029-111618 | CRYPTO_PERPS_1H_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance eight USDT perpetuals: 1h: HYP-AIR-20261029-111618 |
| HYP-AIR-20261029-112141 | CRYPTO_PERPS_1H_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance eight USDT perpetuals: 1h: HYP-AIR-20261029-112141 |
| HYP-AIR-20261029-114744 | CRYPTO_PERPS_1H_V2 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance eight USDT perpetuals: 1h: HYP-AIR-20261029-114744 |
| HYP-AUTO-FUNDING7D-BINANCE-20261030 | AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Binance-only 7-day funding level carry / daily rebalance (Delphic author-rule adaptation) |
| HYP-SOC-ALG-OVERSOLD-DAX-20261031 | AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | DAX one-hour close-location oversold rebound with volatility cap |
| HYP-SOC-BTA-ADXNQ-20261031 | AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | NQ 30m ADX trend-confirmed same-day breakout after bar confirmation |
| HYP-SOC-BTA-INDEXDOWN-20261031 | INDICES_1D_V1 | ACTIVE | 1 | 1 | PASS | REVISE | STAGE3_REVISE | ES CFD delayed long after a fully observed red daily bar |
| HYP-SOC-BTA-NQMON-20261030 | AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Monday Nasdaq Bollinger ATR breakout with delayed CFD fills (BTA source adaptation) |
| HYP-SOC-BTA-TIMENQ-20261031 | AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | NQ 1h calendar-time Monday 10-22 Chicago window |
| HYP-SOC-BTA-WEIRDNQ-20261030 | AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | BTA genetic breakout of NQ 30-minute CFD after prior HLC3/open divergence |
| HYP-SOC-HL-STOCHK-BTC-20261031 | AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | BTC hourly Stoch-K oversold upward-cross mean reversion |
| HYP-SOC-QC-DUALTHRUST-ES-20261031 | AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | ES intraday reversal after a completed-bar Dual Thrust excursion |
| HYP-SOC-QC-TOM-ES-20261030 | INDICES_1D_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | ES CFD NYSE turn-of-month calendar flow (QuantConnect SPY-source adaptation) |
| HYP-SOC-QP-BTC-OVERNIGHT-20261031 | AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Bitcoin NYSE-close 10-day-max weekday overnight continuation |
| HYP-SOC-STOIC-XSMOM19-20261030 | CRYPTO_PERPS_TOP100_1H_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Stoic Research XSMOM: 10-day rank, 21-day hold, 19-of-20 author coins |
| HYP-SOC-TV-BTC-2123-20261031 | AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | BTC 21:00-23:00 UTC after-hours return premium |
| HYP-SOC-WBX-DOWNSHOCK-20261031 | AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1 | ACTIVE | 0 | 1 | REJECT | NONE | STAGE1_REJECT_OR_REVIEW | Four-hour reversal after panic sell-volume events in BTC ETH LTC XRP perpetuals |

## Recovery checkpoint

1. Resolve original blockers from individual specifications/source records; do not infer `BLOCKED_DATA` from missing attempts.
2. Compare requirements against canonical `ml4t-data` catalog and original frozen contract; prefer existing verified `NEXT_BAR` study when compatible. Syncster PR #38 merged but source checkout and isolated origin/main must be refreshed against actual merged head; no Syncster dataset was read during this reconciliation.
3. Investigate unscreened data-dependent rows individually and keep source/strategy equivalence and selection lineage intact. For `QUOTE_SIDE` original FX 115–119, compare existing `NEXT_BAR` proxy 120–124 but do not duplicate their Stage-3 REVISE results.
4. Before any new Warden screen or backtest, use the repo runbook, preflight, read-only source provenance, WAL-consistent registry snapshot, immutable evidence path and causal tests. No automated manifest signing or protected holdout access.
5. Re-read live registry before resumption because concurrent sessions can append trials. No continuous worker was started in this session.
