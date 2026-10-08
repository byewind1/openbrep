"""Apply a frozen ExecutionPlan's typed parameter contract to generated HSF data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from openbrep.contracts.object_spec import parse_object_spec
from openbrep.hsf_project import GDLParameter
from openbrep.parameter_units import UnitValueError, normalize_typed_value


@dataclass(frozen=True)
class ParameterContractResult:
    ok: bool
    parameters: list[GDLParameter]
    added: tuple[str, ...] = ()
    reconciled: tuple[str, ...] = ()
    errors: tuple[dict[str, str], ...] = ()


def apply_planned_parameters(
    existing: list[GDLParameter],
    planning_artifact: dict[str, Any],
) -> ParameterContractResult:
    """Merge typed Plan parameters into a generated paramlist without losing values.

    The candidate ObjectSpec owns planned parameter identity/type/defaults. Explicit
    user values override defaults after unit normalization. Unplanned generated
    entries are preserved; this function never silently deletes model output.
    """
    raw_spec = planning_artifact.get("candidate_spec")
    parsed = parse_object_spec(raw_spec if isinstance(raw_spec, dict) else {})
    if not parsed.ok:
        return ParameterContractResult(
            False, list(existing), errors=tuple(error.to_dict() for error in parsed.errors),
        )

    sources = planning_artifact.get("parameter_sources")
    sources = sources if isinstance(sources, dict) else {}
    by_name = {parameter.name: index for index, parameter in enumerate(existing)}
    parameters = list(existing)
    added: list[str] = []
    reconciled: list[str] = []
    errors: list[dict[str, str]] = []

    for index, spec in enumerate(parsed.value.params):
        name = spec.gdl_name
        path = f"candidate_spec.params[{index}]"
        source = sources.get(name)
        explicit = isinstance(source, dict) and source.get("source") == "user_explicit"
        value = source.get("value") if explicit else spec.default_value
        unit = source.get("unit") if explicit else spec.unit
        existing_index = by_name.get(name)
        previous = parameters[existing_index] if existing_index is not None else None
        if value is None:
            if previous is not None:
                value = previous.value
                unit = None  # HSF values are already stored in GDL internal units.
            elif spec.required:
                errors.append({
                    "code": "MISSING_REQUIRED_VALUE",
                    "field_path": f"{path}.default_value",
                    "message": f"必需参数 {name} 没有用户值或计划默认值。",
                })
                continue
            else:
                value = _default_value(spec.type, spec.enum_values)
                unit = None

        type_tag = "String" if spec.type == "enum" else spec.type
        normalized = normalize_typed_value(
            type_tag, value, unit=unit,
            field_path=f"{path}.value",
        )
        if isinstance(normalized, UnitValueError):
            errors.append({
                "code": normalized.code,
                "field_path": normalized.field_path,
                "message": normalized.message,
            })
            continue
        if spec.type == "enum" and normalized.canonical not in spec.enum_values:
            errors.append({
                "code": "ENUM_VALUE_INVALID",
                "field_path": f"{path}.value",
                "message": f"参数 {name} 的值不在计划枚举集合中。",
            })
            continue

        if previous is None:
            parameters.append(GDLParameter(
                name=name,
                type_tag=type_tag,
                description=spec.description,
                value=normalized.canonical,
            ))
            by_name[name] = len(parameters) - 1
            added.append(name)
        else:
            next_parameter = replace(
                previous,
                type_tag=type_tag,
                description=spec.description or previous.description,
                value=normalized.canonical,
            )
            if next_parameter != previous:
                parameters[existing_index] = next_parameter
                reconciled.append(name)

    return ParameterContractResult(
        not errors, parameters, tuple(added), tuple(reconciled), tuple(errors),
    )


def _default_value(type_name: str, enum_values: list[Any]) -> Any:
    if type_name == "enum":
        return enum_values[0] if enum_values else ""
    if type_name == "String":
        return ""
    if type_name == "Boolean":
        return False
    return 0
