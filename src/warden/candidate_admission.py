"""Frozen selectable-candidate identity; no outcome computation or policy input."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from warden.codeid import code_closure
from warden.contracts import canonical_json


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    hypothesis_id: str
    contract_id: str
    contract_hash: str
    dataset_sha256: str
    strategy_module: str
    strategy_code_sha256: str
    code_content_sha256: str
    parameters_json: str
    effective_parameters_json: str
    strategy_family: str
    model_type: str
    model_class: str
    simulation_epoch: str
    legacy_replay: bool

    def __post_init__(self):
        for field, width in (
            ("contract_hash", 16), ("dataset_sha256", 64),
            ("strategy_code_sha256", 64), ("code_content_sha256", 64),
        ):
            if not re.fullmatch(rf"[0-9a-f]{{{width}}}", getattr(self, field)):
                raise ValueError(f"Invalid frozen candidate {field}")
        if not all((self.hypothesis_id, self.contract_id, self.strategy_module,
                    self.strategy_family, self.model_type, self.simulation_epoch)):
            raise ValueError("Incomplete frozen candidate identity")

    @property
    def payload(self) -> str:
        return canonical_json(asdict(self))

    @property
    def candidate_id(self) -> str:
        return hashlib.sha256(self.payload.encode()).hexdigest()


def freeze_candidate(repo_root, spec, contract, dataset, effective_params,
                     code_sha: str, simulation_epoch: str, *, legacy_replay: bool) -> CandidateIdentity:
    """Bind the actual code closure and declared/effective execution inputs.

    In addition to Warden's path-bound closure digest, hash its entry bytes and
    sorted dependency content digests to recognize byte-identical renamed modules.
    This is exact equivalence, not a claim of semantic novelty detection.
    """
    root = Path(repo_root).resolve()
    module = str(spec.strategy_module).strip().removeprefix("./")
    actual_sha, paths = code_closure(root, module)
    if actual_sha != code_sha:
        raise ValueError("strategy closure changed during candidate preparation")
    entry = (root / module).resolve().relative_to(root).as_posix()
    contents = {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in paths
    }
    verified = hashlib.sha256(
        "\n".join(f"{rel} {contents[rel]}" for rel in paths).encode()
    ).hexdigest()
    if verified != code_sha:
        raise ValueError("strategy closure changed during candidate preparation")
    content = hashlib.sha256(canonical_json([
        contents[entry], sorted(value for rel, value in contents.items() if rel != entry),
    ]).encode()).hexdigest()
    return CandidateIdentity(
        str(spec.hypothesis_id), contract.contract_id, contract.spec_hash(), dataset.sha256,
        module, code_sha, content, canonical_json(spec.parameters),
        canonical_json(effective_params), spec.strategy_family, str(spec.model_type),
        str(spec.model_class or ""), simulation_epoch, legacy_replay,
    )
