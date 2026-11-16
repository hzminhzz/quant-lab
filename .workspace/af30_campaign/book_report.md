# AF30 Handcrafted Book Report: RUN-HYP091-001

## Executive Summary
- **Contract**: MULTI_ASSET_1H_V1 (17 instruments, 1h, 2015-2025 exploratory)
- **Trial Run ID**: RUN-HYP091-001 (HYP-2026-091)
- **Verdict**: **REVISE**
- **Net Sharpe**: -0.2914
- **Gross Sharpe**: 0.2442
- **Deflated Sharpe Ratio (DSR)**: 0.0186 (family N=21.0, k_eff=11.0)
- **2x-Cost Stress Sharpe**: -0.8264
- **+1-Bar Latency Sharpe**: -0.4126
- **Annualized Turnover**: 287.96x
- **Breakeven Cost**: 0.456 bps

## Risk & Diversification Plumbing
- **Instrument Diversification Multiplier (IDM)**: 1.8762 (bounds: [1.0, 2.5])
- **Cash Volatility Scale**: 3.0000 (target: 20.0%, bounds: [0.5, 3.0])
- **Realised Train Volatility**: 5.86%
- **Realised Validation Volatility**: 4.56%
- **17x17 Asset Return Correlation**: Mean off-diagonal = 0.1446, Max off-diagonal = 0.7583

## Handcrafted Instrument Weights
| Asset | Class | Weight | Weight % |
|---|---|---|---|
| AUDUSD | fx | 0.03125 | 3.12% |
| CADJPY | fx | 0.03125 | 3.12% |
| CL | energy | 0.25000 | 25.00% |
| DAX | equity | 0.04167 | 4.17% |
| ES | equity | 0.04167 | 4.17% |
| EURUSD | fx | 0.03125 | 3.12% |
| EUSIDXEUR | equity | 0.04167 | 4.17% |
| GBPJPY | fx | 0.03125 | 3.12% |
| GBPUSD | fx | 0.03125 | 3.12% |
| HK33 | equity | 0.04167 | 4.17% |
| NIKKEI225 | equity | 0.04167 | 4.17% |
| NQ | equity | 0.04167 | 4.17% |
| USDCAD | fx | 0.03125 | 3.12% |
| USDCHF | fx | 0.03125 | 3.12% |
| USDJPY | fx | 0.03125 | 3.12% |
| XAGUSD | metals | 0.12500 | 12.50% |
| XAUUSD | metals | 0.12500 | 12.50% |

## Strategy Validation PnL Contributions
| Strategy Module | Validation Gross PnL | Share of Gross PnL |
|---|---|---|
| `ts_breakout_20_1h` | 0.0758 | 9.87% |
| `ts_ewmac_multi_1h` | -1.1472 | -149.33% |
| `ts_s01_buy_hold_single_1h` | -12.7759 | -1663.05% |
| `ts_s02_buy_hold_risk_scaled_1h` | -44.3728 | -5776.01% |
| `ts_s03_buy_hold_variable_risk_1h` | -42.8299 | -5575.18% |
| `ts_s04_buy_hold_portfolio_scaling_1h` | -42.8315 | -5575.38% |
| `ts_s05_slow_trend_long_only_1h` | 0.0005 | 0.06% |
| `ts_s06_slow_trend_long_short_1h` | 0.0384 | 5.00% |
| `ts_s07_slow_trend_strength_1h` | 0.3567 | 46.43% |
| `ts_s08_fast_trend_strength_1h` | 1.2277 | 159.81% |
| `ts_s12_adjusted_trend_1h` | 2.0340 | 264.76% |
| `ts_s13_trend_carry_regimes_1h` | 0.0314 | 4.09% |
| `ts_s14_spot_trend_1h` | 0.0283 | 3.68% |
| `ts_s17_normalised_trend_1h` | 0.0877 | 11.41% |
| `ts_s18_trend_asset_classes_1h` | 0.0318 | 4.14% |
| `ts_s19_xs_momentum_1h` | 0.6993 | 91.02% |
| `ts_s22_value_1h` | 0.1563 | 20.35% |
| `ts_s23_acceleration_1h` | 4.7388 | 616.84% |
| `ts_s26_fast_mean_reversion_1h` | 5.6321 | 733.13% |
| `ts_s27_safer_fast_mean_reversion_1h` | 4.6904 | 610.54% |

