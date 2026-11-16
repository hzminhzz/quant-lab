# HYP-SOC-BTA-WEIRDNQ-20261030 — Stage 1 native Warden result

**Decision: STAGE_1_REJECT (frozen configuration).**

- Original publisher: [BTA weird NASDAQ algorithm](https://breakouttradingacademy.com/weird-nasdaq-algo-strategy/), dated 2024-05-22, current source capture 2026-10-30 SHA256 `6e919373c1d70ed055ff543d57d77beeb1629b7283012159ff268e01128d4326`.
- Original futures strategy: NQ 30-minute, long breakout during 07:00–18:00 US Eastern at previous high + absolute difference of typical price and highest open of preceding 35 bars, exit same trading day, original *$1,000 futures contract stop loss*. Formula discovered via genetic algorithm across thousands of variants: **heavy source selection bias**.
- Adaptation explicitly changes execution: NQ CFD 30m, previous completed HLC3 as ambiguous typical-price term, detect current bar HIGH cross after the bar completes, Warden market NEXT_BAR next 30m execution instead of intrabar stop. Early entry stop NOT equivalent; **original $1000 futures stop OMITTED** in the native weight-based fill model. Desired exit signal at 17:30 ET, NEXT_BAR 18:00 ET. This is *not* a faithful futures strategy reproduction.
- New principal-delegated research-only contract `AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1` derived from `INDICES_30M_V1`, same explorer/validation/holdout data SHA `25f8c39dc245f0fe6070ce3fe3cd4063fd3093f7c951d9ed017f36367f23ff8f`, label and embargo 24 half-hour bars, 1 next-bar delay, 0.5bps taker +1.0bps slippage *per side*. Original related 30m Warden prior attempts in the new contract selection group=0, but the publisher's GA search history is much larger and **must** be reflected by later DSR audit.
- Nine focused tests passed: frozen signal and market, prefix invariance, prior high/open formula, 30m delayed exit, New York DST, no-trade control. No protected data.
- Guarded durable verify job `warden-verify-3bc96aea8bba1743d2`: PASS, 109,100 NQ rows, prefix_invariance=true, verified_market.
- Guarded durable screen job `warden-screen-8f681692bda9150bff`: native Stage1 scientific REJECT, exit1, immutable selection attempt `ATT-5baffc268b6d26ce-001`.

| Metric | Native result | Gate |
| --- | ---: | --- |
| Net profit factor | **1.047512403460569** | >=1.15 (FAIL) |
| Bootstrap p-value | 0.04 | <0.05 (PASS, uncorrected for source GA search) |
| Conditional edge | 0.00006091939629418795 | Positive |
| Time in market | 0.21357333981999513 | >=0.05 (PASS) |
| Trade edge | 0.0008228804607122618 | Positive |
| Average holding | 13.507692307692308 **30-minute bars** | Informational |

The Warden notes incorrectly abbreviate holding as `h`; the verified contract timeframe is 30 minutes. This is not 13.5 clock hours.

**Outcome:** Do not backtest, retune, invert, change fill timing, or claim a Stage1 PASS. Do not treat the source author's unadjusted performance claims as independent evidence. Source text captured 2026 cannot authenticate exact 2024 vintage or prove 2023–2025 validation was historically unseen. Production holdout not accessed. No live orders.

At this completed checkpoint the Warden has **140 hypotheses, 71 full backtests, 175 Stage1 and other selection attempts**. Prior HYP-SOC-BTA-NQMON and HYP-SOC-QC-TOM-ES Stage1 rejections stay frozen; old XSRR quarantines untouched.

## Next research action

Proceed to other publisher/source family or build a faithful quote/tick stop-fill data contract with proper bid/ask and original $-stop simulator under human-owned Warden engineering governance. In the meanwhile retain read-only archive queue progression. Do not spend attempts on a lightly modified version of this genetic source formula.
