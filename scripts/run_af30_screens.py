#!/usr/bin/env python3
"""Execute Step 4 of AF30 Campaign: register 20 hypotheses, verify, and screen on MULTI_ASSET_1H_V1."""

import ast
import json
import subprocess
import sys
from pathlib import Path
import yaml

from warden_ops.readonly import ReadOnlyResearchRegistry

CAMPAIGN = [
    (71, "01", "buy_hold_single", "ts_s01_buy_hold_single_1h.py", "Buy & Hold Single", "trend"),
    (72, "02", "buy_hold_risk_scaled", "ts_s02_buy_hold_risk_scaled_1h.py", "Buy & Hold Risk Scaled", "trend"),
    (73, "03", "buy_hold_variable_risk", "ts_s03_buy_hold_variable_risk_1h.py", "Buy & Hold Variable Risk", "trend"),
    (74, "04", "buy_hold_portfolio_scaling", "ts_s04_buy_hold_portfolio_scaling_1h.py", "Buy & Hold Portfolio Scaling", "trend"),
    (75, "05", "slow_trend_long_only", "ts_s05_slow_trend_long_only_1h.py", "Slow Trend Long Only", "trend"),
    (76, "06", "slow_trend_long_short", "ts_s06_slow_trend_long_short_1h.py", "Slow Trend Long Short", "trend"),
    (77, "07", "slow_trend_strength", "ts_s07_slow_trend_strength_1h.py", "Slow Trend Strength", "trend"),
    (78, "08", "fast_trend_strength", "ts_s08_fast_trend_strength_1h.py", "Fast Trend Strength", "trend"),
    (79, "09", "ewmac_multi", "ts_ewmac_multi_1h.py", "Multi-Speed EWMAC", "trend"),
    (80, "12", "adjusted_trend", "ts_s12_adjusted_trend_1h.py", "Adjusted Trend", "trend"),
    (81, "13", "trend_carry_regimes", "ts_s13_trend_carry_regimes_1h.py", "Trend Carry Regimes", "trend"),
    (82, "14", "spot_trend", "ts_s14_spot_trend_1h.py", "Spot Trend", "trend"),
    (83, "17", "normalised_trend", "ts_s17_normalised_trend_1h.py", "Normalised Trend", "trend"),
    (84, "18", "trend_asset_classes", "ts_s18_trend_asset_classes_1h.py", "Trend Asset Classes", "trend"),
    (85, "19", "xs_momentum", "ts_s19_xs_momentum_1h.py", "Cross-Sectional Momentum", "relative-value"),
    (86, "21", "breakout_20", "ts_breakout_20_1h.py", "Donchian Breakout 20", "breakout"),
    (87, "22", "value", "ts_s22_value_1h.py", "Value Reversal", "reversal"),
    (88, "23", "acceleration", "ts_s23_acceleration_1h.py", "Acceleration", "trend"),
    (89, "26", "fast_mean_reversion", "ts_s26_fast_mean_reversion_1h.py", "Fast Mean Reversion", "reversal"),
    (90, "27", "safer_fast_mean_reversion", "ts_s27_safer_fast_mean_reversion_1h.py", "Safer Fast Mean Reversion", "reversal"),
]


def extract_bullets(file_path: Path) -> list[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree) or ""
    bullets = []
    for line in doc.splitlines():
        line = line.strip()
        if line.startswith("-"):
            bullets.append(line.lstrip("-").strip())
    return bullets


def main():
    repo_root = Path(__file__).resolve().parent.parent
    hyp_dir = repo_root / ".workspace" / "hypotheses" / "registered"
    hyp_dir.mkdir(parents=True, exist_ok=True)
    exp_dir = repo_root / "experiments" / "active" / "af30"
    exp_dir.mkdir(parents=True, exist_ok=True)
    out_dir = repo_root / ".workspace" / "af30_campaign"
    out_dir.mkdir(parents=True, exist_ok=True)

    screens_record = []

    for hyp_num, s_num, slug, mod_filename, name, prem_type in CAMPAIGN:
        hyp_id = f"HYP-2026-0{hyp_num}"
        mod_rel_path = f"strategies/candidates/{mod_filename}"
        mod_path = repo_root / mod_rel_path

        bullets = extract_bullets(mod_path)
        bullets_str = "; ".join(bullets)
        if not bullets_str:
            bullets_str = f"AF30 Strategy {s_num} canonical logic as ported"

        if s_num == "13":
            title = "AF30 S13 as ported (trend + vol regime, no carry term) on 17-instrument multi-asset 1h panel"
        else:
            title = f"AF30 S{s_num} {name} on 17-instrument multi-asset 1h panel"

        economic_mechanism = f"AF30 S{s_num}: {bullets_str}"
        counterparty_or_friction = (
            f"Counterparty pays a {prem_type} risk premium; "
            "friction is 1 bp one-way taker+slippage on 17 liquid index, FX, metal and crude "
            "instruments, plus 3xATR-14 trailing-stop whipsaw."
        )
        falsification_criteria = (
            "Stage-1 on MULTI_ASSET_1H_V1 rejects the mechanism when "
            "profit_factor < 1.15 or trade_edge < 2.5x round-trip cost, "
            "or when the 2x-cost stress Sharpe is <= 0 or the +1-bar-latency Sharpe is <= 0."
        )

        hyp_doc = {
            "hypothesis_id": hyp_id,
            "contract_id": "MULTI_ASSET_1H_V1",
            "title": title,
            "strategy_family": "rules_ts",
            "economic_mechanism": economic_mechanism,
            "counterparty_or_friction": counterparty_or_friction,
            "expected_holding_bars": 504,
            "falsification_criteria": falsification_criteria,
            "source_provenance": f"Carver, Advanced Futures Trading Strategies (2022), Strategy {int(s_num)}",
            "llm_model_id": "unspecified",
            "strategy_module": mod_rel_path,
        }
        hyp_path = hyp_dir / f"{hyp_id}.yaml"
        hyp_path.write_text(yaml.safe_dump(hyp_doc, sort_keys=False), encoding="utf-8")

        db_path = repo_root / "registry" / "registry.db"
        already_registered = ReadOnlyResearchRegistry(db_path).read_hypothesis(hyp_id) is not None

        if not already_registered:
            print(f"Registering {hyp_id}...")
            res_reg = subprocess.run(
                ["uv", "run", "warden", "register-hypothesis", str(hyp_path), "--json"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                check=False,
            )
            if res_reg.returncode != 0:
                print(f"ERROR registering {hyp_id}:\n{res_reg.stderr}", file=sys.stderr)
                sys.exit(res_reg.returncode)
        else:
            print(f"Already registered: {hyp_id}")

        screen_yaml_path = exp_dir / f"{s_num}_{slug}_screen.yaml"
        screen_doc = {
            "contract_id": "MULTI_ASSET_1H_V1",
            "hypothesis_id": hyp_id,
            "model_type": "rules",
            "strategy_family": "rules_ts",
            "strategy_module": mod_rel_path,
            "strategy_name": f"AF30_S{s_num}_SCREEN",
            "parameters": {},
        }
        screen_yaml_path.write_text(yaml.safe_dump(screen_doc, sort_keys=False), encoding="utf-8")

        print(f"Verifying {screen_yaml_path.name}...")
        res_ver = subprocess.run(
            ["uv", "run", "warden", "verify", str(screen_yaml_path), "--json"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        if res_ver.returncode != 0:
            print(f"VERIFY FAILED for {screen_yaml_path.name}:\n{res_ver.stderr}\n{res_ver.stdout}", file=sys.stderr)
            sys.exit(1)

        try:
            ver_out = json.loads(res_ver.stdout)
            assert ver_out.get("status") == "PASS", f"Verify status != PASS: {ver_out}"
        except Exception as e:
            print(f"Verify parsing failed for {screen_yaml_path.name}: {e}\nstdout: {res_ver.stdout}")
            sys.exit(1)

        existing_screen = ReadOnlyResearchRegistry(db_path).read_standalone_screen(hyp_id)

        if existing_screen:
            print(f"Screen already recorded for {hyp_id}")
            outcome = existing_screen["outcome"]
            diag_json = existing_screen["diagnostics_json"]
            diag = json.loads(diag_json) if diag_json else {}
            passed = (outcome == "PASS")
            verdict = outcome
            metrics = {k: v for k, v in diag.items() if k != "dataset"}
            exit_code = 0 if passed else 1
            scr_payload = {"passed": passed, "metrics": metrics, "notes": "From existing screen record"}
        else:
            print(f"Screening {screen_yaml_path.name}...")
            res_scr = subprocess.run(
                ["uv", "run", "warden", "screen", str(screen_yaml_path), "--json"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                check=False,
            )
            try:
                scr_payload = json.loads(res_scr.stdout.strip())
            except Exception as e:
                print(f"Failed to parse screen JSON for {screen_yaml_path.name}: {e}\nstdout: {res_scr.stdout}\nstderr: {res_scr.stderr}")
                sys.exit(1)

            metrics = scr_payload.get("metrics", {})
            passed = scr_payload.get("passed", False)
            verdict = "PASS" if passed else "REJECT"
            exit_code = res_scr.returncode

        record_entry = {
            "hypothesis_id": hyp_id,
            "af30_strategy": f"s{s_num}",
            "module": mod_rel_path,
            "strategy_name": f"AF30_S{s_num}_SCREEN",
            "title": title,
            "passed": passed,
            "verdict": verdict,
            "exit_code": exit_code,
            "profit_factor": metrics.get("profit_factor"),
            "conditional_edge": metrics.get("conditional_edge"),
            "trade_edge": metrics.get("trade_edge"),
            "avg_holding_bars": metrics.get("avg_holding_bars"),
            "time_in_market": metrics.get("time_in_market"),
            "bootstrap_p_value": metrics.get("bootstrap_p_value"),
            "metrics": metrics,
            "notes": scr_payload.get("notes", ""),
            "raw_payload": scr_payload,
        }
        screens_record.append(record_entry)
        print(f"  -> {hyp_id} S{s_num}: {verdict} (exit={exit_code}, PF={metrics.get('profit_factor', 0):.2f})")
    screens_json_path = out_dir / "screens.json"
    screens_json_path.write_text(json.dumps(screens_record, indent=2), encoding="utf-8")
    print(f"\nAll 20 screens complete. Written to {screens_json_path}")


if __name__ == "__main__":
    main()
