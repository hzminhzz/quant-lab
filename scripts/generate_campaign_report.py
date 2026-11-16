#!/usr/bin/env python3
"""Generate the final AF30 Campaign Report (.workspace/af30_campaign/report.md)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3


def main():
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / ".workspace" / "af30_campaign"
    screens_path = out_dir / "screens.json"

    screens = json.loads(screens_path.read_text(encoding="utf-8"))

    db_path = repo_root / "registry" / "registry.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        search_state = conn.execute(
            "SELECT * FROM contract_search_state WHERE contract_id='MULTI_ASSET_1H_V1'"
        ).fetchone()
        t1 = conn.execute(
            "SELECT * FROM trials WHERE run_id='RUN-HYP091-001'"
        ).fetchone()
        t2 = conn.execute(
            "SELECT * FROM trials WHERE run_id='RUN-HYP091-002'"
        ).fetchone()

    lines = [
        "# AF30 Campaign Report — 20 Strategies & Handcrafted Book on MULTI_ASSET_1H_V1",
        "",
        "## 1. Overview & Setup",
        "- **Contract ID**: `MULTI_ASSET_1H_V1`",
        "- **Universe**: 17 instruments (6 equity indices: DAX, ES, EUSIDXEUR, HK33, NIKKEI225, NQ; 8 FX pairs: AUDUSD, CADJPY, EURUSD, GBPJPY, GBPUSD, USDCAD, USDCHF, USDJPY; 2 metals: XAGUSD, XAUUSD; 1 energy commodity: CL)",
        "- **Data Window**: 2015-01-01 to 2025-07-01 exploratory (1,004,384 bars, SHA256 `ff082076...`); 2025-07-01 to 2026-10-25 holdout staged (123,285 bars, SHA256 `42f42504...`)",
        "- **Data Provenance**: `verified_market` (ml4t-data minute stores normalized and causally resampled)",
        "- **Holdout Policy**: The staged holdout file (`data/holdout_staging/multi_asset_1h.parquet`) was inspected exclusively for data hygiene (identifying and filtering the 13 raw XAUUSD price anomalies in Dec 2025). The root-protected production holdout directory (`data/holdout/`) was **NEVER unsealed, accessed, or backtested**, preserving the production holdout boundary.",
        "",
        "## 2. Stage-1 Screen Results (20 Ported Strategies)",
        "",
        "| Hypothesis | Strategy | Module | Profit Factor | Cond. Edge (bps) | Trade Edge (bps) | Time in Mkt | Avg Hold (h) | 2x-Cost SR | +1-Bar SR | Turnover | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for s in screens:
        hid = s["hypothesis_id"]
        s_name = s["strategy_name"]
        mod_name = Path(s["module"]).name
        pf = f"{s['profit_factor']:.2f}" if s.get("profit_factor") is not None else "N/A"
        ce = f"{s['conditional_edge']*1e4:.2f}" if s.get("conditional_edge") is not None else "N/A"
        te = f"{s['trade_edge']*1e4:.2f}" if s.get("trade_edge") is not None else "N/A"
        tim = f"{s['time_in_market']*100:.1f}%" if s.get("time_in_market") is not None else "N/A"
        ah = f"{s['avg_holding_bars']:.1f}" if s.get("avg_holding_bars") is not None else "N/A"
        sr2 = "-"
        sr_lat = "-"
        turnover = f"~{2.0 * s['time_in_market'] / max(s['avg_holding_bars'], 1.0):.2f}/bar" if s.get('time_in_market') and s.get('avg_holding_bars') else "-"
        verdict = s["verdict"]

        lines.append(
            f"| `{hid}` | {s_name} | `{mod_name}` | {pf} | {ce} | {te} | {tim} | {ah} | {sr2} | {sr_lat} | {turnover} | **{verdict}** |"
        )

    lines.extend([
        "",
        "## 3. Handcrafted Book Trials Comparison",
        "",
        "| Metric | Trial 1 (`RUN-HYP091-001`) | Trial 2 (`RUN-HYP091-002`) | Target / Requirement | Status |",
        "|---|---|---|---|---|",
        f"| **Hypothesis / Run** | HYP-2026-091 / T1 | HYP-2026-091 / T2 | Governed Lineage | Trials 1 & 2 of 3 used |",
        f"| **Universe** | Full 17 Instruments | 15 Instruments (excl. XAG, CL) | Liquid Multi-Asset | Excluded high-vol assets |",
        f"| **Stage Reached** | `{t1['stage_reached']}` | `{t2['stage_reached']}` | `STAGE_3_STRESS` | Both reached Stage 3 |",
        f"| **Verdict** | **`{t1['verdict']}`** | **`{t2['verdict']}`** | `CANDIDATE_FOR_QUALIFICATION` | Both Falsified |",
        f"| **Gross Sharpe** | `{t1['gross_sharpe']:.4f}` | `{t2['gross_sharpe']:.4f}` | > 0 | Both positive pre-friction |",
        f"| **Net Sharpe** | `{t1['net_sharpe']:.4f}` | `{t2['net_sharpe']:.4f}` | > 1.0 | **Failed** |",
        f"| **Deflated Sharpe (DSR)** | `{t1['deflated_sharpe_prob']:.4f}` | `{t2['deflated_sharpe_prob']:.4f}` | >= 0.90 | **Failed** |",
        f"| **2x-Cost Stress Sharpe** | `{t1['stress_2x_cost_sharpe']:.4f}` | `{t2['stress_2x_cost_sharpe']:.4f}` | > 0.50 | **Failed** |",
        f"| **+1-Bar Latency Sharpe** | `{t1['delay_plus1_sharpe']:.4f}` | `{t2['delay_plus1_sharpe']:.4f}` | > 0.0 | **Failed** |",
        f"| **Annualized Turnover** | `{t1['annualized_turnover']:.2f}x` | `{t2['annualized_turnover']:.2f}x` | Low turnover | Increased churn on 15 assets |",
        f"| **Breakeven Cost** | `{t1['breakeven_cost_bps']:.3f} bps` | `{t2['breakeven_cost_bps']:.3f} bps` | >= 1.0 bps round-trip | Sub-friction margin |",
        f"| **Max Drawdown** | `{t1['max_drawdown_pct']:.2f}%` | `{t2['max_drawdown_pct']:.2f}%` | Controlled | Higher DD on 15 assets |",
        "",
        "## 4. Multiple-Testing & DSR Family State",
        f"- **Contract Family**: `MULTI_ASSET_1H_V1`",
        f"- **Selection Attempts in DB**: 20 standalone screens + 3 param grid configurations + 2 trials = **25 attempts**.",
        f"- **Current Applied k_eff Gate**: {search_state['max_keff_gate']:.1f}",
        f"- **Current Expected Max Sharpe Under Null (Noise Floor)**: {search_state['max_noise']:.4f}",
        f"- **Required Net Sharpe for DSR >= 0.90**: ~1.55 annualised Sharpe required to exceed the multiple-testing hurdle.",
        "",
        "### Governance & Accounting Disclosures",
        "1. **Trial 1 DSR Accounting Disclosure**: In `runner.py:331-333`, `n_attempts` is evaluated (`hist['raw_attempt_count'] + 1`) before the `param_grid` configurations are recorded (lines 366-380). Consequently, Trial 1 (`RUN-HYP091-001`) evaluated DSR against $N=21$ (20 prior screens + 1 trial), omitting the 3 param-grid attempts evaluated during that run. After Trial 1 completed, the actual attempt count was $N=24$, and is now $N=25$ with Trial 2.",
        "2. **Unregistered Exploratory Sweep Disclosure**: Direct, un-governed simulation calls (`simulate_positions`) across combinations of `rebalance_bars` and `stop_multiple` were run during root-cause analysis in the session transcript. These ad-hoc validation sweeps were NOT tracked in `selection_attempts` or `registry.db`. Consequently, the registry figures ($N=25$, $k_{\\text{eff}}=22.5$) represent **registry-recorded attempts only**, not a complete accounting of all validation compute. Any future parameters derived from those informal searches would suffer from unmeasured selection bias and must not be treated as pre-registered hypotheses.",
        "",
        "## 5. Adversarial 6-Check Audit (Trial 2: RUN-HYP091-002)",
        "- [PASS] `1_target_rebalance_alignment`: Execution lag and signal delay aligned.",
        "- [FAIL] `2_outlier_sensitivity`: Performance degrades when top 5% returning bars are stripped.",
        "- [PASS] `3_point_in_time_causality`: Zero future-leakage verified across feature pipelines.",
        "- [PASS] `4_fold_local_fitting`: Folds isolated, zero validation labels in feature preprocessors.",
        "- [FAIL] `5_cost_and_latency_survival`: Strategy fails under 2x costs (-1.08 SR) and +1 bar latency (-0.52 SR).",
        "- [FAIL] `6_selection_bias_accounting`: Net Sharpe of -0.495 fails the contract-wide DSR hurdle (p=0.0033 vs 0.90).",
        "",
        "## 6. Falsification Verdict & Scientific Conclusion",
        "",
        "**Verdict: The Robert Carver AF30 Handcrafted Book is FALSIFIED across both Trial 1 (17 instruments) and Trial 2 (15 instruments without XAG and CL).**",
        "",
        "Under the pre-registered hypothesis falsification criteria (`Book DSR < 0.90, or net Sharpe <= 1.0, or 2x-cost Sharpe <= 0.50, or +1-bar Sharpe <= 0`), Trial 2 failed all four gates: Net Sharpe = -0.4946, DSR = 0.0033 (against $k_{\\text{eff}}=22.5$ and noise floor 1.224), 2x-cost Sharpe = -1.0836, and +1-bar latency Sharpe = -0.5185. Removing silver (`XAGUSD`) and crude oil (`CL`) eliminated two volatile instruments but did not resolve the core structural issue: 24-hour rebalancing on 1-hour bars with a 3xATR-14 trailing stop whipsaws frequently, generating 320.27x annualized turnover and consuming all gross edge (gross Sharpe +0.0953). In accordance with quant-lab governance, zero holdout data was unsealed, and HYP-2026-091 now has exactly 1 trial remaining before lineage budget exhaustion.",
    ])

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {report_path}")


if __name__ == "__main__":
    main()
