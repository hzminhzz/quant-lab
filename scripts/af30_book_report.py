#!/usr/bin/env python3
"""Generate AF30 Handcrafted Book report and book_stats.json."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.strategy_loader import load_module_from_path
from warden_ops.readonly import ReadOnlyResearchRegistry


def main():
    repo_root = Path(__file__).resolve().parent.parent
    contract_path = repo_root / "contracts" / "multi_asset_1h_v1.yaml"
    contract = load_contract(contract_path)

    book_path = repo_root / "strategies" / "books" / "af30_handcrafted_book_1h.py"
    book_mod = load_module_from_path(book_path)

    panel_df = pl.read_parquet(repo_root / contract.exploratory_data_path)
    out = book_mod.generate_positions(panel_df, contract)
    stats = book_mod.LAST_BOOK_STATS

    trial_row = ReadOnlyResearchRegistry(
        repo_root / "registry" / "registry.db").read_trial_evidence("RUN-HYP091-001")
    if trial_row is None:
        raise LookupError("RUN-HYP091-001 is not recorded in this registry")

    train_idx = stats["train_indices"]
    val_idx = stats["val_indices"]
    pos = out["positions"]
    ret = stats["returns"]

    book_ret_train = np.sum(pos[train_idx] * ret[train_idx], axis=1)
    book_ret_val = np.sum(pos[val_idx] * ret[val_idx], axis=1)
    train_vol = float(np.std(book_ret_train) * math.sqrt(contract.bars_per_year))
    val_vol = float(np.std(book_ret_val) * math.sqrt(contract.bars_per_year))

    total_val_gross = float(np.sum(book_ret_val))
    strat_contribs = {}
    per_strat_pos = stats["per_strategy_positions"]
    strat_modules = stats["strategy_modules"]

    for m_path, s_pos in zip(strat_modules, per_strat_pos):
        mod_name = Path(m_path).stem
        strat_val_ret = np.sum(s_pos[val_idx] * ret[val_idx])
        pct_contrib = float(strat_val_ret / total_val_gross) if abs(total_val_gross) > 1e-12 else 0.0
        strat_contribs[mod_name] = {
            "module": m_path,
            "val_gross_pnl": float(strat_val_ret),
            "share_of_val_gross": pct_contrib,
        }

    weights = stats["weights"]

    book_stats_payload = {
        "contract_id": contract.contract_id,
        "run_id": "RUN-HYP091-001",
        "weights": weights,
        "idm": stats["idm"],
        "vol_scale": stats["vol_scale"],
        "realised_train_vol": train_vol,
        "realised_val_vol": val_vol,
        "corr_mean_off_diag": stats["corr_mean_off_diag"],
        "corr_max_off_diag": stats["corr_max_off_diag"],
        "strategy_contributions": strat_contribs,
        "net_sharpe": float(trial_row["net_sharpe"]) if trial_row else None,
        "annualized_turnover": float(trial_row["annualized_turnover"]) if trial_row else None,
        "breakeven_cost_bps": float(trial_row["breakeven_cost_bps"]) if trial_row else None,
        "deflated_sharpe_prob": float(trial_row["deflated_sharpe_prob"]) if trial_row else None,
        "stress_2x_cost_sharpe": float(trial_row["stress_2x_cost_sharpe"]) if trial_row else None,
        "delay_plus1_sharpe": float(trial_row["delay_plus1_sharpe"]) if trial_row else None,
        "verdict": trial_row["verdict"] if trial_row else None,
    }

    out_dir = repo_root / ".workspace" / "af30_campaign"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "book_stats.json"
    json_path.write_text(json.dumps(book_stats_payload, indent=2), encoding="utf-8")

    md_lines = [
        "# AF30 Handcrafted Book Report: RUN-HYP091-001",
        "",
        "## Executive Summary",
        f"- **Contract**: {contract.contract_id} (17 instruments, 1h, 2015-2025 exploratory)",
        f"- **Trial Run ID**: RUN-HYP091-001 (HYP-2026-091)",
        f"- **Verdict**: **{trial_row['verdict']}**",
        f"- **Net Sharpe**: {trial_row['net_sharpe']:.4f}",
        f"- **Gross Sharpe**: {trial_row['gross_sharpe']:.4f}",
        f"- **Deflated Sharpe Ratio (DSR)**: {trial_row['deflated_sharpe_prob']:.4f} (family N={trial_row['total_trials_n']}, k_eff={trial_row['effective_trials_keff']:.1f})",
        f"- **2x-Cost Stress Sharpe**: {trial_row['stress_2x_cost_sharpe']:.4f}",
        f"- **+1-Bar Latency Sharpe**: {trial_row['delay_plus1_sharpe']:.4f}",
        f"- **Annualized Turnover**: {trial_row['annualized_turnover']:.2f}x",
        f"- **Breakeven Cost**: {trial_row['breakeven_cost_bps']:.3f} bps",
        "",
        "## Risk & Diversification Plumbing",
        f"- **Instrument Diversification Multiplier (IDM)**: {stats['idm']:.4f} (bounds: [1.0, 2.5])",
        f"- **Cash Volatility Scale**: {stats['vol_scale']:.4f} (target: 20.0%, bounds: [0.5, 3.0])",
        f"- **Realised Train Volatility**: {train_vol * 100:.2f}%",
        f"- **Realised Validation Volatility**: {val_vol * 100:.2f}%",
        f"- **17x17 Asset Return Correlation**: Mean off-diagonal = {stats['corr_mean_off_diag']:.4f}, Max off-diagonal = {stats['corr_max_off_diag']:.4f}",
        "",
        "## Handcrafted Instrument Weights",
        "| Asset | Class | Weight | Weight % |",
        "|---|---|---|---|",
    ]
    for sym, w_val in sorted(weights.items()):
        cls = book_mod.CLASS_OF.get(sym, "unknown")
        md_lines.append(f"| {sym} | {cls} | {w_val:.5f} | {w_val*100:.2f}% |")

    md_lines.extend([
        "",
        "## Strategy Validation PnL Contributions",
        "| Strategy Module | Validation Gross PnL | Share of Gross PnL |",
        "|---|---|---|",
    ])
    for s_name, data in sorted(strat_contribs.items()):
        md_lines.append(f"| `{s_name}` | {data['val_gross_pnl']:.4f} | {data['share_of_val_gross']*100:.2f}% |")

    md_lines.append("")
    md_path = out_dir / "book_report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Generated {json_path} and {md_path}")


if __name__ == "__main__":
    main()
