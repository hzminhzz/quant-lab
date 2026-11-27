"""T20 / #106 — contract lifecycle registry, exploratory fence, signed-origin admission.

Everything here is local and synthetic.  No production contract, dataset, registry, signing
state or archive is touched: every case writes its own ``tmp_path`` repo, its own manifests
and its own SQLite copy.  The shipped lifecycle document is read, never written.

Scope of the evidence:

* a **blocking** lifecycle status refuses a new hypothesis (CLI + ``advance`` auto-register)
  and leaves historical trials, audit reads and v1 ``spec_hash`` values untouched;
* the exploratory fence exits 0 only when every file is owned by exactly one manifest and
  its last bar strictly precedes the owning contract's ``holdout_start``;
* ``warden_evidence.data_admission`` is invoked from the provenance path and is authoritative
  for v2 market contracts, while v1 evidence is explicitly grandfathered;
* the three replacement fixtures declare the inherited selection group, and a v2 contract
  inherits the legacy DSR floor through recorded trials (no number is copied);
* the six legacy scripts are declared ``pending_replacement`` and are **not** archived —
  archival needs a confirmed replacement on the host.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
import yaml

from warden import lifecycle as lifecycle_module
from warden.cli import main as cli_main
from warden.contract_source import all_contract_paths
from warden.contracts import (
    ContractPolicy,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
)
from warden.lifecycle import (
    ContractLifecycleBlocked,
    ContractLifecycleError,
    load_lifecycle_registry,
)
from warden.market_data import read_parquet_bytes
from warden.provenance import resolve_dataset
from warden.registry import ResearchRegistry
from warden_evidence.exploratory_fence import (
    AMBIGUOUS,
    BEYOND_HOLDOUT,
    DIGEST_MISMATCH,
    NO_CONTRACT,
    UNOWNED,
    ExploratoryFenceError,
    audit_exploratory_storage,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p2_t20"
LIFECYCLE_REL = Path("contracts") / "lifecycle" / "contract_lifecycle.json"
START = datetime(2025, 1, 1, tzinfo=UTC)

DEPRECATED = {
    "INDICES_1H_V1",
    "FX_MAJORS_1H_V1",
    "FX_MAJORS_1D_V1",
    "FX_MAJORS_30M_V1",
    "FX_MAJORS_15M_V1",
    "FX_MAJORS_4H_V1",
    "FX_MAJORS_5M_V1",
    "FX_MAJORS_1M_V1",
    "CRYPTO_PERPS_1H_V1",
    "METALS_1M_V1",
}
SURVIVORSHIP = {
    "CRYPTO_MAJORS_1H_V1",
    "CRYPTO_PERPS_TOP100_1H_V1",
    "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1",
}
REPLACEMENTS = {
    "CRYPTO_PERPS_BROAD_1H_V2": "CRYPTO_PERPS_1H_V1",
    "FX_MAJORS_1D_NY_CLOSE_V2": "FX_MAJORS_1D_V1",
    "CME_ES_NQ_15M_SYNCSTER_V2": "INDICES_1H_V1",
}
LEGACY_SCRIPTS = {
    "scripts/salvage_crypto.py",
    "scripts/salvage_crypto_perps.py",
    "scripts/salvage_multi_asset.py",
    "scripts/build_crypto_perps_broad_1h.py",
    "scripts/build_fx_majors_1d_v2.py",
    "scripts/prepare_syncster_cme.py",
}


# --------------------------------------------------------------------------- helpers


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _panel(n_bars: int = 24, *, assets: tuple[str, ...] = ("AAA", "BBB")) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(5)
    for asset, base in zip(assets, (100.0, 50.0, 25.0)):
        price = base
        for t in range(n_bars):
            close = price * (1.0 + float(rng.normal(0.0, 0.002)))
            rows.append(
                {
                    "timestamp": START + timedelta(hours=t),
                    "asset": asset,
                    "open": price,
                    "high": max(price, close) * 1.001,
                    "low": min(price, close) * 0.999,
                    "close": close,
                    "volume": 10.0,
                }
            )
            price = close
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _contract(
    contract_id: str,
    *,
    spec_version: int = 1,
    group: str = "",
    inherit: str = "",
    exploratory: str = "data/exploratory/panel.parquet",
    holdout_start: datetime = START + timedelta(hours=24),
    provenance: str = "verified_market",
    snapshot: str = "0" * 64,
    asset_class: str = "crypto_perps",
    frequency: str = "1h",
    bars_per_year: int = 8760,
    refit: str = "purged_expanding",
) -> EvaluationContract:
    policy = None
    if spec_version == 2:
        policy = ContractPolicy(
            spec_version=2,
            evidence_tier="synthetic",
            selection_group_id=group,
            inherits_selection_from=inherit,
            holdout_refit_mode=refit,
        )
    return EvaluationContract(
        contract_id=contract_id,
        asset_class=asset_class,
        frequency=frequency,
        bars_per_year=bars_per_year,
        exploratory_data_path=exploratory,
        holdout_data_path="data/holdout/panel_holdout.parquet",
        train_start=START - timedelta(days=30),
        val_start=START + timedelta(hours=1),
        val_end=START + timedelta(hours=23),
        holdout_start=holdout_start,
        holdout_end=holdout_start + timedelta(days=2),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        data_snapshot_sha256=snapshot,
        data_provenance=provenance,
        policy=policy,
    )


def _write_lifecycle(repo: Path, entries: list[dict[str, Any]]) -> Path:
    target = repo / LIFECYCLE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "note": "fixture",
                "contracts": entries,
                "legacy_scripts": [],
            }
        ),
        encoding="utf-8",
    )
    return target


def _blocked_entry(contract_id: str, status: str = "DEPRECATED_UNEXECUTABLE") -> dict[str, Any]:
    return {"contract_id": contract_id, "status": status, "reason": "fixture closure"}


def _write_repo(
    tmp_path: Path,
    *,
    name: str,
    contract: EvaluationContract | Callable[[str], EvaluationContract],
    panel: pl.DataFrame | None = None,
    provenance: str | None = None,
    manifest: bool = True,
    lifecycle: list[dict[str, Any]] | None = None,
    generate: bool = False,
) -> Path:
    """A self-contained repo: contract YAML, dataset manifest, panel, registry."""
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / name
    for rel in (
        "contracts/datasets",
        "registry",
        ".workspace/hypotheses",
        "experiments/active",
        "strategies/candidates",
        "cache/features",
        "src/warden",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# t20 fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t20'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    frame = _panel() if panel is None else panel
    probe_rel = "data/exploratory/panel.parquet"
    data_path = repo / probe_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(data_path)
    digest = _sha(data_path)
    if callable(contract):
        contract = contract(digest)
    if contract.exploratory_data_path != probe_rel:
        target = repo / contract.exploratory_data_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data_path.read_bytes())
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.to_dict()), encoding="utf-8"
    )
    if manifest:
        (repo / "contracts" / "datasets" / f"{contract.contract_id}.json").write_text(
            json.dumps(
                {
                    "contract_id": contract.contract_id,
                    "path": contract.exploratory_data_path,
                    "sha256": digest,
                    "provenance": provenance or contract.data_provenance,
                    "source": "t20_fixture",
                }
            ),
            encoding="utf-8",
        )
    if lifecycle is not None:
        _write_lifecycle(repo, lifecycle)
    ResearchRegistry(repo / "registry" / "registry.db").register_contract(contract)
    if generate:
        generate_manifest(repo)
    return repo


def _hypothesis(contract_id: str, hypothesis_id: str = "HYP-T20-001") -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_id,
        contract_id,
        "T20 lifecycle fixture",
        "rules_ts",
        "Mechanism explanation of twenty chars",
        "Friction explanation of twenty chars",
        8,
        "Reject net Sharpe below one",
    )


def _write_hypothesis_file(repo: Path, contract_id: str, hypothesis_id: str) -> Path:
    path = repo / ".workspace" / "hypotheses" / f"{hypothesis_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    hyp = _hypothesis(contract_id, hypothesis_id)
    path.write_text(
        yaml.safe_dump(
            {
                "hypothesis_id": hyp.hypothesis_id,
                "contract_id": hyp.contract_id,
                "title": hyp.title,
                "strategy_family": hyp.strategy_family,
                "economic_mechanism": hyp.economic_mechanism,
                "counterparty_or_friction": hyp.counterparty_or_friction,
                "expected_holding_bars": hyp.expected_holding_bars,
                "falsification_criteria": hyp.falsification_criteria,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_spec(repo: Path, contract_id: str, hypothesis_id: str) -> Path:
    spec = repo / "experiments" / "active" / f"{hypothesis_id.lower()}.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract_id,
                "hypothesis_id": hypothesis_id,
                "strategy_name": "T20Strat",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": "strategies/candidates/t20.py",
                "parameters": {},
                "param_grid": [{"fast_span": 5}, {"fast_span": 7}],
            }
        ),
        encoding="utf-8",
    )
    return spec


def _result(run_id: str, hypothesis_id: str, contract_id: str, *, keff: float) -> ExperimentResult:
    return ExperimentResult(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        contract_id=contract_id,
        strategy_name="T20Strat",
        strategy_family="rules_ts",
        config_hash="cfg",
        stage_reached="STAGE_3_STRESS",
        verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.0,
        net_sharpe=1.0,
        deflated_sharpe_prob=0.99,
        expected_max_sharpe_noise=0.1,
        total_trials_n=1,
        effective_trials_keff=keff,
        mean_ic=None,
        ic_hac_tstat=None,
        hit_rate=0.55,
        profit_factor=1.3,
        max_drawdown_pct=4.0,
        annualized_turnover=25.0,
        breakeven_cost_bps=12.0,
        stress_2x_cost_sharpe=0.9,
        param_neighbor_stability=None,
        notes="fixture",
        data_snapshot_sha256="0" * 64,
        strategy_module="strategies/candidates/t20.py",
        model_type="rules",
        data_provenance="verified_market",
    )


def _rows(repo: Path, table: str) -> int:
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _tree(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (_sha(path), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------- 1. registry document


def test_shipped_registry_closes_exactly_the_declared_legacy_contracts() -> None:
    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    assert set(registry.blocking_entries()) == DEPRECATED | SURVIVORSHIP
    by_status: dict[str, set[str]] = {}
    for entry in registry.entries.values():
        if entry.blocking:
            by_status.setdefault(entry.status, set()).add(entry.contract_id)
    assert by_status["DEPRECATED_UNEXECUTABLE"] == DEPRECATED
    assert by_status["LEGACY_SURVIVORSHIP_BIASED"] == SURVIVORSHIP
    assert set(registry.replacements()) == set(REPLACEMENTS)

    # Every closed contract is a real, loadable contract document in this repo.
    from warden.contracts import load_contract

    present = {load_contract(p).contract_id for p in all_contract_paths(REPO_ROOT)}
    assert DEPRECATED | SURVIVORSHIP <= present
    # The replacement ids are new: they must not silently re-use a v1 contract id.
    assert not (set(REPLACEMENTS) & present)

    for successor_id, legacy_id in REPLACEMENTS.items():
        successor = registry.entry(successor_id)
        legacy = registry.entry(legacy_id)
        assert successor is not None and successor.status == "ACTIVE_V2"
        assert legacy is not None and legacy.replacement_contract_id == successor_id
        assert successor.selection_group_id == legacy.selection_group_id
        assert registry.dsr_inheritance(legacy_id) == {
            "selection_group_id": legacy.selection_group_id,
            "inherits_selection_from": legacy_id,
        }


def test_registry_document_is_read_only_and_survives_use() -> None:
    target = REPO_ROOT / LIFECYCLE_REL
    before = (_sha(target), target.stat().st_mtime_ns)
    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    for contract_id, entry in registry.entries.items():
        if entry.blocking:
            with pytest.raises(ContractLifecycleBlocked):
                registry.assert_open(contract_id)
        else:
            registry.assert_open(contract_id)
        registry.dsr_inheritance(contract_id)
    registry.blocking_reason(next(iter(registry.blocking_entries())))
    assert (_sha(target), target.stat().st_mtime_ns) == before
    # No writer exists on the module at all: status changes are a reviewed file edit.
    assert not [
        name
        for name in ("save", "save_registry", "write", "dump", "update", "record", "append")
        if hasattr(lifecycle_module, name)
    ]


def test_absent_registry_is_reported_and_strict_mode_refuses(tmp_path: Path) -> None:
    absent = load_lifecycle_registry(tmp_path)
    assert absent.source == "absent" and absent.entries == {}
    assert absent.assert_open("ANYTHING_V1") is None
    with pytest.raises(ContractLifecycleError, match="is missing"):
        load_lifecycle_registry(tmp_path, strict=True)


@pytest.mark.parametrize(
    "payload, message",
    [
        ("{not json", "not valid JSON"),
        (json.dumps({"schema_version": 1, "contracts": [{"contract_id": "X_V1"}]}), "status"),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "contracts": [
                        {"contract_id": "X_V1", "status": "DEPRECATED_UNEXECUTABLE", "reason": "a"},
                        {"contract_id": "X_V1", "status": "DEPRECATED_UNEXECUTABLE", "reason": "b"},
                    ],
                }
            ),
            "twice",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "contracts": [{"contract_id": "X_V1", "status": "DEPRECATED_UNEXECUTABLE",
                                   "reason": "a", "evidence": "x", "extra": 1}],
                }
            ),
            "unknown keys",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "contracts": [{"contract_id": "X_V1", "status": "PROBABLY_FINE", "reason": "a"}],
                }
            ),
            "unknown",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "contracts": [
                        {"contract_id": "X_V1", "status": "DEPRECATED_UNEXECUTABLE", "reason": "a",
                         "replacement_contract_id": "X_V2"},
                        {"contract_id": "X_V2", "status": "DEPRECATED_UNEXECUTABLE", "reason": "b",
                         "selection_group_id": "G"},
                    ],
                }
            ),
            "ACTIVE_V2",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "contracts": [
                        {"contract_id": "X_V1", "status": "DEPRECATED_UNEXECUTABLE", "reason": "a",
                         "replacement_contract_id": "MISSING_V2"},
                    ],
                }
            ),
            "does not declare",
        ),
        (json.dumps({"schema_version": 2, "contracts": []}), "schema_version"),
        (json.dumps({"schema_version": 1, "contracts": [], "unknown": 1}), "unknown keys"),
    ],
)
def test_a_corrupt_registry_fails_closed(tmp_path: Path, payload: str, message: str) -> None:
    target = tmp_path / LIFECYCLE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    with pytest.raises(ContractLifecycleError, match=message):
        load_lifecycle_registry(tmp_path)


def test_dsr_inheritance_is_returned_only_for_a_declared_group() -> None:
    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    assert registry.dsr_inheritance("NOT_A_CONTRACT_V1") == {}
    entry = registry.entry("CRYPTO_PERPS_1H_V1")
    assert entry is not None
    assert registry.dsr_inheritance("CRYPTO_PERPS_1H_V1")["selection_group_id"] == entry.selection_group_id


def test_blocking_message_names_status_and_successor() -> None:
    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    with pytest.raises(ContractLifecycleBlocked) as caught:
        registry.assert_open("CRYPTO_PERPS_1H_V1")
    text = str(caught.value)
    assert "DEPRECATED_UNEXECUTABLE" in text
    assert "CRYPTO_PERPS_BROAD_1H_V2" in text


# --------------------------------------------------------------------------- 2. new-hypothesis gate


@pytest.mark.parametrize(
    "contract_id, status",
    [
        ("CRYPTO_PERPS_1H_V1", "DEPRECATED_UNEXECUTABLE"),
        ("CRYPTO_MAJORS_1H_V1", "LEGACY_SURVIVORSHIP_BIASED"),
    ],
)
def test_register_hypothesis_refuses_a_closed_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contract_id: str,
    status: str,
) -> None:
    contract = _contract(contract_id)
    repo = _write_repo(
        tmp_path,
        name=f"blocked_{status.lower()}",
        contract=contract,
        lifecycle=[_blocked_entry(contract_id, status)],
        generate=True,
    )
    hyp_file = _write_hypothesis_file(repo, contract_id, "HYP-T20-BLOCKED")
    monkeypatch.chdir(repo)
    code = cli_main(["register-hypothesis", str(hyp_file), "--json"])
    err = capsys.readouterr().err
    assert code == 2
    assert "REFUSED" in err and status in err
    assert _rows(repo, "hypotheses") == 0
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM hypotheses WHERE hypothesis_id=?",
                            ("HYP-T20-BLOCKED",)).fetchone()[0] == 0


def test_register_hypothesis_still_accepts_a_contract_the_registry_does_not_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract("T20_OPEN_V1")
    repo = _write_repo(
        tmp_path,
        name="open_contract",
        contract=contract,
        lifecycle=[_blocked_entry("CRYPTO_PERPS_1H_V1")],
        generate=True,
    )
    hyp_file = _write_hypothesis_file(repo, "T20_OPEN_V1", "HYP-T20-OPEN")
    monkeypatch.chdir(repo)
    assert cli_main(["register-hypothesis", str(hyp_file), "--json"]) == 0
    assert _rows(repo, "hypotheses") == 1


def test_advance_refuses_to_auto_register_onto_a_closed_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract_id = "CRYPTO_PERPS_1H_V1"
    contract = _contract(contract_id)
    repo = _write_repo(
        tmp_path,
        name="advance_blocked",
        contract=contract,
        lifecycle=[_blocked_entry(contract_id)],
        generate=True,
    )
    # No hypothesis row: advance's auto-registration path is the one under test.
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        conn.execute("DELETE FROM hypotheses")
    _write_spec(repo, contract_id, "HYP-T20-ADV")
    hyp_file = _write_hypothesis_file(repo, contract_id, "HYP-T20-ADV")
    monkeypatch.chdir(repo)
    code = cli_main(["advance", "experiments/active/hyp-t20-adv.yaml", "--json"])
    assert code == 1
    assert _rows(repo, "hypotheses") == 0
    assert hyp_file.is_file()


# --------------------------------------------------------------------------- 3. history preserved


def test_a_closed_contracts_history_still_reads_with_an_unchanged_v1_spec_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    contract_id = "CRYPTO_PERPS_1H_V1"
    contract = _contract(contract_id)
    repo = _write_repo(
        tmp_path,
        name="history",
        contract=contract,
        lifecycle=[_blocked_entry(contract_id)],
        generate=True,
    )
    registry = ResearchRegistry(repo / "registry" / "registry.db")
    registry.register_hypothesis(_hypothesis(contract_id, "HYP-T20-HIST"))
    registry.record_trial(
        _result("RUN-T20-HIST", "HYP-T20-HIST", contract_id, keff=2.0),
        {"git_commit_sha": "fixture"}, np.zeros(48),
    )
    before = contract.spec_hash()
    assert _rows(repo, "trials") == 1

    monkeypatch.chdir(repo)
    capsys.readouterr()
    assert cli_main(["status", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["value"]["trials_count"] == 1
    assert status_payload["value"]["hypotheses_count"] == 1
    assert status_payload["value"]["recent_trials"][0]["run_id"] == "RUN-T20-HIST"
    # The lifecycle document is present and closes the contract, and the read still works.
    assert load_lifecycle_registry(Path(".")).blocking_reason(contract_id)
    # A lifecycle read never moves the v1 digest: it is not part of spec_hash_payload().
    reloaded = load_lifecycle_registry(Path("."))
    reloaded.assert_open("T20_OTHER_V1")
    from warden.contracts import load_contract

    assert load_contract(repo / "contracts" / f"{contract_id.lower()}.yaml").spec_hash() == before
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        row = conn.execute("SELECT verdict,effective_trials_keff FROM trials WHERE run_id=?",
                           ("RUN-T20-HIST",)).fetchone()
    assert row == ("CANDIDATE_FOR_QUALIFICATION", 2.0)


def test_read_commands_do_not_consult_the_lifecycle_registry() -> None:
    from warden import cli as cli_module

    for name in ("cmd_status", "cmd_spec_status", "cmd_verify_causality", "cmd_audit_check"):
        source = inspect.getsource(getattr(cli_module, name))
        assert "lifecycle" not in source, f"{name} must not gate a read on lifecycle state"


# --------------------------------------------------------------------------- 4. exploratory fence


def _fence_repo(tmp_path: Path, *, extra_owned: bool = False) -> tuple[Path, EvaluationContract]:
    contract = _contract("T20_FENCE_V1", exploratory="data/exploratory/panel.parquet")
    repo = _write_repo(tmp_path, name="fence", contract=contract, generate=False)
    if extra_owned:
        frame = _panel(12).with_columns(pl.col("asset") + "_B")
        second = repo / "data" / "exploratory" / "second.parquet"
        frame.write_parquet(second)
        (repo / "contracts" / "datasets" / "T20_FENCE_SECOND_V1.json").write_text(
            json.dumps(
                {
                    "contract_id": "T20_FENCE_V1",
                    "path": "data/exploratory/second.parquet",
                    "sha256": _sha(second),
                    "provenance": "verified_market",
                    "source": "fixture",
                }
            ),
            encoding="utf-8",
        )
    return repo, contract


def test_fence_passes_when_every_file_is_owned_and_precedes_holdout(tmp_path: Path) -> None:
    repo, _ = _fence_repo(tmp_path, extra_owned=True)
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.ok, report.to_dict()
    assert len(report.files) == 2
    assert {item.state for item in report.files} == {"fenced"}
    assert all(item.max_timestamp and item.holdout_start for item in report.files)


def test_fence_refuses_an_unowned_file(tmp_path: Path) -> None:
    repo, _ = _fence_repo(tmp_path)
    stray = repo / "data" / "exploratory" / "stray.parquet"
    _panel(6).write_parquet(stray)
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert not report.ok
    assert [(item.path, item.state) for item in report.failures] == [
        ("data/exploratory/stray.parquet", UNOWNED)
    ]


def test_fence_refuses_a_bar_at_or_after_holdout_start(tmp_path: Path) -> None:
    repo, contract = _fence_repo(tmp_path)
    # Rewrite the panel so its last bar is exactly holdout_start (24 hourly bars).
    frame = _panel(25)
    frame.write_parquet(repo / contract.exploratory_data_path)
    manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    record = json.loads(manifest.read_text())
    record["sha256"] = _sha(repo / contract.exploratory_data_path)
    manifest.write_text(json.dumps(record))
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert not report.ok
    assert report.failures[0].state == BEYOND_HOLDOUT
    assert contract.holdout_start.isoformat() in report.failures[0].reason


def test_fence_refuses_a_digest_mismatch(tmp_path: Path) -> None:
    repo, contract = _fence_repo(tmp_path)
    manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    record = json.loads(manifest.read_text())
    record["sha256"] = "f" * 64
    manifest.write_text(json.dumps(record))
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.failures[0].state == DIGEST_MISMATCH


def test_fence_refuses_two_manifests_owning_one_path(tmp_path: Path) -> None:
    repo, contract = _fence_repo(tmp_path)
    manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    duplicate = json.loads(manifest.read_text())
    duplicate["contract_id"] = "T20_FENCE_OTHER_V1"
    (repo / "contracts" / "datasets" / "T20_FENCE_OTHER_V1.json").write_text(json.dumps(duplicate))
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.failures[0].state == AMBIGUOUS


def test_fence_refuses_a_manifest_without_its_contract(tmp_path: Path) -> None:
    repo, contract = _fence_repo(tmp_path)
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").unlink()
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.failures[0].state == NO_CONTRACT


def test_fence_refuses_an_empty_store(tmp_path: Path) -> None:
    contract = _contract("T20_EMPTY_V1")
    repo = tmp_path / "empty"
    (repo / "data" / "exploratory").mkdir(parents=True)
    (repo / "contracts" / "datasets").mkdir(parents=True)
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.to_dict()), encoding="utf-8"
    )
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.files == () and not report.ok
    assert "empty store" in report.reasons[0]


def test_fence_refuses_the_holdout_root_and_paths_outside_data(tmp_path: Path) -> None:
    repo, _ = _fence_repo(tmp_path)
    with pytest.raises(ExploratoryFenceError, match="sealed holdout"):
        audit_exploratory_storage(repo, dataset_dir="data/holdout", parquet_reader=read_parquet_bytes)
    with pytest.raises(ExploratoryFenceError, match="under 'data/'"):
        audit_exploratory_storage(repo, dataset_dir="contracts", parquet_reader=read_parquet_bytes)
    with pytest.raises(ExploratoryFenceError, match="does not exist"):
        audit_exploratory_storage(repo, dataset_dir="data/nowhere", parquet_reader=read_parquet_bytes)


def test_fence_is_read_only(tmp_path: Path) -> None:
    repo, _ = _fence_repo(tmp_path, extra_owned=True)
    before = _tree(repo)
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.ok
    assert _tree(repo) == before


def test_fence_cli_exit_codes_and_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from warden.policy import authorize

    assert authorize("data fence").allow and not authorize("data fence").approval_required
    assert authorize("data seal-holdout").approval_required

    repo, _ = _fence_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert cli_main(["data", "fence", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["value"]["files_checked"] == 1

    (repo / "data" / "exploratory" / "stray.parquet").write_bytes(b"not really parquet")
    monkeypatch.chdir(repo)
    assert cli_main(["data", "fence", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["value"]["failures"][0]["state"] == UNOWNED


# --------------------------------------------------------------------------- 5. signed-origin admission


def _attestation(repo: Path, contract: EvaluationContract, key: bytes) -> Path:
    row = {
        "schema": 1,
        "contract_id": contract.contract_id,
        "source_id": "fixture-vendor",
        "data_sha256": _sha(repo / contract.exploratory_data_path),
        "raw_source_sha256": "1" * 64,
        "observed_at": "2025-01-01T00:00:00Z",
    }
    payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    row["signature"] = hmac.new(key, payload, hashlib.sha256).hexdigest()
    target = repo / "contracts" / "attestations" / f"{contract.contract_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(row), encoding="utf-8")
    return target


def _market_repo(tmp_path: Path, name: str, *, spec_version: int) -> tuple[Path, EvaluationContract]:
    def build(digest: str) -> EvaluationContract:
        return _contract(
            "T20_ADMIT_V2" if spec_version == 2 else "T20_ADMIT_V1",
            spec_version=spec_version,
            group="LEGACY_CRYPTO_PERPS_1H" if spec_version == 2 else "",
            inherit="CRYPTO_PERPS_1H_V1" if spec_version == 2 else "",
            provenance="verified_market",
            snapshot=digest,
            exploratory="data/exploratory/panel.parquet",
        )

    repo = _write_repo(tmp_path, name=name, contract=build)
    from warden.contracts import load_contract

    return repo, load_contract(repo / "contracts" / f"{'t20_admit_v2' if spec_version == 2 else 't20_admit_v1'}.yaml")


def test_v2_market_dataset_without_a_signed_origin_is_not_market_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WARDEN_ADMISSION_HMAC_KEY", raising=False)
    repo, contract = _market_repo(tmp_path, "admit_missing", spec_version=2)
    resolution = resolve_dataset(contract, repo)
    assert resolution.eligible_market is False
    assert resolution.admission_state == "STAGED"
    assert "market admission refused" in resolution.reason
    assert resolution.sha256 == _sha(repo / contract.exploratory_data_path)


def test_v2_market_dataset_with_a_signed_origin_is_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = b"fixture-only-key-not-production"
    monkeypatch.setenv("WARDEN_ADMISSION_HMAC_KEY", key.hex())
    repo, contract = _market_repo(tmp_path, "admit_signed", spec_version=2)
    _attestation(repo, contract, key)
    resolution = resolve_dataset(contract, repo)
    assert resolution.eligible_market is True, resolution.to_dict()
    assert resolution.admission_state == "CONTRACT_ADMITTED"


def test_v2_market_dataset_with_the_wrong_key_stays_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract = _market_repo(tmp_path, "admit_wrong_key", spec_version=2)
    _attestation(repo, contract, b"the-real-key")
    monkeypatch.setenv("WARDEN_ADMISSION_HMAC_KEY", b"a-different-key".hex())
    resolution = resolve_dataset(contract, repo)
    assert resolution.eligible_market is False
    assert resolution.admission_state == "STAGED"


def test_v1_market_dataset_keeps_its_historical_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WARDEN_ADMISSION_HMAC_KEY", raising=False)
    repo, contract = _market_repo(tmp_path, "admit_legacy", spec_version=1)
    resolution = resolve_dataset(contract, repo)
    assert resolution.eligible_market is True
    assert resolution.admission_state == "legacy_grandfathered"
    assert "not re-adjudicated" in resolution.admission_reason


def test_admission_is_invoked_from_the_provenance_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, bool]] = []

    from warden_evidence import data_admission

    original = data_admission.classify_recovered_dataset

    def spy(contract, repo_root, attestation=None, verifier_key=None):
        calls.append((contract.contract_id, str(attestation), verifier_key is not None))
        return original(contract, repo_root, attestation, verifier_key)

    monkeypatch.setattr(data_admission, "classify_recovered_dataset", spy)
    key = b"fixture-only-key-not-production"
    monkeypatch.setenv("WARDEN_ADMISSION_HMAC_KEY", key.hex())
    repo, contract = _market_repo(tmp_path, "admit_spy", spec_version=2)
    attestation = _attestation(repo, contract, key)
    resolve_dataset(contract, repo)
    assert calls == [(contract.contract_id, str(attestation), True)]

    # A synthetic dataset is not market data, so admission is never consulted for it.
    calls.clear()
    synthetic = _contract("T20_SYNTH_V1", provenance="synthetic")
    synth_repo = _write_repo(tmp_path, name="admit_synthetic", contract=synthetic, provenance="synthetic")
    resolution = resolve_dataset(synthetic, synth_repo)
    assert resolution.eligible_market is False and resolution.admission_state == ""
    assert calls == []


# --------------------------------------------------------------------------- 6. replacement rehearsal


def test_replacement_fixture_contracts_declare_the_inherited_group() -> None:
    from warden.contracts import load_contract

    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    loaded = {
        contract.contract_id: contract
        for contract in (load_contract(p) for p in sorted((FIXTURES / "contracts").glob("*.yaml")))
    }
    assert set(loaded) == set(REPLACEMENTS)
    for contract_id, legacy_id in REPLACEMENTS.items():
        contract = loaded[contract_id]
        policy = contract.governance
        assert policy.spec_version == 2
        inheritance = registry.dsr_inheritance(legacy_id)
        assert policy.selection_group_id == inheritance["selection_group_id"]
        assert policy.inherits_selection_from == inheritance["inherits_selection_from"]
        assert policy.dataset_id == "" and policy.dataset_manifest_sha256 == ""  # host pins these
        # The replacement's declared exploratory path is the builder's write target.
        assert contract.exploratory_data_path == (
            f"data/exploratory/{_spec_for(contract_id).dataset_id}.parquet"
        )


def _spec_for(contract_id: str):
    from warden_evidence.dataset_builder import load_dataset_spec

    names = {
        "CRYPTO_PERPS_BROAD_1H_V2": "crypto_perps_broad_1h_v2.yaml",
        "FX_MAJORS_1D_NY_CLOSE_V2": "fx_majors_1d_ny_close_v2.yaml",
        "CME_ES_NQ_15M_SYNCSTER_V2": "cme_es_nq_15m_syncster_v2.yaml",
    }
    return load_dataset_spec(FIXTURES / "specs" / names[contract_id])


def test_replacement_specs_parse_and_keep_their_safety_declarations() -> None:
    crypto, fx, cme = (_spec_for(cid) for cid in REPLACEMENTS)
    assert crypto.source.adapter == "binance_um_perp"
    assert crypto.universe.retain_delisted is True
    assert crypto.funding is not None and crypto.funding.missing_policy == "contract_fallback"
    assert crypto.funding.fallback_rate not in (None, 0.0)
    assert fx.source.adapter == "dukascopy_local"
    assert fx.time_contract.daily_roll_anchor == "17:00 America/New_York"
    assert fx.volume_semantics == "tick_count_proxy"
    assert cme.source.kind == "composite_union" and len(cme.source.inputs) == 2
    assert all(item.format == "parquet" for item in cme.source.inputs.values())


def test_v2_replacement_inherits_the_legacy_dsr_floor(tmp_path: Path) -> None:
    from warden.contracts import load_contract

    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    repo = tmp_path / "floor"
    (repo / "contracts").mkdir(parents=True)
    legacy_id = "CRYPTO_PERPS_1H_V1"
    replacement = load_contract(FIXTURES / "contracts" / "crypto_perps_broad_1h_v2.yaml")
    # A sibling that declares a different group must NOT inherit the floor.
    sibling = EvaluationContract.from_dict(
        {
            **replacement.to_dict(),
            "contract_id": "T20_SIBLING_V2",
            "selection_group_id": "T20_OTHER_GROUP",
            # A different search problem: it must not reach the legacy 1h trials.
            "inherits_selection_from": "",
        }
    )
    legacy = _contract(legacy_id)
    store = ResearchRegistry(repo / "registry" / "registry.db")
    store.register_contract(legacy)
    store.register_hypothesis(_hypothesis(legacy_id, "HYP-T20-FLOOR"))
    store.record_trial(
        _result("RUN-T20-FLOOR", "HYP-T20-FLOOR", legacy_id, keff=3.5),
        {"git_commit_sha": "fixture"}, np.zeros(48),
    )
    store.register_contract(replacement)
    store.register_contract(sibling)

    inherited_k, _inherited_noise, inherited_family = store.prior_search_floor(replacement)
    assert inherited_k >= 3.5
    assert inherited_family == registry.dsr_inheritance(legacy_id)["selection_group_id"]
    other_k, _, other_family = store.prior_search_floor(sibling)
    assert other_family == "T20_OTHER_GROUP"
    assert other_k < inherited_k


def test_cme_replacement_builds_from_pinned_parents_and_records_lineage(tmp_path: Path) -> None:
    from warden_evidence.dataset_builder import build_dataset

    spec_doc = yaml.safe_load((FIXTURES / "specs" / "cme_es_nq_15m_syncster_v2.yaml").read_text())
    # A fixture build uses a two-day window: the shape is what is under test, not a year of
    # synthetic bars.  Bounds stay 15m grid-aligned.
    spec_doc["time_window"] = {
        "start_inclusive": "2024-01-01T00:00:00Z",
        "exploratory_end_exclusive": "2024-01-02T00:00:00Z",
        "holdout_start_inclusive": "2024-01-02T00:00:00Z",
        "holdout_end_exclusive": "2024-01-03T00:00:00Z",
    }

    repo = tmp_path / "build"
    (repo / "parents").mkdir(parents=True)
    (repo / "contracts" / "datasets").mkdir(parents=True)
    (repo / "data" / "exploratory").mkdir(parents=True)
    stamps = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i) for i in range(96)]
    for key, asset, base in (("es", "ES", 4500.0), ("nq", "NQ", 16000.0)):
        frame = pl.DataFrame(
            [
                {
                    "timestamp": stamp,
                    "asset": asset,
                    "open": base + i,
                    "high": base + i + 2,
                    "low": base + i - 2,
                    "close": base + i + 1,
                    "volume": 120.0,
                }
                for i, stamp in enumerate(stamps)
            ]
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        path = repo / "parents" / f"cme_{key}_15m.parquet"
        frame.write_parquet(path)
        spec_doc["source"]["inputs"][key]["sha256"] = _sha(path)
        spec_doc["source"]["inputs"][key]["path"] = f"parents/cme_{key}_15m.parquet"

    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec_doc), encoding="utf-8")
    from warden_evidence.dataset_builder import load_dataset_spec

    spec = load_dataset_spec(spec_path)
    outcome = build_dataset(
        spec,
        repo_root=repo,
        source_root=repo,
        dataset_dir="data/exploratory",
        manifest_dir="contracts/datasets",
        parquet_reader=read_parquet_bytes,
    )
    assert outcome.manifest["lineage"]["kind"] == "composite_union"
    parents = outcome.manifest["lineage"]["parents"]
    assert [parent["key"] for parent in parents] == ["es", "nq"]
    assert all(parent["verified"] for parent in parents)
    assert outcome.manifest["dataset"]["rows"] == 192
    built = repo / "data" / "exploratory" / f"{spec.dataset_id}.parquet"
    assert built.is_file()
    contract_path = _spec_for("CME_ES_NQ_15M_SYNCSTER_V2")
    assert built.name == Path(
        f"{contract_path.dataset_id}.parquet"
    ).name  # the contract pins exactly this file name

    # A re-exported parent is a pin mismatch, never a silent substitution.
    tampered = pl.read_parquet(repo / "parents" / "cme_es_15m.parquet").head(10)
    tampered.write_parquet(repo / "parents" / "cme_es_15m.parquet")
    built.unlink()
    (repo / "contracts" / "datasets" / f"{spec.dataset_id}.json").unlink()
    with pytest.raises(Exception) as caught:
        build_dataset(
            spec,
            repo_root=repo,
            source_root=repo,
            dataset_dir="data/exploratory",
            manifest_dir="contracts/datasets",
            parquet_reader=read_parquet_bytes,
        )
    assert "pin" in str(caught.value).lower() or "digest" in str(caught.value).lower()


def test_legacy_scripts_are_still_present_and_pending_archive() -> None:
    registry = load_lifecycle_registry(REPO_ROOT, strict=True)
    declared = {item["path"] for item in registry.legacy_scripts}
    assert declared == LEGACY_SCRIPTS
    for item in registry.legacy_scripts:
        assert item["status"] == "pending_replacement"
        assert item["replaces"] in registry.replacements()
        # Nothing is archived on fixture-only evidence: the scripts are still where they were.
        assert (REPO_ROOT / item["path"]).is_file()
    # And the lifecycle document never declares one of them "archived".
    assert all(item["status"] != "archived" for item in registry.legacy_scripts)
