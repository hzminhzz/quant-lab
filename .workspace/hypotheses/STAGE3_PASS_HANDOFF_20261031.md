# Guarded Stage 3 handoff — 2026-10-31

**Status: STAGE3_REVISE; SIX_CHECK_AUDIT_REJECTED; DO_NOT_RETRY.**

This file is a source-to-Warden continuation pointer, NOT trading approval,
an independent holdout evaluation, or proof of source strategy replication.

## Native evidence

- Hypothesis: `HYP-SOC-BTA-INDEXDOWN-20261031`
- Strategy: `strategies/candidates/bta_index_down_es_cfd_1d_20261031.py`
- Experiment: `experiments/active/bta_index_down_es_cfd_1d_20261031.yaml`
- Research contract: `INDICES_1D_V1`, ES daily CFD, NEXT_BAR delay1, 0.5bps taker + 0.5bps slippage EACH side.
- Stage1 attempt: `ATT-4f8505fedde82ba2-001`, outcome **PASS**.
- Durable verify: `warden-verify-1fa223190d364f0461`, status PASS on 3,111 verified market rows.
- Durable screen: `warden-screen-bf18f8984c89556595`, PF 1.3598363108787053, block bootstrap p reported 0.0, TiM 0.45629820051413883, mean holding 1.7574257425742574 DAILY bars, conditional edge 0.0010928703154558582, trade edge 0.0019206384256773745.
- Test: `uv run pytest -q tests/experiments/test_bta_index_down_es_cfd_1d_20261031.py`: 2 passed; combined with other two hypotheses, 12 tests passed.
- Author source original 2022-12-01 BTA https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/ captured 2026-10-30, SHA256 a5c0671d0136297977aebd4e3b81bc708fdd46cb56a063e263827ec20d205b5a.
- Result file `.workspace/hypotheses/HYP-SOC-BTA-INDEXDOWN-20261031_RESULT.md`.
- Durable batch + eight blocked source leads: `/home/quant/dev/AI-research-loop/examples/THESIS_FACTORY_20261031_BATCH1.json` (GitHub main PR #10).

## Completed Stage 2 and official Stage 3 — 2026-10-31

- **Read-only Stage 2:** replayed the exact immutable ES signal on approved exploratory data through Warden's existing next-bar simulation, without any Warden registry mutation. Net Sharpe at 1x/1.5x/2x costs **1.034708 / 0.974710 / 0.914685**; +1 bar latency **−0.269278**; remove top 5% best validation bars **−1.741000**. 129 of 778 validation bars have Sunday UTC timestamps; Sunday/Monday return intervals supply approximately 48.8% of the frozen strategy's summed net bar returns. These are attribution and sensitivity diagnostics, not a new weekday strategy.
- **Only native full trial:** `RUN-SOC-BTA-INDEXDOWN-20261031`, durable `warden-backtest-0d82835a48987553fd`. Exact frozen `experiments/active/bta_index_down_es_cfd_1d_20261031.yaml` executed under shared governance-guard/Warden lock; job terminal receipt records `WARDEN_RUN_COMPLETE`. Native verdict **REVISE**, net Sharpe 1.034708, DSR 0.843550 (**below required 0.90**), maxDD12.73%, 2x-cost SR0.914685, +1bar SR−0.269278. There is no economic or statistical justification to retune after seeing this verdict.
- **Strategy-bound native causality verification:** `warden verify-causality --run-id RUN-SOC-BTA-INDEXDOWN-20261031` passed 5 prefix cuts, binding recorded strategy code/config and exploratory dataset hashes. The original Stage1 prefix verification alone did not provide code-bound Stage3 evidence.
- **Native six-check audit:** `warden audit-check --run-id RUN-SOC-BTA-INDEXDOWN-20261031` **REJECTED** (checks 2/outliers and 5/cost+latency failed; checks 1/3/4/6 passed). The original pre-causality audit failed check3; after exact immutable code-bound verification, the audit was deterministically recomputed to 4/6. **No second Warden trial.**
- **Source fidelity limitations remain:** published BTA cash-index tendency has six stated tendencies, unknown wider publisher search and unspecified executable rules; our ES CFD red-day next-open variant is a different hypothesis. Sunday and midnight-UTC CFD daily bars are not NYSE official cash-close bars. The historical author selection count is not included in Warden's DSR correction. The 1bp/side native index cost is a fixed research estimate, not broker-executable spread calibration.
- **No protected holdout, capital allocation or orders.** Stage3 result and audit are negative; holdout qualification must NOT be used to rehabilitate the strategy.

Additional durable evidence in AI-research-loop main: `examples/STAGE2_ES_INDEXDOWN_20261031.json`, `examples/STAGE1_PASS_STAGE23_CLOSURE_20261031.json` and `.md`; standalone reproducible stage2 source `scripts/stage2_es_indexdown_readonly.py`.

## Routing and terminal state

**Do not run Stage3 again for this exact frozen ES candidate, do not repeat Stage1, change its clock, add filters, invert it, relabel as a new hypothesis, or attempt a holdout evaluation.** Its Stage3 `REVISE` and native six-check `REJECTED` are preserved as immutable negative evidence. The Research Director may continue discovery of *independent* strategies and should reference this closure, not the former `STAGE1_PASS_ONLY` checkpoint.

Native registry after the guarded trial: **148 hypotheses, 72 full trials, 183 selection attempts**. Native HYP-SOC Stage1 usage remains **12/20** (at most eight future truly distinct source screens). Concurrent work unrelated to this hypothesis preserved.
