"""AC-01/A04: the public control contract rejects ambiguity before dispatch."""

import pytest
from pydantic import ValidationError

from warden_ops.control_contracts import capabilities, parse_request


def test_strict_versioned_requests_and_safe_paths():
    request = parse_request({"schema_version": 1, "operation": "prepare",
                             "action": {"kind": "screen", "spec_path": "experiments/active/a.yaml"}})
    assert request.action.kind == "screen"
    for payload in [
        {"schema_version": 2, "operation": "observe"},
        {"schema_version": 1, "operation": "observe", "shell": "true"},
        {"schema_version": 1, "operation": "execute", "command": "true"},
        {"schema_version": 1, "operation": "prepare",
         "action": {"kind": "qualify", "spec_path": "a.yaml"}},
        {"schema_version": 1, "operation": "prepare",
         "action": {"kind": "screen", "spec_path": "../outside.yaml"}},
        {"schema_version": 1, "operation": "prepare",
         "action": {"kind": "screen", "spec_path": "data/holdout/a.yaml"}},
        {"schema_version": 1, "operation": "observe", "limit": True},
    ]:
        with pytest.raises(ValidationError):
            parse_request(payload)


def test_catalog_names_owners_effects_and_receipt_recovery():
    catalog = capabilities()
    assert catalog["schema_version"] == 1
    assert catalog["request_schema"]["discriminator"]["propertyName"] == "operation"
    actions = {row["name"]: row for row in catalog["actions"]}
    assert actions["screen"]["owner"] == "warden.runner.ExperimentRunner.screen"
    assert actions["screen"]["charge"] == "existing candidate and selection accounting"
    assert actions["screen"]["retry"] == "lookup original receipt; never repeat scoring"
    assert actions["observe"]["scientific_write"] is False
    assert actions["prepare"]["sealed_access"] is False
    assert actions["qualify"]["available"] is False
    for row in actions.values():
        assert all(row[key] for key in ("owner", "authority", "receipt", "cancellation", "retry"))
