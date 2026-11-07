"""Campaigns reconstruct Warden verdicts after process/report crashes, read-only."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.campaign_projection import project
from scripts.run_carver_daily_campaign import main as campaign_main
from warden.contracts import HypothesisSpec, load_contract
from warden.registry import ResearchRegistry


def _spec(tmp_path: Path, hid: str, cid: str = "CRYPTO_PERPS_1H_V1") -> Path:
    path = tmp_path / f"{hid}.yaml"
    path.write_text(yaml.safe_dump({"hypothesis_id": hid, "contract_id": cid,
                                    "strategy_module": "strategies/candidates/vol_breakout_1h.py"}))
    return path


def _registry(tmp_path: Path) -> ResearchRegistry:
    reg = ResearchRegistry(tmp_path / "registry.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)
    for hid in ("HYP-PASS", "HYP-REJECT", "HYP-INTERRUPTED", "HYP-PENDING"):
        reg.register_hypothesis(HypothesisSpec(
            hid, contract.contract_id, "Campaign", "rules_ts",
            "A causal mechanism whose effect may cross many trading days",
            "Fees, volatility and slippage may erase the entire signal", 8,
            "Reject when the strategy has insufficient executable edge",
        ))
    return reg


def test_screen_record_survives_report_loss_and_never_reruns(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    for name, outcome in (("HYP-PASS", "PASS"), ("HYP-REJECT", "REJECT")):
        reg.record_selection_attempt(hypothesis_id=name, contract_id="CRYPTO_PERPS_1H_V1",
                                     kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome=outcome,
                                     strategy_module="strategies/candidates/vol_breakout_1h.py")
    specs = [_spec(tmp_path, hid) for hid in ("HYP-PASS", "HYP-REJECT", "HYP-PENDING")]
    first = project(specs, reg.db_path)
    second = project(specs, reg.db_path)
    assert first == second
    status = {row["hypothesis_id"]: row["status"] for row in first}
    assert status == {"HYP-PASS": "STAGE1_PASS", "HYP-REJECT": "STAGE1_REJECT",
                      "HYP-PENDING": "PENDING"}
    assert all(x["automatic_replay"] != "ALLOW" for x in first)
    with reg._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 2


def test_report_only_campaign_recovers_without_any_warden_execution(tmp_path: Path, monkeypatch) -> None:
    import json

    import scripts.run_carver_daily_campaign as campaign

    reg = _registry(tmp_path)
    reg.record_selection_attempt(hypothesis_id="HYP-PASS", contract_id="CRYPTO_PERPS_1H_V1",
                                 kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="PASS")
    specs = tmp_path / "specs"
    specs.mkdir()
    _spec(specs, "HYP-PASS")
    _spec(specs, "HYP-PENDING")

    def forbid(*args, **kwargs):
        raise AssertionError("report reconstruction cannot execute any Warden command")

    monkeypatch.setattr(campaign, "run", forbid)
    out = tmp_path / "out"
    opts = ["--report-only", "--registry", str(reg.db_path), "--spec-dir", str(specs),
            "--out-dir", str(out)]
    assert campaign_main(opts) == 0
    first = (out / "campaign_registry_projection.json").read_bytes()
    assert campaign_main(opts) == 0
    assert first == (out / "campaign_registry_projection.json").read_bytes()
    statuses = {x["hypothesis_id"]: x["status"] for x in json.loads(first)["records"]}
    assert statuses == {"HYP-PASS": "STAGE1_PASS", "HYP-PENDING": "PENDING"}


def test_interrupted_trial_and_stale_contract_demand_review(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register_trial_start("HYP-INTERRUPTED", "RUN-INTERRUPTED")
    reg.reconcile_attempt("RUN-INTERRUPTED", confirm_interrupted=True)
    specs = [_spec(tmp_path, "HYP-INTERRUPTED"), _spec(tmp_path, "HYP-PENDING", "WRONG-CONTRACT")]
    status = {x["hypothesis_id"]: x for x in project(specs, reg.db_path)}
    assert status["HYP-INTERRUPTED"]["status"] == "INTERRUPTED_REVIEW"
    assert status["HYP-INTERRUPTED"]["evidence_id"] == "RUN-INTERRUPTED"
    assert status["HYP-PENDING"]["status"] == "REVIEW"
