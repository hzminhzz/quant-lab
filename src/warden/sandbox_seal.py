"""Bind a compiled sandbox contract to the artifact identity it was compiled with (#117).

``contracts/sandbox/**`` is deliberately outside the protected manifest: protecting it
would force a human re-sign on every compile, which is exactly what a cheap exploratory
compile must not need.  The other half of that bargain is this module.  Every execution
seam that loads a sandbox contract re-derives its identity from the artifact manifest and
the signed venue policy, so editing the YAML between compile and verify/screen/backtest/
advance is a refusal instead of a looser trial.

What is compared, and why each comparison exists:

=====================================  ====================================================
``sandbox.schema``                     a manifest of another artifact contract is not this one
``sandbox.contract_id``                the manifest is addressed by the contract id
``sandbox.policy.rules``               the costs, funding and leverage ceiling the signed
                                       policy injected at compile time
``sandbox.policy.sha256``              the digest of the *currently trusted* policy, so a
                                       re-signed policy invalidates the artifact
``sandbox.policy.selection_group_id``  the search-debt group the compile charged
policy train/val/holdout dates         the fences, re-read from the signed policy
``sandbox.mechanics``                  folds, label horizon, embargo, execution delay
``sandbox.requirement``                asset class, cadence, execution mode
``record.dataset``                     price basis and frequency actually compiled
``record.sha256``                      the Parquet digest the contract pins
``record.path``                        the exploratory file the contract names
=====================================  ====================================================

A missing, unreadable or foreign manifest is a refusal, not a warning: a sandbox contract
with no artifact behind it would otherwise run on whatever numbers the YAML happens to
hold.  Nothing here writes, opens a holdout byte, or signs anything.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from warden.contract_source import SANDBOX_MANIFEST_DIR, is_sandbox_contract
from warden.contracts import canonical_json

__all__ = ["SandboxArtifactError", "verify_sandbox_contract"]


class SandboxArtifactError(RuntimeError):
    """A compiled sandbox contract no longer matches the artifact it was compiled with.

    A ``RuntimeError`` on purpose: ``warden data compile``/``verify``'s handler sets turn a
    ``ValueError`` into an evidence refusal (exit 1), while this is a refusal of the
    *request* — the document is not the one the artifact authorises — and the CLI maps it
    to exit 2 without the registration path swallowing it as a review item.
    """


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _artifact_manifest_path(repo_root: Path, contract_id: str) -> Path:
    return repo_root / SANDBOX_MANIFEST_DIR / f"{contract_id}.json"


def verify_sandbox_contract(
    repo_root: Path | str,
    contract: Any,
    *,
    registered_spec_hash: str | None = None,
    policy: Any | None = None,
) -> dict[str, Any]:
    """Check a sandbox contract against its artifact; return the manifest record.

    A non-sandbox contract is not this module's business and returns ``{}``.  A sandbox
    contract with no matching manifest, an unauthenticated policy, or any recorded field
    that differs from the document raises :class:`SandboxArtifactError` naming each field
    that moved.  ``registered_spec_hash`` adds the registry's recorded contract digest to
    the comparison, so an edit *after* registration is caught too (the manifest comparison
    catches an edit before it, which the registry cannot see).
    """
    root = Path(repo_root)
    if not is_sandbox_contract(root, contract.contract_id):
        return {}

    contract_id = str(contract.contract_id)
    from warden.compile import ARTIFACT_SCHEMA  # local: compile imports resolve at module load

    manifest_path = _artifact_manifest_path(root, contract_id)
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SandboxArtifactError(
            f"sandbox contract {contract_id} has no artifact manifest at "
            f"{manifest_path.as_posix()}; only a compiled contract is runnable"
        ) from exc
    except (OSError, ValueError) as exc:
        raise SandboxArtifactError(
            f"sandbox contract {contract_id}: artifact manifest {manifest_path.as_posix()} is "
            f"unreadable ({exc}); a runnable sandbox contract needs its compiled identity"
        ) from exc
    if not isinstance(record, Mapping):
        raise SandboxArtifactError(
            f"{manifest_path.as_posix()} is not a JSON object; a sandbox contract needs its "
            "artifact manifest"
        )
    sandbox = record.get("sandbox")
    if not isinstance(sandbox, Mapping):
        raise SandboxArtifactError(
            f"{manifest_path.as_posix()} carries no sandbox block; only a "
            f"{ARTIFACT_SCHEMA} artifact is runnable"
        )
    if sandbox.get("schema") != ARTIFACT_SCHEMA:
        raise SandboxArtifactError(
            f"{manifest_path.as_posix()} declares sandbox schema {sandbox.get('schema')!r}, not "
            f"{ARTIFACT_SCHEMA!r}"
        )

    raw_policy = sandbox.get("policy")
    policy_block: Mapping[str, Any] = raw_policy if isinstance(raw_policy, Mapping) else {}
    raw_rules = policy_block.get("rules")
    rules: Mapping[str, Any] = raw_rules if isinstance(raw_rules, Mapping) else {}
    raw_requirement = sandbox.get("requirement")
    requirement: Mapping[str, Any] = (
        raw_requirement if isinstance(raw_requirement, Mapping) else {}
    )
    raw_mechanics = sandbox.get("mechanics")
    mechanics: Mapping[str, Any] = (
        raw_mechanics if isinstance(raw_mechanics, Mapping) else {}
    )
    raw_dataset = record.get("dataset")
    dataset: Mapping[str, Any] = raw_dataset if isinstance(raw_dataset, Mapping) else {}
    governance = contract.governance

    if policy is None:
        from warden_ops.venue_policy import VenuePolicyError, load_venue_policy

        try:
            policy = load_venue_policy(root)
        except VenuePolicyError as exc:
            raise SandboxArtifactError(
                f"sandbox contract {contract_id}: cannot re-assert the signed venue policy its "
                f"costs came from ({exc}); recompile once the policy is trusted again"
            ) from exc

    if policy_block.get("sha256") != policy.sha256:
        raise SandboxArtifactError(
            f"sandbox contract {contract_id} was compiled under venue policy "
            f"{str(policy_block.get('sha256'))[:12]}... but the trusted policy is now "
            f"{policy.sha256[:12]}...; recompile the requirement under the current policy"
        )

    checks: dict[str, tuple[Any, Any]] = {
        "contract_id": (contract_id, sandbox.get("contract_id")),
        "taker_fee_bps": (contract.taker_fee_bps, rules.get("taker_fee_bps")),
        "slippage_bps": (contract.slippage_bps, rules.get("slippage_bps")),
        "funding_bps_per_8h": (contract.funding_bps_per_8h, rules.get("funding_bps_per_8h")),
        "allow_leverage": (bool(governance.allow_leverage), rules.get("allow_leverage")),
        "max_gross_leverage": (
            float(governance.max_gross_leverage),
            rules.get("max_gross_leverage"),
        ),
        "selection_group_id": (
            governance.selection_group_id,
            policy_block.get("selection_group_id"),
        ),
        "train_start": (_iso(contract.train_start), _iso(policy.train_start)),
        "val_start": (_iso(contract.val_start), _iso(policy.val_start)),
        "val_end": (_iso(contract.val_end), _iso(policy.val_end)),
        "holdout_start": (_iso(contract.holdout_start), _iso(policy.holdout_start)),
        "holdout_end": (_iso(contract.holdout_end), _iso(policy.holdout_end)),
        "n_folds": (contract.n_folds, mechanics.get("n_folds")),
        "label_horizon_bars": (contract.label_horizon_bars, mechanics.get("label_horizon_bars")),
        "embargo_bars": (contract.embargo_bars, mechanics.get("embargo_bars")),
        "execution_delay_bars": (
            contract.execution_delay_bars,
            mechanics.get("execution_delay_bars"),
        ),
        "asset_class": (contract.asset_class, requirement.get("asset_class")),
        "frequency": (contract.frequency, requirement.get("frequency")),
        "execution_mode": (contract.execution_mode, requirement.get("execution_mode")),
        "dataset_frequency": (contract.frequency, dataset.get("frequency")),
        "data_snapshot_sha256": (contract.data_snapshot_sha256, record.get("sha256")),
        "exploratory_data_path": (contract.exploratory_data_path, record.get("path")),
    }
    mismatched = {
        name: {"found": found, "recorded": recorded}
        for name, (found, recorded) in checks.items()
        if found != recorded
    }
    if registered_spec_hash is not None and registered_spec_hash != contract.spec_hash():
        mismatched["registered_spec_hash"] = {
            "found": contract.spec_hash(),
            "recorded": registered_spec_hash,
        }
    if mismatched:
        raise SandboxArtifactError(
            f"sandbox contract {contract_id} no longer matches the artifact it was compiled "
            f"with ({canonical_json(mismatched)}); recompile the requirement instead of editing "
            "contracts/sandbox/**"
        )
    return dict(record)
