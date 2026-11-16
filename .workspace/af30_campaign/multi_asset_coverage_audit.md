# Multi-Asset Coverage & Bar Completeness Audit across Frequencies

## 1. Overview & Scope
This audit validates session geometry, bar counts, and calendar alignment across all five multi-asset contracts (`5m`, `30m`, `1h`, `4h`, `1d`) for 17 liquid instruments over the 10-year exploratory window (2015-01-01 to 2025-07-01).

## 2. Session Hours & Exchange Calendar Invariants
Different asset classes trade under different market conventions. In a unified multi-asset panel:
- **EQUITY_US**: CME Globex: Sun 23:00 - Fri 21:00 UTC (pause 21:00-22:00 UTC daily)
- **EQUITY_EU**: Eurex: Mon-Fri 06:00 - 20:00 UTC
- **EQUITY_ASIA**: HKEX / OSE: Mon-Fri 01:00 - 08:30 UTC / 00:00 - 06:00 UTC
- **FX**: Continuous Interbank: Sun 21:00 - Fri 21:00 UTC (24/5)
- **METALS**: Loco London / Comex: Sun 22:00 - Fri 21:00 UTC (pause 21:00-22:00 UTC daily)
- **ENERGY**: NYMEX Globex: Sun 23:00 - Fri 21:00 UTC (pause 21:00-22:00 UTC daily)

Because European and Asian exchanges operate only during local business hours while FX trades continuously, bar counts naturally differ across instruments at intraday resolutions. This is an intrinsic property of multi-asset union grids.

## 3. Bar Coverage Matrix across Frequencies

| Asset | Class | 5m Bars | 30m Bars | 1h Bars | 4h Bars | 1d Bars | Start Date | End Date |
|---|---|---|---|---|---|---|---|---|
| `AUDUSD` | fx | 788,532 | 131,958 | 66,128 | 17,143 | 3,345 | 2015-01-01 | 2025-06-30 |
| `CADJPY` | fx | 784,795 | 130,838 | 65,422 | 16,908 | 3,281 | 2015-01-01 | 2025-06-30 |
| `CL` | energy | 716,954 | 120,197 | 60,289 | 16,255 | 3,154 | 2015-01-02 | 2025-06-30 |
| `DAX` | equity_eu | 646,146 | 109,157 | 55,812 | 15,759 | 3,092 | 2015-01-01 | 2025-06-30 |
| `ES` | equity_us | 645,888 | 109,100 | 55,766 | 15,786 | 3,111 | 2015-01-01 | 2025-06-30 |
| `EURUSD` | fx | 787,472 | 131,826 | 66,057 | 17,129 | 3,350 | 2015-01-01 | 2025-06-30 |
| `EUSIDXEUR` | equity_eu | 442,904 | 75,363 | 37,892 | 12,104 | 2,685 | 2015-01-02 | 2025-06-30 |
| `GBPJPY` | fx | 784,266 | 130,829 | 65,421 | 16,908 | 3,281 | 2015-01-01 | 2025-06-30 |
| `GBPUSD` | fx | 786,312 | 131,901 | 66,111 | 17,140 | 3,345 | 2015-01-01 | 2025-06-30 |
| `HK33` | equity_asia | 423,645 | 73,655 | 38,096 | 12,124 | 2,590 | 2015-01-02 | 2025-06-30 |
| `NIKKEI225` | equity_asia | 681,243 | 114,986 | 58,704 | 16,404 | 3,108 | 2015-01-01 | 2025-06-30 |
| `NQ` | equity_us | 645,888 | 109,100 | 55,766 | 15,786 | 3,111 | 2015-01-01 | 2025-06-30 |
| `USDCAD` | fx | 787,814 | 131,796 | 66,044 | 17,116 | 3,346 | 2015-01-01 | 2025-06-30 |
| `USDCHF` | fx | 784,341 | 130,818 | 65,417 | 16,909 | 3,282 | 2015-01-01 | 2025-06-30 |
| `USDJPY` | fx | 786,495 | 131,910 | 66,120 | 17,137 | 3,346 | 2015-01-01 | 2025-06-30 |
| `XAGUSD` | metals | 712,227 | 118,893 | 59,480 | 16,094 | 3,131 | 2015-01-01 | 2025-06-29 |
| `XAUUSD` | metals | 668,548 | 111,556 | 55,859 | 15,115 | 2,946 | 2015-01-01 | 2025-06-29 |

## 4. Empirical Session & Calendar Observations
1. **Continuous FX (24/5)**: The 8 FX pairs print the highest bar counts across all frequencies (~788k 5m bars, ~131k 30m bars, ~17k 4h bars, ~3,350 daily bars). In UTC calendar day bins, Sunday evening trading (21:00–23:59 UTC) creates a 6th trading session per week (~312–314 days/year).
2. **US Equity Futures & Energy (CL, ES, NQ)**: Operate 23 hours/day with a 1-hour maintenance pause at 21:00 UTC, yielding ~645k 5m bars and ~3,111 daily bars.
3. **European Equity Indices (DAX, EUSIDXEUR)**: Open ~06:00 to 20:00 UTC (14h/day), yielding ~442k–646k 5m bars. EUSIDXEUR starts in 2015 with regional holiday pauses.
4. **Asian Equity Indices (HK33, NIKKEI225)**: HK33 operates during Hong Kong exchange hours (~6.5h/day), yielding ~423k 5m bars and ~2,590 daily bars.

## 5. Model Guidance & Methodological Limitations
- **Cross-Sectional Models**: Strategies using cross-sectional ranking (e.g. `cross_sectional_ml` or relative value) must mask out instruments that are outside their active exchange hours at that timestamp (e.g. via an observation or volume mask). Ranking a closed instrument against an active instrument using stale prices induces artificial signal distortion.
- **Time-Series Models**: Pure time-series strategies (e.g. `rules_ts`) water-fill across active bars; when an instrument is closed, its position is held constant until the next open bar.
- **Partial/Stub Bars**: The Sunday evening open in FX produces 3 hours of trading on calendar Sunday UTC. Slower strategies on daily frequencies (`MULTI_ASSET_1D_V1`) should be aware that Sunday bars have lower volume than midweek bars.
