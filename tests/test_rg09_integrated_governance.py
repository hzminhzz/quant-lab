"""Integrated admission witness using only synthetic scratch data."""
import runpy
import sqlite3
from pathlib import Path

import pytest
import yaml

from tests.test_rg05_research_intent import approve, review
from tests.test_rg06_incremental import _control_panel, _long_module
from warden.contracts import utc
from warden.registry import HypothesisBudgetExhaustedError, ScreenAlreadyAttemptedError

PREFLIGHT = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/preflight_trial.py"))


def counts(f):
    tables = ("candidate_admissions", "candidate_operations", "screen_receipts",
              "attempt_receipts", "selection_attempts", "trials")
    with sqlite3.connect(f.registry.db_path) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def test_two_preflights_and_eight_screen_requests_with_all_governance_enabled(tiny_repo, monkeypatch):
    """Real intent, novelty, revisions, budget and receipts; no policy double."""
    end = utc("2025-06-30T00:00:00Z")
    source = _long_module("BBB").replace("def __init__(self):", "def __init__(self, weight):")
    source = source.replace("self.executor =", "self.weight = weight\n        self.executor =")
    source = source.replace("0.4 if a ==", "self.weight if a ==")
    source = source.replace("return LongStrategy()", "return LongStrategy(params.get('weight', 0.4))")
    f = tiny_repo(module_source=source, panel_override=_control_panel(), contract_overrides={
        "frequency": "1d", "bars_per_year": 365, "val_start": utc("2025-01-21T00:00:00Z"),
        "val_end": end, "holdout_start": end, "holdout_end": utc("2025-12-01T00:00:00Z")})
    runner, _, _ = approve(f)
    base = yaml.safe_load(f.spec.read_text())
    monkeypatch.chdir(f.repo)
    for i in range(2):
        f.spec.write_text(yaml.safe_dump({**base, "parameters": {"weight": 0.3 + i * 0.02}}))
        result = PREFLIGHT["preflight"](f.spec, f.registry.db_path)
        assert result["strategy_execution"] == "NOT_RUN"
        assert result["kind"] == "METADATA_ONLY"
        assert not any(counts(f).values())
    paths, prior = [], None
    for i in range(8):
        f.spec = f.repo / f"variant-{i}.yaml"
        f.spec.write_text(yaml.safe_dump({**base, "parameters": {"weight": 0.25 + i * 0.025}}))
        _, binding = review(runner, f, **({"revision": "sizing", "predecessor_candidate_id": prior} if prior else {}))
        if i < 3:
            assert runner.run_stage1_screen(f.spec).passed
            paths.append(f.spec)
            prior = binding["candidate_id"]
        else:
            before = counts(f)
            with pytest.raises(HypothesisBudgetExhaustedError):
                runner.run_stage1_screen(f.spec)
            assert counts(f) == before
    assert counts(f)["candidate_admissions"] == counts(f)["screen_receipts"] == 3
    assert counts(f)["selection_attempts"] == 3
    renamed = f.repo / "renamed.yaml"
    renamed.write_bytes(paths[0].read_bytes())
    result = runner.run_spec(renamed)
    assert f.registry.reconcile_attempt(result.run_id)["state"] == "FINALIZED"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 3
    assert counts(f)["candidate_operations"] == 4
    with pytest.raises(ScreenAlreadyAttemptedError):
        runner.run_spec(renamed)
    assert counts(f)["trials"] == 1


def test_scoped_gateway_checks_digest_of_synthetic_artifact_bytes(tiny_repo, monkeypatch):
    """A supplied resolution is not proof that the bytes subsequently parsed match it."""
    from dataclasses import replace

    from tests.test_rg08_holdout_batch import candidate
    from warden import exposure
    from warden.market_data import ROLE_HOLDOUT, MarketDataGateway
    from warden.provenance import resolve_dataset

    f, _, _ = candidate(tiny_repo, monkeypatch=monkeypatch)
    resolution = resolve_dataset(f.contract, f.repo, role=ROLE_HOLDOUT)
    assert resolution.eligible_market
    scope = yaml.safe_load((f.repo / exposure.POLICY_PATH).read_text())["windows"][f.contract.contract_id]
    # A deliberately incorrect expected digest for this disposable synthetic fixture.
    wrong = "0" * 64
    with pytest.raises(exposure.ScopeIncident, match="digest"):
        MarketDataGateway(f.repo).load(
            f.contract, ROLE_HOLDOUT,
            resolution=replace(resolution, sha256=wrong),
            holdout_scope={**scope, "dataset_sha256": wrong},
        )
