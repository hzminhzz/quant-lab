# Shadow Engine Parity Report

## 1. Analytical Oracles (`ml4t.backtest.Engine`)
| Oracle | Status | Max Abs Error | Notes |
| :--- | :--- | ---: | :--- |
| next_bar_open | PASS | 1.819e-12 | NEXT_BAR OPEN fractional round-trip with percentage commission and slippage. |
| quote_side | PASS | 0.000e+00 | QUOTE_SIDE long (buy ask, mark bid, sell bid) and short (sell bid, mark ask, buy ask). |
| funding_8h_perps | PASS | 0.000e+00 | 25 hourly bars with 8h funding_df (h=0,8,16,24) and multiplier=2.0 ContractSpec. |
| stop_loss | PASS | 1.819e-12 | Native StopLoss(0.05) triggers on bar 3 at exact stop level minus percentage slippage. |
| trailing_stop | PASS | 0.000e+00 | Native TrailingStop(0.05) with LAGGED/CLOSE/STOP_PRICE defaults; HWM=110 -> exit=104.5. |
| cash_rejection_no_leverage | PASS | 0.000e+00 | Order notional $20,000 > $10,000 with allow_leverage=False rejects cleanly with 0 fills. |
| cost_stress_1p5x | PASS | 7.276e-12 | Commission-only 1.5x matches exactly; combined commission+slippage includes commission-on-slippage cross-term=-0.000600 (linear_only_delta=0.000600). |

## 2. Historical Replays
| Run ID | Contract | Source | sr_vec | sr_engine | abs_sr_delta | max_gross_leverage | cash_deficit_bars | funding_delta_bps | 1.5x_analytical_vs_engine_delta | engine_wall_seconds | peak_rss_mb |
| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RUN-HYP065-003 | INDICES_1H_V2 | git:225831e6 | 1.2570 | 0.6333 | 0.6237 | 0.833 | 0 | 0.0000 | 0.000501 | 5.082 | 491.5 |
| RUN-HYP066-003 | CRYPTO_MAJORS_1H_V1 | git:225831e6 | 1.3446 | 1.3378 | 0.0068 | 1.000 | 0 | 1.9682 | 0.000166 | 14.160 | 843.1 |
| RUN-HYP069-001 | INDICES_1H_V2 | git:225831e6 | 1.3923 | 1.0103 | 0.3820 | 0.503 | 0 | 0.0000 | 0.000148 | 5.305 | 843.1 |
| RUN-HYP091-001 | MULTI_ASSET_1H_V1 | worktree | 1.1501 | 1.1094 | 0.0407 | 1.880 | 6790 | 0.0000 | 0.002239 | 49.627 | 1294.9 |
| RUN-HYP120-001 | FX_MAJORS_1D_V2 | worktree | 0.6561 | 0.6177 | 0.0384 | 1.000 | 0 | 0.0000 | 0.000000 | 0.321 | 1294.9 |
| RUN-XSRR-001 | CRYPTO_PERPS_TOP100_1H_V1 | worktree | 0.1243 | 0.0595 | 0.0649 | 1.000 | 0 | 24.7453 | 0.019057 | 41.302 | 1728.0 |
| RUN-HYP114-003 | INDICES_1D_V1 | worktree | 1.2471 | 1.2387 | 0.0084 | 2.636 | 722 | 0.0000 | 0.000083 | 3.962 | 1728.0 |

## 3. Holiday & Session Behavior (`holiday_session_behavior`)
- **Synthetic Mixed Venue (`ES` + `HK33`)**: null_session_date_rows=2, partial_holiday_sessions=1, raw_error=`ValueError: session_col contains a null session date`, hourly_error=`ValueError: duplicate bar for 'HK33' in session 2025-01-28`, collapsed_error=`ValueError: session 2025-01-29 is missing bars for ['HK33']`
- **INDICES_1H_V2**: rows=302036, null_session_date_rows=91922, incomplete_timestamps=34168, incomplete_sessions=438, raw_error=`ValueError: session_col contains a null session date`, collapsed_error=`ValueError: session 2015-01-02 is missing bars for ['NIKKEI225']`
- **MULTI_ASSET_1H_V1**: rows=1004384, null_session_date_rows=106531, incomplete_timestamps=43557, incomplete_sessions=653, raw_error=`ValueError: session_col contains a null session date`, collapsed_error=`ValueError: session 2015-01-01 is missing bars for ['CL', 'DAX', 'ES', 'EUSIDXEUR', 'HK33', 'NIKKEI225', 'NQ', 'XAGUSD', 'XAUUSD']`

## 4. 100-Asset Engine Scaling (`engine_scaling_100_asset`)
| Bars | Assets | Rows | Fills | Wall (s) | Bars/s | Peak RSS (MB) | Tracemalloc Peak (MB) | <300s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 500 | 100 | 50000 | 860 | 2.261 | 221.1 | 1728.0 | 5.19 | True |
| 2000 | 100 | 200000 | 3443 | 9.361 | 213.6 | 1728.0 | 19.48 | True |
| 4000 | 100 | 400000 | 6846 | 18.605 | 215.0 | 1728.0 | 38.24 | True |
| 8760 | 100 | 876000 | 14964 | 40.742 | 215.0 | 1728.0 | 82.87 | True |
