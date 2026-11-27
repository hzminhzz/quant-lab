"""A13–A17: protected authority protocol in isolated fixtures, not installation proof."""
from pathlib import Path

import pytest

from tests.test_ac04_release_build import source
from tests.test_ac05_decisions import gate, signed
from warden_ops.control_contracts import digest
from warden_ops.decisions import Effect, make_packet
from warden_ops.release import build_candidate
from warden_ops.release_authority import Baseline, ReleaseAuthority


class Executor:
    def __init__(self, release_id):
        self.current = release_id
        self.schema = 9
        self.fail = False
        self.halted = False

    def observe(self):
        return {"release_id": self.current, "ledger_schema": self.schema, "healthy": not self.fail}

    def activate(self, path, expected):
        assert self.current == expected
        self.current = Path(path).name

    def halt(self):
        self.halted = True


def approval(effects):
    packet = make_packet(effects=effects, evidence=["fixture:verification"], consequences=["fixture only"],
                         limitations=["no independent installed authority"], rollback="compatible rollback or halt",
                         now=1000., expires_at=2000.)
    return packet, signed(packet, approved_effects=[x.kind for x in effects])


def fixture(cli_repo, tmp_path):
    root = source(cli_repo)
    old = build_candidate(root, tmp_path / "builds", ledger_min=8, ledger_max=9)
    executor = Executor(old["release_id"])
    baseline = Baseline(identity="b" * 64, required_checks=["governance", "authority"])
    calls = []
    def verifier(path, expected_baseline):
        calls.append((path, expected_baseline))
        return {"release_id": Path(path).name, "baseline": expected_baseline,
                "checks": {"governance": "PASS", "authority": "PASS"},
                "evidence": ["fixture:real protocol tests; production checks unavailable"]}
    authority = ReleaseAuthority(tmp_path / "authority.sqlite3", gate=gate(), baseline=baseline,
                                 verifier=verifier, executor=executor, clock=lambda: 1200.)
    return root, old, executor, authority, calls


def test_exact_admission_and_activation_are_separate_and_verifier_is_trusted(cli_repo, tmp_path):
    root, old, executor, authority, calls = fixture(cli_repo, tmp_path)
    (root / "src/warden/example.py").write_text("VALUE = 2\n")
    new = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=9,
                          rollback_release=old["release_id"])
    packet, proof = approval([Effect(kind="release", subject_sha256=new["release_id"])])
    receipt = authority.admit(new["path"], packet=packet, proof=proof, previous_path=old["path"])
    assert receipt["release_id"] == new["release_id"]
    assert calls[0][1] == "b" * 64
    with pytest.raises(ValueError, match="effect"):
        authority.activate(new["path"], packet=packet, proof=proof, rollback_path=old["path"])
    assert executor.current == old["release_id"]
    packet, proof = approval([Effect(kind="activation", subject_sha256=new["release_id"])])
    activated = authority.activate(new["path"], packet=packet, proof=proof, rollback_path=old["path"])
    assert activated["state"] == "ACTIVE"
    assert executor.current == new["release_id"]


def test_scoped_grant_allows_docs_but_never_candidate_verifier_changes(cli_repo, tmp_path):
    root, old, _executor, authority, _calls = fixture(cli_repo, tmp_path)
    grant = {"schema_version": 1, "recipient": "release-authority", "baseline": "b" * 64,
             "classes": ["documentation"], "effects": ["release"], "expires_at": 1400.}
    packet, proof = approval([Effect(kind="policy", subject_sha256=digest(grant))])
    grant_id = authority.register_grant(grant, packet=packet, proof=proof)
    (root / "README.md").write_text("Reviewed operational documentation\n")
    docs = build_candidate(root, tmp_path / "builds", ledger_min=8, ledger_max=9)
    assert authority.admit(docs["path"], grant_id=grant_id,
                           previous_path=old["path"])["authorized_by"] == grant_id
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_verifier.py").write_text("def test_weakened(): pass\n")
    untrusted = build_candidate(root, tmp_path / "builds", ledger_min=8, ledger_max=9)
    with pytest.raises(ValueError, match="scope"):
        authority.admit(untrusted["path"], grant_id=grant_id, previous_path=old["path"])


@pytest.mark.parametrize("relative", ["docs/research-outcomes/index.json", "docs/conf.py",
                                     "docs/research-outcomes/ledger.md", "docs/AGENTS.md",
                                     "docs/executable.md"])
def test_documentation_grant_never_covers_machine_read_policy_or_code(cli_repo, tmp_path, relative):
    root, old, _executor, authority, _calls = fixture(cli_repo, tmp_path)
    grant = {"schema_version": 1, "recipient": "release-authority", "baseline": "b" * 64,
             "classes": ["documentation"], "effects": ["release"], "expires_at": 1400.}
    packet, proof = approval([Effect(kind="policy", subject_sha256=digest(grant))])
    grant_id = authority.register_grant(grant, packet=packet, proof=proof)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("untrusted machine-read content")
    if relative == "docs/executable.md":
        path.chmod(0o755)
    new = build_candidate(root, tmp_path / "builds", ledger_min=8, ledger_max=9)
    with pytest.raises(ValueError, match="scope"):
        authority.admit(new["path"], grant_id=grant_id, previous_path=old["path"])


def test_lost_activation_reply_is_observed_without_repeating_effect(cli_repo, tmp_path, monkeypatch):
    root, old, executor, authority, _calls = fixture(cli_repo, tmp_path)
    (root / "src/warden/example.py").write_text("VALUE = 2\n")
    new = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=10)
    packet, proof = approval([Effect(kind="release", subject_sha256=new["release_id"]),
                              Effect(kind="activation", subject_sha256=new["release_id"])])
    authority.admit(new["path"], packet=packet, proof=proof, previous_path=old["path"])
    actual, effects = executor.activate, []
    def lost(path, expected):
        actual(path, expected)
        effects.append(path)
        raise KeyboardInterrupt("lost response after activation")
    monkeypatch.setattr(executor, "activate", lost)
    with pytest.raises(KeyboardInterrupt):
        authority.activate(new["path"], packet=packet, proof=proof)
    recovered = authority.activate(new["path"], packet=packet, proof=proof)
    assert recovered["recovered"] is True
    assert len(effects) == 1


def test_observer_failure_after_activation_attempt_is_halted_and_recorded(cli_repo, tmp_path, monkeypatch):
    root, old, executor, authority, _calls = fixture(cli_repo, tmp_path)
    (root / "src/warden/example.py").write_text("VALUE = 2\n")
    new = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=10)
    packet, proof = approval([Effect(kind="release", subject_sha256=new["release_id"]),
                              Effect(kind="activation", subject_sha256=new["release_id"])])
    authority.admit(new["path"], packet=packet, proof=proof, previous_path=old["path"])
    observed = executor.observe
    def activate(path, expected):
        executor.current = Path(path).name
        monkeypatch.setattr(executor, "observe", lambda: (_ for _ in ()).throw(OSError("observer unavailable")))
    monkeypatch.setattr(executor, "activate", activate)
    result = authority.activate(new["path"], packet=packet, proof=proof, rollback_path=old["path"])
    assert result["state"] == "HALTED_RECOVERY_UNVERIFIED"
    assert executor.halted is True
    assert authority.history()[-1]["kind"] == "activation-result"
    assert observed()["ledger_schema"] == 9


def test_failed_activation_never_rewinds_incompatible_ledger(cli_repo, tmp_path):
    root, old, executor, authority, _calls = fixture(cli_repo, tmp_path)
    (root / "src/warden/example.py").write_text("VALUE = 2\n")
    new = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=10)
    packet, proof = approval([Effect(kind="release", subject_sha256=new["release_id"]),
                              Effect(kind="activation", subject_sha256=new["release_id"])])
    authority.admit(new["path"], packet=packet, proof=proof, previous_path=old["path"])
    executor.schema = 10
    executor.fail = True
    result = authority.activate(new["path"], packet=packet, proof=proof, rollback_path=old["path"])
    assert result["state"] == "HALTED_INCOMPATIBLE_ROLLBACK"
    assert executor.schema == 10
    assert executor.halted is True
    assert any(event["kind"] == "activation-intent" for event in authority.history())
