---
name: quant-lab-thesis
description: "Start, gate, or resume a trading thesis in quant-lab: preflight the harness, register a hypothesis, verify causality, screen, backtest, audit. Use when a new idea, paper or clipping becomes an experiment, or when a warden command refuses."
when_to_use: "Use before registering any hypothesis, editing experiments/active/, or running warden verify/screen/backtest"
dependencies: [ml4t-strategy-workflow]
paths: ["experiments/active/**", ".workspace/hypotheses/**", "strategies/candidates/**", "AGENT_RUNBOOK.md"]
---
# quant-lab thesis intake

`AGENT_RUNBOOK.md` at the repo root is the procedure; this skill is only the trigger. Read it, then:

1. `uv run python scripts/preflight_thesis.py --contract <ID>` — read-only state card. Exit `0` proceed, `2` human must re-sign the manifest, `3` stop. Obey it before doing anything else.
2. Read the **original** source, not a summary of it; every rule cites `raw/clippings/...:<line>`, and every absent rule is written `[MISSING]` rather than invented.
3. `warden register-hypothesis` → `verify` → `screen` → `backtest` (max 3 trials, ever) → `audit-check`. `qualify` is human-only.

Non-negotiables, each violated once already:

- the contract file pins the data path; never read a neighbouring Parquet
- do not recompute a gate threshold by hand — read `src/warden/runner.py` and `qualify.py`
- an absence claim needs a named search over named fields
- tests must not write `registry/registry.db`; copy it with the sqlite backup API
- never `open()`, `read()` or `stat()` anything under `data/holdout`

The harness does **not** run 1.5× cost or ±15% parameter neighbours, and Stage 0 runs at backtest time on a hard-coded volatility proxy. Declare those checks yourself or write `[MISSING]`.
