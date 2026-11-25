"""Typed strategy parameters and numeric-neighbor accounting.

``param_grid: []`` used to be indistinguishable from "this strategy has no
tunable numbers": an empty grid simply recorded ``neighbor_tested=False`` and
Stage 3 could still publish ``CANDIDATE_FOR_QUALIFICATION``.  That is the
neighbor-stability bypass this module closes.

Two declarations are supported, both agent-owned and neither able to inject
evaluator policy:

* a module-level ``params_model`` class subclassing :class:`ParamsModel`
  (Pydantic, ``extra="forbid"``, frozen) for every shipped strategy template;
* no model at all, in which case numeric values supplied in the spec's
  ``parameters`` mapping are the evidence of parameterisation.

The requirement itself is deliberately narrow and mechanical:

* a strategy that declares **any** numeric field (or supplies numeric values
  without a model) needs at least :data:`NEIGHBOR_MIN_DISTINCT` *distinct,
  genuine* numeric neighbors in ``param_grid`` before it can be a candidate;
* "genuine" means the entry explicitly sets at least one numeric field and the
  resulting configuration differs from the **effective** baseline (model
  defaults merged with spec parameters), so an entry that restates the baseline
  is degenerate; an entry that only edits a non-numeric key never counts;
* "distinct" means the merged numeric configuration differs from every other
  accepted entry, so two spellings of the same numbers count once;
* a strategy with zero numeric fields is exempt as ``parameter_free`` and does
  not need a fake degenerate grid to look tested.

Sign-safe stability arithmetic stays in :func:`warden.gates.evaluate_neighbor_stability`;
this module only decides *which* configurations are allowed to be evidence.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError

#: A neighbor gate is only satisfied by at least this many distinct, genuine
#: numeric perturbations.  One perturbed run is a spot check, not stability.
NEIGHBOR_MIN_DISTINCT = 2

EXEMPTION_PARAMETER_FREE = "parameter_free"

__all__ = [
    "EXEMPTION_PARAMETER_FREE",
    "NEIGHBOR_MIN_DISTINCT",
    "NeighborGridReport",
    "ParamsModel",
    "ParamsModelError",
    "ParamsModelReport",
    "classify_numeric_neighbors",
    "evaluate_neighbor_requirement",
    "numeric_field_names",
    "resolve_params_model",
    "validate_strategy_params",
]

_NUMERIC_SCALARS: tuple[type, ...] = (int, float, Decimal)


class ParamsModelError(ValueError):
    """Raised when declared strategy parameters are not a valid typed config.

    This is a fail-closed parse error, raised before a trial row, a selection
    attempt or a screen receipt exists -- a bad parameter declaration never
    costs a budget slot.
    """


class ParamsModel(BaseModel):
    """Base class for a strategy's typed parameter declaration.

    ``extra="forbid"`` makes a typo a parse error instead of a silently ignored
    key; ``frozen=True`` keeps the declaration immutable once validated.
    Warden validates only the declared field subset, because the runner injects
    its own keys (``target_symbols``) into the same mapping.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ParamsModelReport:
    """Outcome of validating one strategy's spec parameters against its model."""

    declared: bool
    name: str | None
    model: type[ParamsModel] | None
    numeric_fields: tuple[str, ...]
    effective: dict[str, Any]
    baseline_effective: dict[str, Any]
    baseline_numeric: dict[str, float]
    unknown_keys: tuple[str, ...]

    def to_diagnostics(self) -> dict[str, Any]:
        """Metadata-only record; never echoes parameter values into evidence."""

        return {
            "declared": self.declared,
            "name": self.name,
            "numeric_fields": list(self.numeric_fields),
            "unknown_keys": list(self.unknown_keys),
        }


@dataclass(frozen=True)
class NeighborGridReport:
    """Which ``param_grid`` entries are admissible neighbor evidence.

    ``evaluated_indices`` are the distinct configurations that actually differ
    from the effective baseline and are therefore legitimate stability evidence
    (numeric *or* categorical).  ``numeric_indices`` is the subset that changes a
    numeric field -- those are the ones a parameterised strategy needs at least
    :data:`NEIGHBOR_MIN_DISTINCT` of.
    """

    requested: int
    evaluated_indices: tuple[int, ...] = ()
    numeric_indices: tuple[int, ...] = ()
    degenerate_indices: tuple[int, ...] = ()
    duplicate_indices: tuple[int, ...] = ()
    non_numeric_indices: tuple[int, ...] = ()
    entries: tuple[dict[str, Any], ...] = field(default=())

    @property
    def distinct_numeric(self) -> int:
        return len(self.numeric_indices)

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "requested": int(self.requested),
            "evaluated_configurations": len(self.evaluated_indices),
            "distinct_numeric_neighbors": int(self.distinct_numeric),
            "min_distinct": NEIGHBOR_MIN_DISTINCT,
            "evaluated_indices": list(self.evaluated_indices),
            "numeric_indices": list(self.numeric_indices),
            "degenerate_indices": list(self.degenerate_indices),
            "duplicate_indices": list(self.duplicate_indices),
            "non_numeric_indices": list(self.non_numeric_indices),
        }


def _is_numeric_annotation(annotation: Any) -> bool:
    """True for ``int``/``float``/``Decimal`` and optional unions of them."""

    if annotation in _NUMERIC_SCALARS:
        return True
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        members = [a for a in get_args(annotation) if a is not type(None)]
        return bool(members) and all(_is_numeric_annotation(a) for a in members)
    return False


def _is_numeric_value(value: Any) -> bool:
    """Runtime numeric test used when a strategy declares no model.

    ``bool`` is excluded on purpose: a boolean flag is a switch, not a
    perturbation axis, and ``isinstance(True, int)`` would otherwise make every
    boolean parameter look like a number.
    """

    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return False
    return False


def numeric_field_names(model_cls: type[ParamsModel]) -> tuple[str, ...]:
    """Declared fields whose annotation is numeric, in declaration order."""

    return tuple(
        name
        for name, info in model_cls.model_fields.items()
        if _is_numeric_annotation(info.annotation)
    )


def _strategy_target(mod: Any) -> Any:
    """Mirror ``protocols.resolve_protocol_target`` without importing it."""

    candidate = getattr(mod, "strategy", None)
    if candidate is not None and (
        callable(getattr(candidate, "required_symbols", None))
        or callable(getattr(candidate, "build_estimator", None))
    ):
        return candidate
    return mod


def resolve_params_model(mod: Any) -> type[ParamsModel] | None:
    """Return the module's (or strategy object's) declared ``params_model``.

    A declaration that is not a :class:`ParamsModel` subclass is a parse error
    rather than an ignored attribute: a strategy cannot claim typed parameters
    with a plain dict or an unrelated Pydantic model.
    """

    for holder in (_strategy_target(mod), mod):
        declared = getattr(holder, "params_model", None)
        if declared is None:
            continue
        if isinstance(declared, type) and issubclass(declared, ParamsModel):
            return declared
        raise ParamsModelError(
            "params_model must be a subclass of warden.params_model.ParamsModel, "
            f"got {type(declared).__name__}."
        )
    return None


def _declared_subset(
    model_cls: type[ParamsModel], params: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    fields = model_cls.model_fields
    subset = {str(k): v for k, v in params.items() if str(k) in fields}
    unknown = tuple(sorted(str(k) for k in params if str(k) not in fields))
    return subset, unknown


def validate_strategy_params(mod: Any, params: Mapping[str, Any] | None) -> ParamsModelReport:
    """Validate spec parameters against the strategy's typed declaration.

    With a declared model the *effective* configuration is the model defaults
    overlaid by the declared subset -- that merged mapping, not the raw YAML
    subset, is the baseline every neighbor is measured against.
    """

    given = {str(k): v for k, v in dict(params or {}).items()}
    model_cls = resolve_params_model(mod)
    if model_cls is None:
        baseline = {k: float(v) for k, v in given.items() if _is_numeric_value(v)}
        return ParamsModelReport(
            declared=False,
            name=None,
            model=None,
            numeric_fields=tuple(sorted(baseline)),
            effective=given,
            baseline_effective=dict(given),
            baseline_numeric=baseline,
            unknown_keys=(),
        )

    subset, unknown = _declared_subset(model_cls, given)
    try:
        validated = model_cls.model_validate(subset)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ParamsModelError(
            f"{model_cls.__name__} rejected the declared parameters: {details}"
        ) from exc
    effective = validated.model_dump()
    fields = numeric_field_names(model_cls)
    baseline = {
        name: float(effective[name])
        for name in fields
        if effective.get(name) is not None and _is_numeric_value(effective[name])
    }
    baseline_effective = {**effective, **{k: given[k] for k in unknown}}
    return ParamsModelReport(
        declared=True,
        name=model_cls.__name__,
        model=model_cls,
        numeric_fields=fields,
        effective=effective,
        baseline_effective=baseline_effective,
        baseline_numeric=baseline,
        unknown_keys=unknown,
    )


def _entry_configuration(
    report: ParamsModelReport, raw: Mapping[str, Any], index: int
) -> tuple[dict[str, Any], dict[str, float], bool, int]:
    """Effective configuration a grid entry executes, its numeric view, and whether
    it perturbs a numeric field.

    The numeric flag is ``True`` only when at least one numeric field the entry
    *explicitly* sets carries a value different from the effective baseline.  A
    non-numeric edit -- changing ``target_symbols`` or a label -- can still move
    a numeric field back to its default, and an entry may restate a baseline
    number, but neither is a numeric perturbation.
    """

    if report.model is not None:
        subset, unknown = _declared_subset(report.model, raw)
        try:
            validated = report.model.model_validate(subset).model_dump()
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ParamsModelError(
                f"param_grid[{index}] is not a valid {report.model.__name__} configuration: {details}"
            ) from exc
        effective = {**validated, **{k: raw[k] for k in unknown}}
        numeric: dict[str, float] = {}
        for name in report.numeric_fields:
            value = validated.get(name)
            if value is not None and _is_numeric_value(value):
                numeric[name] = float(value)
        numeric_keys = sum(
            1 for name in report.numeric_fields if name in raw and _is_numeric_value(raw[name])
        )
        perturbed = any(
            name in raw
            and _is_numeric_value(raw[name])
            and numeric.get(name) != report.baseline_numeric.get(name)
            for name in report.numeric_fields
        )
        return effective, numeric, perturbed, numeric_keys

    effective = {**report.baseline_effective, **{str(k): v for k, v in raw.items()}}
    numeric = {k: float(v) for k, v in effective.items() if _is_numeric_value(v)}
    numeric_keys = sum(1 for value in raw.values() if _is_numeric_value(value))
    perturbed = any(
        _is_numeric_value(value) and float(value) != report.baseline_numeric.get(str(key))
        for key, value in raw.items()
    )
    return effective, numeric, perturbed, numeric_keys


def _canonical(mapping: Mapping[str, Any]) -> str:
    return json.dumps(mapping, sort_keys=True, default=str)


def classify_numeric_neighbors(
    grid: Sequence[Mapping[str, Any]],
    report: ParamsModelReport,
) -> NeighborGridReport:
    """Split ``param_grid`` into evaluated, degenerate and duplicate entries.

    Entries are never dropped from execution or accounting -- this report only
    decides which of them may count as evidence.  A type-invalid entry raises
    :class:`ParamsModelError` so a bad perturbation fails before any simulation,
    instead of quietly weakening the grid.
    """

    baseline_config = _canonical(report.baseline_effective)
    entries: list[dict[str, Any]] = []
    evaluated: list[int] = []
    numeric_neighbors: list[int] = []
    degenerate: list[int] = []
    duplicate: list[int] = []
    non_numeric: list[int] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(grid):
        if not isinstance(raw, Mapping):
            raise ParamsModelError(
                f"param_grid[{index}] must be a mapping of parameter names to values, "
                f"got {type(raw).__name__}."
            )
        effective, numeric, perturbed, _numeric_keys = _entry_configuration(report, raw, index)
        key = _canonical(effective)
        entries.append(
            {"index": index, "numeric": numeric, "perturbed_numeric": perturbed}
        )
        if key == baseline_config:
            # Restates the effective baseline: a fake neighbor.
            degenerate.append(index)
            continue
        if key in seen:
            duplicate.append(index)
            continue
        seen[key] = index
        evaluated.append(index)
        if perturbed:
            numeric_neighbors.append(index)
        else:
            non_numeric.append(index)
    return NeighborGridReport(
        requested=len(grid),
        evaluated_indices=tuple(evaluated),
        numeric_indices=tuple(numeric_neighbors),
        degenerate_indices=tuple(degenerate),
        duplicate_indices=tuple(duplicate),
        non_numeric_indices=tuple(non_numeric),
        entries=tuple(entries),
    )


def evaluate_neighbor_requirement(
    report: ParamsModelReport,
    grid_report: NeighborGridReport,
) -> dict[str, Any]:
    """Decide whether this trial needed numeric neighbors, and whether it had them.

    ``met`` is the gate outcome.  ``exemption`` is the explicit diagnostics label
    the acceptance contract names (``parameter_free``); it is present only when
    the strategy genuinely has no numeric axis to perturb.
    """

    grid_has_numbers = any(entry["perturbed_numeric"] for entry in grid_report.entries)
    required = bool(report.numeric_fields) or grid_has_numbers
    base = {
        "required": required,
        "min_distinct": NEIGHBOR_MIN_DISTINCT,
        "distinct_numeric_neighbors": int(grid_report.distinct_numeric),
        "params_model": report.to_diagnostics(),
        "grid": grid_report.to_diagnostics(),
    }
    if not required:
        return {
            **base,
            "met": True,
            "exemption": EXEMPTION_PARAMETER_FREE,
            "reason": "strategy declares no numeric parameter",
        }
    if grid_report.distinct_numeric >= NEIGHBOR_MIN_DISTINCT:
        return {
            **base,
            "met": True,
            "exemption": None,
            "reason": "distinct_numeric_neighbors",
        }
    return {
        **base,
        "met": False,
        "exemption": None,
        "reason": (
            "numeric parameters require at least "
            f"{NEIGHBOR_MIN_DISTINCT} distinct perturbed param_grid configurations; "
            f"got {grid_report.distinct_numeric} genuine of {grid_report.requested} requested"
        ),
    }
