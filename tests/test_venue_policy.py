"""Research standards are read only from a fixture-approved policy."""

import hashlib
import hmac
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from warden_ops.integrity import MANIFEST_FILE, check_worktree
from warden_ops.venue_policy import VenuePolicyError, load_venue_policy

POLICY_REL = "contracts/policy/venue_policy.yaml"
SYNTHETIC_POLICY = """\
version: 1
train_start: '2021-01-01T00:00:00Z'
val_start: '2022-01-01T00:00:00Z'
val_end: '2023-01-01T00:00:00Z'
holdout_start: '2023-01-01T01:00:00+01:00'
holdout_end: '2024-01-01T00:00:00Z'
asset_classes:
  fx:
    taker_fee_bps: 0.6
    slippage_bps: 0.7
    funding_bps_per_8h: 0
    allow_leverage: false
    max_gross_leverage: 0.9
    selection_groups:
      intraday: TEST_FX_INTRADAY
      daily: TEST_FX_DAILY
"""


@pytest.fixture
def signed_policy(tmp_path, monkeypatch):
    # Arbitrary test bytes; there is no production key, CLI signing or manifest generation.
    fixture_key = b"fixture-only-venue-policy"
    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", fixture_key.decode())

    def install(document=SYNTHETIC_POLICY, *, covered=True):
        policy = tmp_path / POLICY_REL
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(document, encoding="utf-8")
        manifest = (
            f"{hashlib.sha256(document.encode()).hexdigest()}  {POLICY_REL}\n" if covered else ""
        ).encode()
        (tmp_path / MANIFEST_FILE).write_bytes(manifest)
        signature = hmac.digest(fixture_key, manifest, "sha256").hex()
        (tmp_path / f"{MANIFEST_FILE}.sig").write_text(signature + "\n", encoding="ascii")
        return tmp_path

    return install


def test_signed_policy_supplies_rules_and_shared_cadence_groups(signed_policy) -> None:
    policy = load_venue_policy(signed_policy())
    rules = policy.rules_for("fx", "1h")
    assert (rules.taker_fee_bps, rules.slippage_bps, rules.funding_bps_per_8h) == (0.6, 0.7, 0)
    assert rules.allow_leverage is False and rules.max_gross_leverage == 0.9
    assert rules.selection_group_id == "TEST_FX_INTRADAY"
    assert policy.selection_group_id("fx", "15m") == "TEST_FX_INTRADAY"
    assert policy.selection_group_id("fx", "1d") == "TEST_FX_DAILY"
    assert policy.holdout_start == datetime(2023, 1, 1, tzinfo=UTC)
    assert policy.sha256 == hashlib.sha256(SYNTHETIC_POLICY.encode()).hexdigest()


def test_worktree_gate_detects_changed_policy(signed_policy) -> None:
    repo = signed_policy()
    assert check_worktree(repo)
    (repo / POLICY_REL).write_text(SYNTHETIC_POLICY + "\n# edited\n", encoding="utf-8")
    assert not check_worktree(repo)
    with pytest.raises(VenuePolicyError, match="digest mismatch"):
        load_venue_policy(repo)


def test_absent_policy_refuses_without_a_default(tmp_path) -> None:
    with pytest.raises(VenuePolicyError, match="missing"):
        load_venue_policy(tmp_path)


def test_policy_needs_signed_manifest_coverage(signed_policy) -> None:
    with pytest.raises(VenuePolicyError, match="not covered"):
        load_venue_policy(signed_policy(covered=False))


def test_interactive_approval_metadata_alone_is_not_a_signature(signed_policy) -> None:
    repo = signed_policy()
    (repo / f"{MANIFEST_FILE}.sig").unlink()
    (repo / f"{MANIFEST_FILE}.meta.json").write_text(
        json.dumps(
            {
                "mode": "interactive",
                "approver": "fixture-operator",
                "manifest_sha256": hashlib.sha256((repo / MANIFEST_FILE).read_bytes()).hexdigest(),
            }
        )
    )
    assert check_worktree(repo)  # integrity != approval
    with pytest.raises(VenuePolicyError, match="verified human manifest HMAC is required"):
        load_venue_policy(repo)


def test_unverified_signature_does_not_authorize_policy(signed_policy, monkeypatch) -> None:
    repo = signed_policy()
    monkeypatch.delenv("WARDEN_HUMAN_SIGNING_KEY")
    with pytest.raises(VenuePolicyError, match="verified human manifest HMAC is required"):
        load_venue_policy(repo)


def test_unsigned_rehash_cannot_authorize_changed_policy(signed_policy) -> None:
    repo = signed_policy()
    changed = SYNTHETIC_POLICY.replace("0.6", "0.1").encode()
    (repo / POLICY_REL).write_bytes(changed)
    (repo / MANIFEST_FILE).write_text(f"{hashlib.sha256(changed).hexdigest()}  {POLICY_REL}\n")
    with pytest.raises(VenuePolicyError, match="invalid human manifest HMAC"):
        load_venue_policy(repo)


@pytest.mark.parametrize(
    "old, new",
    [
        ("version: 1", "version: true"),
        ("version: 1", "version: 2"),
        ("0.6", "'0.6'"),
        ("0.6", "0"),
        ("0.7", ".inf"),
        ("8h: 0", "8h: -1"),
        ("0.9", "1.1"),
        ("allow_leverage: false", "allow_leverage: 'false'"),
        ("2022-01-01T00:00:00Z", "2023-01-02T00:00:00Z"),
        ("'2022-01-01T00:00:00Z'", "'2022-01-01T00:00:00'"),
        ("'2022-01-01T00:00:00Z'", "1640995200"),
        ("'2022-01-01T00:00:00Z'", "!!timestamp invalid"),
        ("TEST_FX_DAILY", "'  '"),
        ("daily:", "weekly:"),
        ("  fx:", "  unknown:"),
        ("taker_fee_bps", "fee_bps"),
        ("    slippage_bps: 0.7", "    slippage_bps: 0.7\n    slippage_bps: 0.2"),
    ],
)
def test_signed_malformed_policy_refuses(signed_policy, old, new) -> None:
    assert old in SYNTHETIC_POLICY
    with pytest.raises(VenuePolicyError, match="malformed venue policy"):
        load_venue_policy(signed_policy(SYNTHETIC_POLICY.replace(old, new)))


def test_unmapped_market_does_not_fall_back(signed_policy) -> None:
    policy = load_venue_policy(
        signed_policy(SYNTHETIC_POLICY.replace("      daily: TEST_FX_DAILY\n", ""))
    )
    with pytest.raises(VenuePolicyError, match="no venue policy rules"):
        policy.selection_group_id("fx", "1d")
    with pytest.raises(VenuePolicyError, match="no venue policy rules"):
        policy.selection_group_id("metals", "1h")
    with pytest.raises(VenuePolicyError, match="unsupported venue policy frequency"):
        policy.selection_group_id("fx", "1w")


def test_standalone_trust_preflight_assesses_the_same_policy(signed_policy) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/preflight_trust_boundary.py"
    repo = signed_policy()
    result = subprocess.run(
        [sys.executable, str(script), "--repo-root", str(repo), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert "PROTECTED_SET_MISMATCH" not in {row["id"] for row in report["findings"]}
    assert str(repo / POLICY_REL) in {row["path"] for row in report["files"]}
