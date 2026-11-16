# Why the reproduction has no edge on our data (read-only diagnostic)

Validation slice 2023-01-01 .. 2025-07-01, 806 dates, 17 instruments. Cross-sectional
Spearman IC of each component against forward returns, `t` is the naive IC t-stat.

| component | IC 1d | t | IC 5d | t |
|---|---|---|---|---|
| TS_trend (EWMAC ensemble) | 0.0191 | 1.47 | 0.0159 | 1.26 |
| XS_trend (cross-sectional z) | 0.0180 | 1.39 | **0.0302** | **2.34** |
| Skew | 0.0044 | 0.36 | 0.0005 | 0.04 |
| VoV | −0.0008 | −0.07 | −0.0015 | −0.14 |
| Trend_sign (0.5 TS + 0.5 XS) | 0.0171 | 1.36 | 0.0236 | 1.95 |
| Sign_raw (composite) | 0.0135 | 1.18 | 0.0171 | 1.52 |
| **SIGNAL (after FDM + EWM smooth)** | **0.0094** | **0.84** | **0.0027** | **0.25** |

Reading:

1. No sign is inverted. Every sleeve points the same way it does in the repo.
2. The two traded sleeves are weak but positive; the two "diversifiers" are zero.
   Skew and VoV contribute a quarter of the book each and carry no information here.
3. The composite decays as it is assembled: 0.0236 (trend) → 0.0171 (composite) →
   0.0027 (after FDM and the EWM(0.5) smoother). The smoothing halves the turnover
   *and* the signal.
4. A near-zero IC book at 146× annual turnover cannot clear a 1.5× cost hurdle, which is
   what the Stage-1 edge gate reported (−1.6 bps/bar) and Stage 3 confirmed (−0.455 netSR).

So the negative result is not a port defect. It is what this signal set does on 17
instruments over 10.5 years.

Prior evidence on the same contract, for context: HYP-2026-103 (EWMAC ensemble alone)
Stage-1 REJECT at PF 1.02; HYP-2026-099 (slow trend, long only) netSR 0.879, REVISE;
HYP-2026-127 (this composite) netSR −0.455, REVISE. Every trend-flavoured configuration
tried on MULTI_ASSET_1D_V1 lands between −0.46 and +0.88 netSR. The DSR 0.90 hurdle is
2.27.
