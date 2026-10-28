---
hypothesis_id: HYP-2026-XXX
contract_id: CONTRACT_ID_HERE
title: One line, no marketing
strategy_family: rules_ts            # rules_ts | cross_sectional_ml | carry_rv | meta_overlay
economic_mechanism: >
  Who pays, and why do they keep paying? State the mechanism, not the pattern.
counterparty_or_friction: >
  Name the counterparty or the friction the edge is paid from. "The market" is not an answer.
expected_holding_bars: 1             # must match the simulated hold; 1 for a one-bar rule
falsification_criteria: >
  Reject at Stage 1 if <family gate>. Reject at Stage 3 if Net Sharpe < 1.0,
  2x cost Sharpe <= 0.50, +1-bar Sharpe <= 0, or DSR < 0.90. No gate may be lowered afterwards.
source_provenance: raw/clippings/<publication>/<file>.md:LINE-LINE   # original source, not a plan about it
llm_model_id: <model>
status: QUEUED
---

# <Title>

## Rules, transcribed from the source

| Rule | Source line | Status |
|---|---|---|
| entry | `file.md:NN` | verbatim |
| exit | `file.md:NN` | verbatim |
| sizing | — | `[MISSING]` — do not invent |
| stop | — | `[MISSING]` — do not invent |

## Pre-registered choices (decided before any market data is read)

- execution horizon: …
- cost model: `contracts/<id>.yaml` → … bps per side, funding …
- convention/parameters not fixed by the source: …

## Falsification

State the exact terminal verdicts this thesis may reach and what evidence produces each.
