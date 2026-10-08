"""Build a validated, read-only candidate ObjectSpec and ExecutionPlan."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from openbrep.contracts.bindings import known_check_executor
from openbrep.contracts.object_spec import (
    ExecutionPlan,
    ObjectSpec,
    Observation,
    ObservationItem,
    ParamSpec,
    PlanRequirementMapping,
    PlanStep,
    Requirement,
    execution_plan_hash_dict,
    parse_execution_plan,
    parse_object_spec,
)
from openbrep.parameter_units import UnitValueError, normalize_typed_value


@dataclass(frozen=True)
class PlanningArtifact:
    status: str  # ready | needs_input | degraded
    candidate_spec: dict[str, Any] | None = None
    execution_plan: dict[str, Any] | None = None
    observations: tuple[dict[str, Any], ...] = ()
    parts: tuple[dict[str, str], ...] = ()
    parameter_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: tuple[dict[str, str], ...] = ()
    current_hsf: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "candidate_spec": self.candidate_spec,
            "execution_plan": self.execution_plan,
            "observations": list(self.observations),
            "parts": list(self.parts),
            "parameter_sources": self.parameter_sources,
            "issues": list(self.issues),
            "current_hsf": self.current_hsf,
        }


def build_planning_artifact(
    object_plan: Any,
    *,
    observations: Iterable[Observation] = (),
    conflict_fields: Iterable[str] = (),
    project: Any = None,
    user_input: str = "",
    domain_requirements: Iterable[dict[str, Any]] = (),
) -> PlanningArtifact:
    """Validate planner declarations and bind them to observations and source facts.

    The artifact is a proposal only. This function never writes HSF source,
    ObjectSpec, revision, or report files.
    """
    observation_rows = list(observations)
    conflict_fields = list(conflict_fields)
    issues: list[dict[str, str]] = []
    current_hsf = _current_hsf_facts(project)
    parts = _normalize_parts(getattr(object_plan, "parts", []), issues)
    typed_parameters = _normalize_typed_parameters(getattr(object_plan, "typed_parameters", []), issues)
    explicit_observation, parameter_sources = _apply_explicit_dimensions(user_input, typed_parameters, issues)
    if explicit_observation is not None:
        observation_rows.append(explicit_observation)
    observation_data = tuple(item.to_dict() for item in observation_rows)
    raw_mappings = list(getattr(object_plan, "requirement_mappings", []) or [])
    domain_requirement_rows = [dict(item) for item in domain_requirements]
    for gdl_name, source in parameter_sources.items():
        raw_mappings.append({
            "requirement_id": f"req-explicit-{gdl_name.casefold()}",
            "text": source["requirement"],
            "kind": "constraint",
            "part_refs": [],
            "parameter_refs": [source["param_id"]],
            "script_refs": [],
            "check_id": None,
            "scenario_refs": [],
            "source_refs": list(source["source_refs"]),
        })
    plan_seed = _object_plan_payload(object_plan)
    spec_id = _stable_id("spec", {
        "object_type": getattr(object_plan, "object_type", ""),
        "parts": parts,
        "params": typed_parameters,
        "requirements": raw_mappings,
        "domain_requirements": domain_requirement_rows,
        "observations": [item.observation_id for item in observation_rows],
    })
    plan_id = _stable_id("plan", {
        "planner": plan_seed,
        "spec_ref": spec_id,
        "observation_refs": [item.observation_id for item in observation_rows],
        "requirement_mappings": raw_mappings,
    })
    source_observation = next((item for item in observation_rows if item.observation_id != "user-request"), None)
    observation_ref = source_observation.observation_id if source_observation else (
        observation_rows[0].observation_id if observation_rows else ""
    )
    requirements, mappings = _build_requirements(raw_mappings, issues)
    for item in domain_requirement_rows:
        check_id = str(item.get("check_id") or "").strip() or None
        requirements.append(Requirement(
            requirement_id=str(item.get("requirement_id") or ""),
            text=str(item.get("text") or ""),
            kind=str(item.get("kind") or "check"),
            check_id=check_id,
            params=dict(item.get("params") or {}),
            status="defined" if check_id and known_check_executor(check_id) else "unknown",
            strength="required",
            source=str(item.get("source") or ""),
        ))
    mapped_texts = {item.text for item in requirements}
    if not raw_mappings:
        for index, text in enumerate(getattr(object_plan, "validation_checks", []) or []):
            requirement_id = f"req-plan-{index + 1}"
            requirements.append(Requirement(
                requirement_id=requirement_id,
                text=str(text),
                kind="check",
                check_id=None,
                status="unknown",
            ))
            issues.append({
                "code": "REQUIREMENT_MAPPING_MISSING",
                "field_path": f"requirements.{requirement_id}",
                "message": "planner 未提供可验证的构件/参数/脚本/检查/场景映射",
            })
    else:
        for index, text in enumerate(getattr(object_plan, "validation_checks", []) or []):
            if str(text) in mapped_texts:
                continue
            requirement_id = f"req-unmapped-{index + 1}"
            requirements.append(Requirement(
                requirement_id=requirement_id,
                text=str(text),
                kind="check",
                check_id=None,
                status="unknown",
            ))
            issues.append({
                "code": "REQUIREMENT_MAPPING_MISSING",
                "field_path": f"requirements.{requirement_id}",
                "message": "planner 的 validation_check 未连接到 typed requirement mapping",
            })

    param_fields = {"param_id", "gdl_name", "type", "unit", "description", "enum_values", "default_value", "required"}
    params = [ParamSpec(**{
        key: value for key, value in _canonical_parameter(item, issues).items()
        if key in param_fields
    }) for item in typed_parameters]
    spec = ObjectSpec(
        spec_id=spec_id,
        object_type=str(getattr(object_plan, "object_type", "unknown") or "unknown"),
        params=params,
        requirements=requirements,
        source_observation_id=observation_ref,
    )
    spec_result = parse_object_spec(spec.to_dict())
    if not spec_result.ok:
        issues.extend(error.to_dict() for error in spec_result.errors)
        return PlanningArtifact(
            status="needs_input" if _needs_input(conflict_fields, issues) else "degraded",
            observations=observation_data,
            parts=tuple(parts),
            parameter_sources=parameter_sources,
            issues=tuple(issues),
            current_hsf=current_hsf,
        )
    spec = spec_result.value
    requirement_status = {item.requirement_id: item.status for item in requirements}

    _validate_mapping_refs(mappings, parts, params, issues)
    steps = _build_steps(object_plan, params, requirements)
    execution = ExecutionPlan(
        plan_id=plan_id,
        spec_ref=spec.spec_id,
        observation_ref=observation_ref or "none",
        steps=steps,
        requirements=[
            Requirement(
                requirement_id=item.requirement_id,
                text=item.text,
                kind=item.kind,
                check_id=item.check_id,
                params=dict(item.params),
                status=requirement_status.get(item.requirement_id, item.status),
                strength=item.strength,
                source=item.source,
            )
            for item in spec.requirements
        ],
        requirement_mappings=mappings,
    )
    execution_result = parse_execution_plan(execution.to_dict(with_hash=False))
    if execution_result.ok:
        normalized_execution = execution_result.value
        normalized_execution.plan_hash = execution_plan_hash_dict(
            normalized_execution.to_dict(with_hash=False)
        )
        execution_result = parse_execution_plan(normalized_execution.to_dict())
    if not execution_result.ok:
        issues.extend(error.to_dict() for error in execution_result.errors)
        return PlanningArtifact(
            status="needs_input" if _needs_input(conflict_fields, issues) else "degraded",
            candidate_spec=spec.to_dict(),
            observations=observation_data,
            parts=tuple(parts),
            parameter_sources=parameter_sources,
            issues=tuple(issues),
            current_hsf=current_hsf,
        )

    if conflict_fields:
        for field_path in dict.fromkeys(str(path) for path in conflict_fields):
            issues.append({
                "code": "OBSERVATION_CONFLICT",
                "field_path": field_path,
                "message": "多图证据冲突，需要用户澄清后才能形成可执行计划",
            })
    missing_structure = not typed_parameters or not parts or not raw_mappings
    degraded = bool(getattr(object_plan, "degraded", False)) or bool(issues) or missing_structure
    clarification_needed = _needs_input(conflict_fields, issues)
    return PlanningArtifact(
        status="needs_input" if clarification_needed else "degraded" if degraded else "ready",
        candidate_spec=spec.to_dict(),
        execution_plan=execution_result.value.to_dict(),
        observations=observation_data,
        parts=tuple(parts),
        parameter_sources=parameter_sources,
        issues=tuple(issues),
        current_hsf=current_hsf,
    )


def _build_requirements(
    declarations: Any,
    issues: list[dict[str, str]],
) -> tuple[list[Requirement], list[PlanRequirementMapping]]:
    requirements: list[Requirement] = []
    mappings: list[PlanRequirementMapping] = []
    if not isinstance(declarations, list):
        issues.append({"code": "INVALID_REQUIREMENT_MAPPINGS", "field_path": "requirement_mappings", "message": "必须是对象数组"})
        return requirements, mappings
    for index, raw in enumerate(declarations):
        path = f"requirement_mappings[{index}]"
        if not isinstance(raw, dict):
            issues.append({"code": "INVALID_REQUIREMENT_MAPPING", "field_path": path, "message": "必须是对象"})
            continue
        requirement_id = str(raw.get("requirement_id") or "").strip()
        text = str(raw.get("text") or "").strip()
        check_id = raw.get("check_id")
        if not requirement_id or not text:
            issues.append({"code": "MISSING_REQUIREMENT_IDENTITY", "field_path": path, "message": "requirement_id 和 text 均必填"})
            continue
        if check_id and not known_check_executor(str(check_id)):
            issues.append({
                "code": "UNKNOWN_CHECK_EXECUTOR",
                "field_path": f"{path}.check_id",
                "message": f"框架未注册检查器 {check_id!r}；本计划将该检查标为 unknown",
            })
            check_id = None
        kind = str(raw.get("kind") or "check")
        if kind == "check" and not check_id and not any(
            issue.get("code") == "UNKNOWN_CHECK_EXECUTOR"
            and issue.get("field_path") == f"{path}.check_id"
            for issue in issues
        ):
            issues.append({
                "code": "REQUIREMENT_EXECUTOR_MISSING",
                "field_path": f"{path}.check_id",
                "message": "check 类型要求必须绑定框架已注册的检查器；未绑定时计划降级",
            })
        requirements.append(Requirement(
            requirement_id=requirement_id,
            text=text,
            kind=kind,
            check_id=str(check_id) if check_id else None,
            params={"part_refs": raw.get("part_refs", []), "parameter_refs": raw.get("parameter_refs", [])},
            status="unknown" if kind == "check" and not check_id else "defined",
            strength=str(raw.get("strength") or "legacy"),
            source=str(raw.get("source") or ""),
        ))
        mappings.append(PlanRequirementMapping(
            requirement_id=requirement_id,
            part_refs=_string_list(raw.get("part_refs")),
            parameter_refs=_string_list(raw.get("parameter_refs")),
            script_refs=_string_list(raw.get("script_refs")),
            scenario_refs=_string_list(raw.get("scenario_refs")),
            source_refs=_string_list(raw.get("source_refs")),
        ))
    return requirements, mappings


def _canonical_parameter(raw: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    item = dict(raw)
    type_tag = str(item.get("type") or "")
    unit = item.get("unit")
    value = item.get("default_value")
    # Planner models often attach human labels such as "count", "index" or
    # "material" to dimensionless GDL values. They are descriptive metadata,
    # not conversion units, so canonicalize them away before the strict typed
    # value parser runs. Unknown units on dimensional values still fail closed.
    non_dimensional_labels = {
        "Integer": {"count", "counts", "枚", "个", "件", "enum"},
        "PenColor": {"index", "pen", "pen index"},
        "Material": {"index", "material", "材质索引"},
        "FillPattern": {"index", "fill", "fill pattern"},
        "LineType": {"index", "line", "line type"},
        "Boolean": {"boolean", "bool", "1", "0/1"},
        "enum": {"enum"},
    }
    unit_label = str(unit or "").strip().lower()
    if unit_label in {"none", "null", "n/a", "na", "-"}:
        item["unit"] = None
        unit = None
    elif type_tag in non_dimensional_labels and unit_label in non_dimensional_labels[type_tag]:
        item["unit"] = None
        unit = None
    if value is None:
        return item
    field_path = f"typed_parameters.{item.get('param_id', '')}.default_value"
    if type_tag == "enum":
        if value not in (item.get("enum_values") or []):
            issues.append({"code": "ENUM_VALUE_INVALID", "field_path": field_path, "message": "enum 默认值不在声明集合中"})
        item["unit"] = None
        return item
    if type_tag in {"PenColor", "Material", "FillPattern", "LineType"}:
        try:
            indexed_value = int(str(value).strip())
        except (TypeError, ValueError):
            issues.append({
                "code": "NON_NUMERIC_INDEX_DEFAULT_IGNORED",
                "field_path": field_path,
                "message": f"{type_tag} 默认值不是数字索引；保留已有 HSF 值或使用运行时默认值。",
            })
            item["default_value"] = None
            item["unit"] = None
            item["required"] = False
            return item
        item["default_value"] = indexed_value
        item["unit"] = None
        return item

    normalized = normalize_typed_value(type_tag, value, unit=unit, field_path=field_path)
    if isinstance(normalized, UnitValueError):
        issues.append({"code": normalized.code, "field_path": normalized.field_path, "message": normalized.message})
        return item
    if type_tag == "Boolean":
        item["default_value"] = normalized.canonical == "1"
    elif type_tag in {"String", "enum"}:
        item["default_value"] = normalized.canonical
    else:
        item["default_value"] = normalized.number
    if type_tag == "Length":
        item["unit"] = "m"
    elif type_tag == "Angle":
        item["unit"] = "deg"
    else:
        item["unit"] = None
    return item


_EXPLICIT_DIMENSIONS = (
    ("width", "宽度|宽|width", "A", "p.width", "宽度"),
    ("depth", "深度|进深|深|depth", "B", "p.depth", "进深"),
    ("height", "总高度|高度|高|height", "ZZYZX", "p.height", "高度"),
)
_NUMBER = r"(?P<value>\d+(?:\.\d+)?)"
_UNIT = r"(?P<unit>mm|毫米|cm|厘米|m|米)"


def _apply_explicit_dimensions(
    instruction: str,
    typed_parameters: list[dict[str, Any]],
    issues: list[dict[str, str]],
) -> tuple[Observation | None, dict[str, dict[str, Any]]]:
    text = str(instruction or "")
    items: list[ObservationItem] = []
    sources: dict[str, dict[str, Any]] = {}
    for dimension, aliases, default_gdl_name, default_param_id, label in _EXPLICIT_DIMENSIONS:
        alias = rf"(?:{aliases})"
        patterns = (
            re.compile(rf"{alias}\s*(?:明确指定|指定|为|是|=|:|约)?\s*{_NUMBER}\s*{_UNIT}?", re.IGNORECASE),
            re.compile(rf"{_NUMBER}\s*{_UNIT}?\s*{alias}", re.IGNORECASE),
        )
        candidates = [match for pattern in patterns for match in pattern.finditer(text)]
        match = max(candidates, key=lambda candidate: _dimension_match_score(text, candidate)) if candidates else None
        if match is None:
            continue
        raw_value = float(match.group("value"))
        unit = match.group("unit")
        source_ref = f"user:explicit_dimension.{dimension}"
        if not unit:
            issues.append({
                "code": "MISSING_EXPLICIT_UNIT",
                "field_path": f"user_request.dimensions.{dimension}",
                "message": f"用户明确指定{label} {raw_value:g}，但未写长度单位；需确认 mm/cm/m 后再生成",
            })
            continue
        normalized = normalize_typed_value(
            "Length", raw_value, unit=unit,
            field_path=f"user_request.dimensions.{dimension}",
        )
        if isinstance(normalized, UnitValueError):
            issues.append({"code": normalized.code, "field_path": normalized.field_path, "message": normalized.message})
            continue
        target = next((item for item in typed_parameters if (
            item.get("gdl_name") == default_gdl_name or item.get("param_id") == default_param_id
        )), None)
        if target is None:
            target = {
                "param_id": default_param_id,
                "gdl_name": default_gdl_name,
                "type": "Length",
                "unit": "m",
                "description": f"用户明确指定的{label}",
                "enum_values": [],
                "required": True,
            }
            typed_parameters.append(target)
        if target.get("type") != "Length":
            issues.append({
                "code": "EXPLICIT_DIMENSION_TYPE_MISMATCH",
                "field_path": f"typed_parameters.{target.get('param_id', default_param_id)}.type",
                "message": f"{target.get('gdl_name')} 不是 Length 参数，不能承接用户明确{label}",
            })
            continue
        target["default_value"] = normalized.number
        target["unit"] = "m"
        target["value_source"] = "user_explicit"
        target["source_refs"] = [source_ref]
        value = normalized.number
        items.append(ObservationItem(
            field_path=str(target["param_id"]),
            status="observed",
            value=value,
            unit="m",
            confidence="high",
            note=f"用户明确指定 {raw_value:g} {unit}；覆盖参考图估计",
        ))
        sources[str(target["gdl_name"])] = {
            "param_id": str(target["param_id"]),
            "value": value,
            "unit": "m",
            "source": "user_explicit",
            "source_refs": [source_ref],
            "requirement": f"用户明确指定{label} {raw_value:g}{unit}（规范值 {value:g}m），高于参考图估计",
        }
    if not items:
        return None, sources
    return Observation(
        observation_id="user-explicit-dimensions",
        source="user_typed",
        source_refs=list(dict.fromkeys(
            ref for source in sources.values() for ref in source["source_refs"]
        )),
        items=items,
    ), sources


def _dimension_match_score(text: str, match: re.Match[str]) -> tuple[int, int]:
    context = text[max(0, match.start() - 18):min(len(text), match.end() + 18)]
    score = 1 if re.search(r"明确指定|指定|要求|做成|設為|设为|采用|採用|改为|改成|调整为|調整為", context) else 0
    if re.search(r"参考图估计|參考圖估計|图像估计|圖像估計|估计|估算|推测|推測", context):
        score -= 3
    return score, match.start()


def _normalize_parts(raw_parts: Any, issues: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(raw_parts, list):
        if raw_parts:
            issues.append({"code": "INVALID_PARTS", "field_path": "parts", "message": "parts 必须是对象数组"})
        return []
    parts: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_parts:
        if not isinstance(raw, dict):
            issues.append({"code": "INVALID_PART", "field_path": "parts", "message": "部件声明必须是对象"})
            continue
        part_id = str(raw.get("part_id") or "").strip()
        description = str(raw.get("description") or "").strip()
        if part_id and description and part_id not in seen:
            seen.add(part_id)
            parts.append({"part_id": part_id, "description": description})
        else:
            issues.append({"code": "INVALID_PART_IDENTITY", "field_path": "parts", "message": "部件 ID 必须唯一且 part_id/description 均非空"})
    return parts


def _normalize_typed_parameters(raw_params: Any, issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not isinstance(raw_params, list):
        if raw_params:
            issues.append({"code": "INVALID_TYPED_PARAMETERS", "field_path": "typed_parameters", "message": "typed_parameters 必须是对象数组"})
        return []
    output = []
    seen: set[str] = set()
    allowed = {
        "param_id", "gdl_name", "type", "unit", "description", "enum_values",
        "default_value", "required", "value_source", "source_refs",
    }
    for raw in raw_params:
        if not isinstance(raw, dict):
            issues.append({"code": "INVALID_TYPED_PARAMETER", "field_path": "typed_parameters", "message": "typed parameter 必须是对象"})
            continue
        item = {key: raw[key] for key in allowed if key in raw}
        param_id = str(item.get("param_id") or "").strip()
        if not param_id or param_id in seen:
            issues.append({"code": "INVALID_TYPED_PARAMETER_ID", "field_path": "typed_parameters.param_id", "message": "param_id 必须非空且唯一"})
            continue
        seen.add(param_id)
        item["param_id"] = param_id
        item["gdl_name"] = str(item.get("gdl_name") or "").strip()
        item["type"] = str(item.get("type") or "").strip()
        item["unit"] = item.get("unit")
        item["description"] = str(item.get("description") or "")
        item["enum_values"] = item.get("enum_values") or []
        item["required"] = bool(item.get("required", False))
        if item["type"].casefold() == "enum" and not item["enum_values"]:
            # GDL stores enum-like controls as String parameters plus VALUES.
            # Without a declared option set the planner has not supplied a
            # valid enum contract; preserve the selected label as a String
            # instead of rejecting the whole candidate.
            issues.append({
                "code": "ENUM_OPTIONS_MISSING_TREATED_AS_STRING",
                "field_path": f"typed_parameters.{param_id}.enum_values",
                "message": "枚举没有声明选项集合，按 String 参数保留当前默认值。",
            })
            item["type"] = "String"
            item["enum_values"] = []
        output.append(item)
    return output


def _validate_mapping_refs(
    mappings: list[PlanRequirementMapping],
    parts: list[dict[str, str]],
    params: list[ParamSpec],
    issues: list[dict[str, str]],
) -> None:
    part_ids = {item["part_id"] for item in parts}
    parameter_ids = {item.param_id for item in params}
    for mapping in mappings:
        for ref in mapping.part_refs:
            if ref not in part_ids:
                issues.append({"code": "UNKNOWN_PART_REF", "field_path": f"requirement_mappings.{mapping.requirement_id}.part_refs", "message": f"未声明部件 {ref!r}"})
        for ref in mapping.parameter_refs:
            if ref not in parameter_ids:
                issues.append({"code": "UNKNOWN_PARAMETER_REF", "field_path": f"requirement_mappings.{mapping.requirement_id}.parameter_refs", "message": f"未声明参数 {ref!r}"})
        if not mapping.part_refs and not mapping.parameter_refs and not mapping.script_refs and not mapping.scenario_refs:
            issues.append({"code": "UNBOUND_REQUIREMENT", "field_path": f"requirement_mappings.{mapping.requirement_id}", "message": "要求没有映射到部件、参数、脚本或场景"})


def _build_steps(object_plan: Any, params: list[ParamSpec], requirements: list[Requirement]) -> list[PlanStep]:
    steps = [PlanStep("step-create", "create_project", str(getattr(object_plan, "object_type", "object")), "建立可编辑 HSF 项目")]
    for script_key, target in (
        ("script_3d_strategy", "scripts/3d.gdl"),
        ("script_2d_strategy", "scripts/2d.gdl"),
        ("parameter_script_strategy", "scripts/1d.gdl"),
        ("ui_script_strategy", "scripts/ui.gdl"),
    ):
        details = list(getattr(object_plan, script_key, []) or [])
        if details:
            steps.append(PlanStep(f"step-{len(steps) + 1}", "modify_script", target, "；".join(str(item) for item in details)))
    for param in params:
        steps.append(PlanStep(f"step-{len(steps) + 1}", "set_param", param.gdl_name, param.description))
    for requirement in requirements:
        if requirement.check_id:
            steps.append(PlanStep(f"step-{len(steps) + 1}", "verify", requirement.check_id, requirement.text))
    return steps


def _current_hsf_facts(project: Any) -> dict[str, Any]:
    if project is None:
        return {"present": False, "parameters": [], "scripts": []}
    parameters = []
    for param in getattr(project, "parameters", []) or []:
        parameters.append({
            "name": str(getattr(param, "name", "")),
            "type": str(getattr(param, "type_tag", "")),
            "value": str(getattr(param, "value", "")),
            "fixed": bool(getattr(param, "is_fixed", False)),
        })
    scripts = []
    for script_type in getattr(project, "scripts", {}) or {}:
        value = getattr(script_type, "value", str(script_type))
        scripts.append(f"scripts/{value}")
    return {
        "present": True,
        "project_name": str(getattr(project, "name", "")),
        "parameters": parameters,
        "scripts": sorted(set(scripts)),
    }


def _object_plan_payload(object_plan: Any) -> dict[str, Any]:
    to_dict = getattr(object_plan, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    names = (
        "object_type", "geometry", "geometry_parts", "parameters", "parts",
        "typed_parameters", "requirement_mappings", "script_3d_strategy",
        "script_2d_strategy", "parameter_script_strategy", "ui_script_strategy",
        "validation_checks", "assumptions", "degraded",
    )
    return {name: getattr(object_plan, name, None) for name in names}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _needs_input(conflict_fields: Iterable[str], issues: list[dict[str, str]]) -> bool:
    return bool(list(conflict_fields)) or any(
        issue.get("code") in {
            "MISSING_EXPLICIT_UNIT", "EXPLICIT_DIMENSION_TYPE_MISMATCH",
            "INVALID_UNIT", "UNIT_MISMATCH",
        }
        for issue in issues
    )


def _stable_id(prefix: str, value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
