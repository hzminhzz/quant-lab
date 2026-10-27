# Carver AF30 Daily Suite - Stage-1 Screen Report

- **Contract**: `MULTI_ASSET_1D_V1` (17 instruments, 312 bars/year, 2015-2025 exploratory)
- **Universe**: 6 equity indices, 8 FX pairs, 2 metals, 1 energy
- **Strategies**: 17 (`strategies/candidates/carver_daily/`)
- **Method**: `warden verify` (prefix invariance) then `warden screen` (Stage-1 signal gate)
- **Disclosure**: no `warden backtest` ran in this work unit and no holdout data was
  read. Every screen writes one `selection_attempt` row, which enlarges the
  multiple-testing family for later DSR evaluation on this contract.

| Hypothesis | Strategy | Module | Profit Factor | Cond. Edge (bps) | Trade Edge (bps) | Time in Mkt | Avg Hold (bars) | Verdict |
|---|---|---|---|---|---|---|---|---|
| `HYP-2026-095` | s01 | `s01_buy_hold_single.py` | 1.26 | 3.39 | 5457.18 | 100.0% | 1612.0 | **PASS** |
| `HYP-2026-096` | s02 | `s02_buy_hold_risk_scaled.py` | 1.25 | 2.81 | 2754.52 | 100.0% | 981.1 | **PASS** |
| `HYP-2026-097` | s03 | `s03_buy_hold_variable_risk.py` | 1.21 | 2.85 | 311.90 | 100.0% | 109.6 | **PASS** |
| `HYP-2026-098` | s04 | `s04_buy_hold_portfolio_scaling.py` | 1.21 | 6.55 | 312.58 | 100.0% | 47.7 | **PASS** |
| `HYP-2026-099` | s05 | `s05_slow_trend_long_only.py` | 1.15 | 1.70 | 90.84 | 100.0% | 53.5 | **PASS** |
| `HYP-2026-100` | s06 | `s06_slow_trend_long_short.py` | 1.03 | 0.44 | 15.46 | 100.0% | 35.1 | **REJECT** |
| `HYP-2026-101` | s07 | `s07_slow_trend_strength.py` | 1.05 | 0.70 | 23.10 | 100.0% | 32.8 | **REJECT** |
| `HYP-2026-102` | s08 | `s08_fast_trend_strength.py` | 1.03 | 0.52 | 12.93 | 100.0% | 24.6 | **REJECT** |
| `HYP-2026-103` | s09 | `s09_ewmac_multi.py` | 1.02 | 0.45 | 4.91 | 100.0% | 11.0 | **REJECT** |
| `HYP-2026-104` | s12 | `s12_adjusted_trend.py` | 1.03 | 0.62 | 5.20 | 100.0% | 8.4 | **REJECT** |
| `HYP-2026-105` | s14 | `s14_spot_trend.py` | 1.05 | 0.70 | 23.10 | 100.0% | 32.8 | **REJECT** |
| `HYP-2026-106` | s17 | `s17_normalised_trend.py` | 1.14 | 0.54 | 9.86 | 100.0% | 18.3 | **REJECT** |
| `HYP-2026-107` | s18 | `s18_trend_asset_classes.py` | 1.02 | 0.45 | 5.01 | 100.0% | 11.1 | **REJECT** |
| `HYP-2026-108` | s19 | `s19_xs_momentum.py` | 1.01 | 0.45 | 2.74 | 100.0% | 6.1 | **REJECT** |
| `HYP-2026-109` | s21 | `s21_breakout.py` | 1.09 | 1.03 | 42.11 | 100.0% | 41.0 | **REJECT** |
| `HYP-2026-110` | s22 | `s22_value.py` | 0.93 | -0.85 | -50.35 | 100.0% | 59.6 | **REJECT** |
| `HYP-2026-111` | s23 | `s23_acceleration.py` | 1.03 | 0.69 | 7.82 | 100.0% | 11.3 | **REJECT** |

**Result**: 5 of 17 cleared the Stage-1 gate (PF >= 1.15, trade edge > 1.5x one-way cost, time in market >= 5%, bootstrap p < 0.05).

## Notes per strategy

- `HYP-2026-095` s01: from existing screen record
- `HYP-2026-096` s02: PF=1.25, Edge=2.8bps, TiM=100.0%, Holding=981.1h
- `HYP-2026-097` s03: PF=1.21, Edge=2.8bps, TiM=100.0%, Holding=109.6h
- `HYP-2026-098` s04: PF=1.21, Edge=6.5bps, TiM=100.0%, Holding=47.7h
- `HYP-2026-099` s05: PF=1.15, Edge=1.7bps, TiM=100.0%, Holding=53.5h
- `HYP-2026-100` s06: PF=1.03, Edge=0.4bps, TiM=100.0%, Holding=35.1h
- `HYP-2026-101` s07: PF=1.05, Edge=0.7bps, TiM=100.0%, Holding=32.8h
- `HYP-2026-102` s08: PF=1.03, Edge=0.5bps, TiM=100.0%, Holding=24.6h
- `HYP-2026-103` s09: PF=1.02, Edge=0.4bps, TiM=100.0%, Holding=11.0h
- `HYP-2026-104` s12: PF=1.03, Edge=0.6bps, TiM=100.0%, Holding=8.4h
- `HYP-2026-105` s14: PF=1.05, Edge=0.7bps, TiM=100.0%, Holding=32.8h
- `HYP-2026-106` s17: PF=1.14, Edge=0.5bps, TiM=100.0%, Holding=18.3h
- `HYP-2026-107` s18: PF=1.02, Edge=0.5bps, TiM=100.0%, Holding=11.1h
- `HYP-2026-108` s19: PF=1.01, Edge=0.4bps, TiM=100.0%, Holding=6.1h
- `HYP-2026-109` s21: PF=1.09, Edge=1.0bps, TiM=100.0%, Holding=41.0h
- `HYP-2026-110` s22: PF=0.93, Edge=-0.8bps, TiM=100.0%, Holding=59.6h
- `HYP-2026-111` s23: PF=1.03, Edge=0.7bps, TiM=100.0%, Holding=11.3h
