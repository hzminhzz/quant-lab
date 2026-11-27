"""Frozen typed schema for agent-owned experiment specifications.

Experiment YAMLs are agent-owned input.  They may select *what* to test
(contract, hypothesis, strategy, model, parameters) but they may never
inject evaluator policy or evaluator state.  ``ExperimentSpec`` is therefore
strict (``extra="forbid"``) and immutable (``frozen=True``): unknown keys such
as ``simulation_epoch``, ``allow_leverage``, ``allow_short_selling``,
``max_volume_participation`` and ``market_impact_coefficient`` raise a
``pydantic.ValidationError`` while parsing, before the runner opens the
registry or reserves a screen/trial row.

The active simulation epoch is a Warden constant, not spec input.  Historical
replay remains available through the explicit ``allow_legacy_replay=True``
runner opt-in and through registry epochs recorded on past trials; it is never
selected by an agent-authored YAML key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _none_param_grid_to_empty(value: Any) -> Any:
    """An explicitly empty ``param_grid:`` key has always meant "no grid"."""

    return [] if value is None else value

__all__ = [
    "ALLOWED_EXPERIMENT_SPEC_FIELDS",
    "ExperimentSpec",
    "load_experiment_spec",
]

#: Top-level keys an agent-authored experiment spec may declare.  Anything
#: else is evaluator policy or evaluator state and is rejected.
ALLOWED_EXPERIMENT_SPEC_FIELDS = frozenset(
    {
        "contract_id",
        "hypothesis_id",
        "strategy_name",
        "strategy_family",
        "strategy_module",
        "model_type",
        "model_class",
        "parameters",
        "param_grid",
        "run_id",
        "research_intent_sha256",
    }
)


class ExperimentSpec(BaseModel):
    """Strict, immutable experiment specification.

    Only the fields below exist; ``extra="forbid"`` rejects evaluator-policy
    keys (``simulation_epoch``, ``allow_leverage``, ...) and any other unknown
    key with a ``ValidationError`` during validation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str = Field(min_length=1)
    hypothesis_id: str | None = Field(default=None, min_length=1)
    strategy_name: str | None = None
    strategy_family: str = "rules_ts"
    strategy_module: str = ""
    model_type: str = "rules"
    model_class: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    param_grid: Annotated[
        list[dict[str, Any]], BeforeValidator(_none_param_grid_to_empty)
    ] = Field(default_factory=list)
    run_id: str | None = None
    research_intent_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Read and validate one experiment YAML; no registry or artifact I/O.

    A non-mapping document (empty file, list, scalar) is a ``ValidationError``
    as well, so callers get one fail-closed error type for every malformed
    spec.
    """

    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ExperimentSpec.model_validate(document)
