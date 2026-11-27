"""RG-08: no production holdout bytes are used by these witnesses."""
import hashlib
import hmac
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import yaml

from tests.test_rg05_research_intent import approve, review
from tests.test_rg06_incremental import _control_panel, _long_module
from warden import exposure
from warden.contracts import utc
from warden.qualify import authorize_holdout_batch, describe_holdout_batch, qualify_candidate
from warden_ops.integrity import generate_manifest

TEST_KEY = "rg08-scratch-test-key-only"


def sign(f, monkeypatch):
    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", TEST_KEY)
    generate_manifest(f.repo)
    raw = (f.repo / ".warden_manifest.sha256").read_bytes()
    (f.repo / ".warden_manifest.sha256.sig").write_text(
        hmac.new(TEST_KEY.encode(), raw, hashlib.sha256).hexdigest()
    )


def seal_fixture(f, *, stronger_benchmark=False, scope_incident=False):
    rng = np.random.default_rng(728)
    rows = []
    for asset, mean, std in (
        ("AAA", 0.03 if stronger_benchmark else 0.006, 0.004 if stronger_benchmark else 0.006),
        ("BBB", 0.014, 0.004),
    ):
        price = f.panel.filter(pl.col("asset") == asset)["close"][-1]
        for day in range(155):
            close = price * (1 + rng.normal(mean, std))
            rows.append({
                "timestamp": f.contract.holdout_start + timedelta(days=day),
                "asset": asset, "open": price, "close": close,
                "high": max(price, close) * 1.001, "low": min(price, close) * 0.999,
                "volume": 1000000.0, "funding_rate": 0.0,
            })
            price = close
    if scope_incident:
        rows.append({**rows[-1], "asset": "UNATTESTED"})
    panel = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    path = f.repo / f.contract.holdout_data_path
    path.parent.mkdir(parents=True)
    panel.write_parquet(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = f.repo / f"contracts/datasets/{f.contract.contract_id}.json"
    body = json.loads(manifest.read_text())
    body.update(holdout_path=f.contract.holdout_data_path, holdout_sha256=sha)
    manifest.write_text(json.dumps(body))
    policy = {
        "version": exposure.VERSION,
        "history_attestation": "Only the scratch fixture historical exposures are present.",
        "windows": {f.contract.contract_id: {
            "dataset_sha256": sha,
            "start": f.contract.holdout_start.isoformat(),
            "end": f.contract.holdout_end.isoformat(),
            "symbols": {"AAA": "UNDERLYING-A", "BBB": "UNDERLYING-B"},
        }},
    }
    (f.repo / exposure.POLICY_PATH).write_text(yaml.safe_dump(policy))
    return path


def candidate(tiny_repo, incremental=False, *, monkeypatch=None, stronger_benchmark=False, scope_incident=False):
    end = utc("2025-06-30T00:00:00Z")
    f = tiny_repo(
        module_source=_long_module("BBB"), panel_override=_control_panel(),
        contract_overrides={
            "frequency": "1d", "bars_per_year": 365,
            "val_start": utc("2025-01-21T00:00:00Z"),
            "val_end": end, "holdout_start": end,
            "holdout_end": utc("2025-12-01T00:00:00Z"),
        },
    )
    if monkeypatch is not None:
        seal_fixture(f, stronger_benchmark=stronger_benchmark, scope_incident=scope_incident)
    runner, _, _ = approve(f, incremental=incremental, benchmark_source=_long_module("AAA"))
    review(runner, f)
    if monkeypatch is not None:
        from warden.cli import cmd_verify

        sign(f, monkeypatch)
        monkeypatch.chdir(f.repo)
        assert cmd_verify(SimpleNamespace(spec_yaml=str(f.spec), json=True), runner) == 0
    result = runner.run_spec(f.spec)
    assert result.verdict == "CANDIDATE_FOR_QUALIFICATION"
    if monkeypatch is not None:
        from warden.cli import cmd_audit_check

        assert cmd_audit_check(SimpleNamespace(run_id=result.run_id, json=True), runner) == 0
        assert f.registry.causal_proof_for_run(result.run_id, f.repo)["verified"]
    return f, runner, result.run_id


def test_absolute_candidate_without_frozen_batch_cannot_claim(tiny_repo):
    f, runner, run_id = candidate(tiny_repo)
    with pytest.raises(ValueError, match="authorized frozen"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner,
                          holdout_override_path=f.repo / "never-read.parquet")
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_claims").fetchone()[0] == 0


def freeze(runner, run_id, *, independence=None):
    description = describe_holdout_batch(run_id, runner)
    approval = {"batch_sha256": description["batch_sha256"], "independence": independence or {}}
    return authorize_holdout_batch(
        run_id, runner, approval, "fixture-principal", "YES_AUTHORIZE_HOLDOUT_BATCH")


@pytest.mark.parametrize("incremental,stronger_benchmark,expected", [
    (False, False, "QUALIFIED"),
    (True, False, "QUALIFIED"),
    (True, True, "REJECTED"),
])
def test_signed_batch_real_kernel_and_frozen_comparative_gate(
    tiny_repo, monkeypatch, incremental, stronger_benchmark, expected
):
    f, runner, run_id = candidate(tiny_repo, incremental, monkeypatch=monkeypatch,
                                  stronger_benchmark=stronger_benchmark)
    batch = freeze(runner, run_id)
    batch_id = batch["batch_id"]
    assert exposure.status(f.registry, batch_id)["state"] == "AUTHORIZED"
    result = qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    assert result["absolute_pass"] is True
    assert result["scientific_disposition"] == expected
    assert result["promoted"] == (expected == "QUALIFIED")
    if incremental:
        assert result["comparison"]["phase"] == "holdout"
        assert result["comparison"]["basis"]["dataset_sha256"] == batch["document"]["scope"]["dataset_sha256"]
        assert result["comparison"]["n_observations"] >= 120
        assert result["comparison"]["status"] == ("PASS" if expected == "QUALIFIED" else "REJECT")
    assert exposure.status(f.registry, batch_id)["result"] == result
    assert exposure.status(f.registry, batch_id)["state"] == "FINALIZED"
    with pytest.raises((ValueError, PermissionError)):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)


def add_legacy_scope(f, monkeypatch, *, lineage="HYP-OLD", alias="OLD-BBB", overlapping=True):
    f.registry.reserve_holdout_lineage(lineage, "RUN-HISTORICAL")
    path = f.repo / exposure.POLICY_PATH
    policy = yaml.safe_load(path.read_text())
    start = f.contract.holdout_start if overlapping else utc("2023-01-01T00:00:00Z")
    policy["legacy_scopes"] = {
        "RUN-HISTORICAL": {
            "scope": {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat(),
                      "symbols": {alias: "UNDERLYING-B"}},
            "evidence": "Scratch principal verified the complete historical exposure.",
        },
    }
    path.write_text(yaml.safe_dump(policy))
    sign(f, monkeypatch)


def test_legacy_scope_unknown_and_explicit_independence(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    f.registry.reserve_holdout_lineage("HYP-OLD", "RUN-UNKNOWN")
    with pytest.raises(ValueError, match="Unknown historical exposure scope"):
        describe_holdout_batch(run_id, runner)
    path = f.repo / exposure.POLICY_PATH
    policy = yaml.safe_load(path.read_text())
    policy["legacy_scopes"] = {"RUN-UNKNOWN": {
        "scope": {"start": f.contract.holdout_start.isoformat(),
                  "end": f.contract.holdout_end.isoformat(),
                  "symbols": {"HISTORICAL-BBB": "UNDERLYING-B"}},
        "evidence": "Scratch principal reviewed legacy instrument aliases and full window.",
    }}
    path.write_text(yaml.safe_dump(policy))
    sign(f, monkeypatch)
    doc = describe_holdout_batch(run_id, runner)
    assert set(doc["overlapping_exposures"]) == {"legacy:RUN-UNKNOWN"}
    with pytest.raises(ValueError, match="independence review"):
        freeze(runner, run_id)
    batch = freeze(runner, run_id, independence={
        "legacy:RUN-UNKNOWN": "Independent scratch thesis fixed before observing any legacy results.",
    })
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_legacy_scopes").fetchone()[0] == 1
    assert qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner,
                             batch_id=batch["batch_id"])["promoted"]


def test_related_lineage_refused_even_for_disjoint_windows(tiny_repo, monkeypatch):
    from warden.contracts import HypothesisSpec

    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    f.registry.register_hypothesis(HypothesisSpec(
        "HYP-ALIAS", f.contract.contract_id, "Renamed related thesis",
        "rules_ts", "Related inventory pressure mechanism",
        "A distinct friction description for the fixture", 8,
        "Reject negative net returns", parent_hypothesis_id="HYP-TINY",
        strategy_module="strategies/flat.py"))
    add_legacy_scope(f, monkeypatch, lineage="HYP-ALIAS", overlapping=False)
    with pytest.raises(PermissionError, match="Same or related lineage"):
        describe_holdout_batch(run_id, runner)


def test_unrelated_disjoint_windows_do_not_require_independence(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    add_legacy_scope(f, monkeypatch, overlapping=False)
    batch = freeze(runner, run_id)
    assert batch["document"]["overlapping_exposures"] == {}


@pytest.mark.parametrize("fault", ["unsigned", "audit", "benchmark", "wrong_batch"])
def test_preclaim_faults_leave_holdout_unspent(tiny_repo, monkeypatch, fault):
    f, runner, run_id = candidate(tiny_repo, incremental=True, monkeypatch=monkeypatch)
    batch = freeze(runner, run_id)
    batch_id = batch["batch_id"]
    if fault == "unsigned":
        (f.repo / ".warden_manifest.sha256.sig").unlink()
    elif fault == "audit":
        f.registry.record_audit(run_id, f.contract.contract_id, "REJECTED", {}, "Fixture audit reversal")
    elif fault == "benchmark":
        p = f.repo / "strategies/benchmark.py"
        p.write_text(p.read_text() + "\n# changed closure\n")
    else:
        batch_id = "0" * 64
    with pytest.raises((ValueError, PermissionError)):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    assert exposure.status(f.registry, batch["batch_id"])["state"] == "AUTHORIZED"
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_claims").fetchone()[0] == 0


def test_concurrent_one_shot_claim_and_crash_before_read(tiny_repo, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    batch_id = freeze(runner, run_id)["batch_id"]

    def crash(*_args, **_kwargs):
        raise RuntimeError("Injected crash before read-start")

    monkeypatch.setattr(exposure, "mark_read", crash)

    def attempt():
        try:
            qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
        except RuntimeError:
            return "CRASHED_AFTER_CLAIM"
        except (PermissionError, ValueError):
            return "REFUSED"
        return "UNEXPECTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["CRASHED_AFTER_CLAIM", "REFUSED"]
    assert exposure.status(f.registry, batch_id)["state"] == "CLAIMED"
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_exposures").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM holdout_batch_reads").fetchone()[0] == 0
    assert attempt() == "REFUSED"


def test_read_start_committed_before_holdout_digest_and_retry_refused(tiny_repo, monkeypatch):
    from warden import qualify

    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    batch_id = freeze(runner, run_id)["batch_id"]
    real_resolve = qualify.resolve_dataset

    def crash(contract, root, *, role="exploratory", **kwargs):
        if role == "holdout":
            assert exposure.status(f.registry, batch_id)["state"] == "READ_STARTED"
            raise RuntimeError("Injected crash before first holdout byte")
        return real_resolve(contract, root, role=role, **kwargs)

    monkeypatch.setattr(qualify, "resolve_dataset", crash)
    with pytest.raises(RuntimeError, match="Injected crash"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    assert exposure.status(f.registry, batch_id)["spent"]
    with pytest.raises(PermissionError, match="already spent"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM qualifications").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM holdout_batch_outcomes").fetchone()[0] == 0


def test_scope_incident_spends_claim_and_records_uncertainty(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch, scope_incident=True)
    batch_id = freeze(runner, run_id)["batch_id"]
    with pytest.raises(exposure.ScopeIncident, match="exceeds its signed exposure scope"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    state = exposure.status(f.registry, batch_id)
    assert state["spent"]
    assert state["state"] == "SCOPE_UNCERTAIN"
    assert state["result"]["scope_uncertain"]
    with pytest.raises(ValueError, match="Unresolved holdout scope incident"):
        describe_holdout_batch(run_id, runner)
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM qualifications").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM holdout_batch_reads").fetchone()[0] == 1


def test_new_overlap_invalidates_previously_frozen_approval(tiny_repo, monkeypatch):
    f, runner, run_id = candidate(tiny_repo, monkeypatch=monkeypatch)
    path = f.repo / exposure.POLICY_PATH
    policy = yaml.safe_load(path.read_text())
    policy["legacy_scopes"] = {"RUN-NEW-EXPOSURE": {
        "scope": {"start": f.contract.holdout_start.isoformat(),
                  "end": f.contract.holdout_end.isoformat(),
                  "symbols": {"OTHER-BBB": "UNDERLYING-B"}},
        "evidence": "Scratch principal verified the full historical exposure scope.",
    }}
    path.write_text(yaml.safe_dump(policy))
    sign(f, monkeypatch)
    batch_id = freeze(runner, run_id)["batch_id"]
    f.registry.reserve_holdout_lineage("HYP-OTHER", "RUN-NEW-EXPOSURE")
    with pytest.raises(ValueError, match="exposure history changed"):
        qualify_candidate(run_id, "YES_UNSEAL_HOLDOUT", runner, batch_id=batch_id)
    assert exposure.status(f.registry, batch_id)["state"] == "AUTHORIZED"
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_batch_reads").fetchone()[0] == 0


def test_v7_upgrade_preserves_legacy_repeated_exposures(tiny_repo):
    from warden.registry import ResearchRegistry

    f = tiny_repo()
    with sqlite3.connect(f.registry.db_path) as conn:
        for table in reversed(exposure.TABLES):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=7")
        conn.execute("INSERT INTO holdout_claims(lineage_id,run_id,state,claimed_at) VALUES (?,?,?,?)",
                     ("HYP-OLD", "RUN-PREVIOUS-CLAIM", "READ_STARTED", 1.0))
        conn.execute("INSERT INTO qualifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("QUAL-OLD", "RUN-PREVIOUS-QUAL", f.contract.contract_id, "HYP-OLD", "HYP-OLD",
                      1.1, 1.0, 0.1, 0.8, "historic-operator", 2.0))
        before_claim = conn.execute("SELECT * FROM holdout_claims").fetchall()
        before_qual = conn.execute("SELECT * FROM qualifications").fetchall()
    ResearchRegistry(f.registry.db_path)
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert conn.execute("SELECT * FROM holdout_claims").fetchall() == before_claim
        assert conn.execute("SELECT * FROM qualifications").fetchall() == before_qual
        assert conn.execute("SELECT exposure_id,source FROM holdout_exposures ORDER BY exposure_id").fetchall() == [
            ("legacy:RUN-PREVIOUS-CLAIM", "legacy"), ("legacy:RUN-PREVIOUS-QUAL", "legacy")]
        assert conn.execute("SELECT count(*) FROM holdout_legacy_scopes").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute("DELETE FROM holdout_exposures")
