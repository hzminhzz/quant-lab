"""Owner-delegated integrity refresh must not manufacture scientific approval."""
import json

import pytest

from tests.test_issue4_trust_boundary import _cli, _mini_repo
from warden_ops.integrity import MANIFEST_FILE, read_signed_manifest_entry


def test_maintenance_refresh_is_noninteractive_checksum_only(tmp_path):
    root = _mini_repo(tmp_path)
    result = _cli(root, "generate-manifest", "--maintenance", "--json",
                  env={"WARDEN_NON_INTERACTIVE": "1"})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "maintenance"
    assert report["scientific_approval"] is False
    assert (root / MANIFEST_FILE).is_file()
    assert not (root / (MANIFEST_FILE + ".sig")).exists()
    assert not (root / (MANIFEST_FILE + ".meta.json")).exists()
    with pytest.raises(ValueError, match="sign|key|HMAC"):
        read_signed_manifest_entry(root, "AGENTS.md")


def test_maintenance_refresh_preserves_existing_scientific_signature(tmp_path):
    root = _mini_repo(tmp_path)
    signed = _cli(root, "generate-manifest", "--json",
                  env={"WARDEN_HUMAN_SIGNING_KEY": "fixture-key-not-real-authority"})
    assert signed.returncode == 0, signed.stderr
    paths = [root / (MANIFEST_FILE + suffix) for suffix in ("", ".sig", ".meta.json")]
    before = [path.read_bytes() for path in paths]
    refused = _cli(root, "generate-manifest", "--maintenance", "--json",
                   env={"WARDEN_NON_INTERACTIVE": "1"})
    assert refused.returncode == 2
    assert "signed manifest" in refused.stderr.lower()
    assert [path.read_bytes() for path in paths] == before
