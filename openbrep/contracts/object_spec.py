"""U03-A 可执行领域对象合同：Observation / ObjectSpec / ExecutionPlan。

总则 §2/§3 的三个合同（schema v2 兼容读取 v1）：

- **Observation** 记录观察/推断/未知：来自视觉提取、用户 typed 需求或手工
  输入；不承载写授权。
- **ObjectSpec** 是随源码采用的长期领域合同：稳定参数 ID → GDL 名映射、
  typed 类型/规范单位（Length=米、Angle=度，复用 openbrep.parameter_units）、
  受限关系表达、requirements/checks。**参数当前值以 HSF 为权威**——spec 里
  不维持第二份当前值（只有 default_value/描述性元数据）。
- **ExecutionPlan** 是执行前候选规格与策略：引用 spec/observation、受限步骤
  列表、Plan hash（规范化 JSON sha256）。可执行与 degraded 明确。

校验合同（验收口径）：
- 重复 ID 拒绝；**check_id 只允许框架注册执行器**（`register_check_executor`/
  内置集），未知 executor 拒绝（字段路径指位）；
- 量纲/enum 错拒绝（spec 值必须是规范内部单位，不做输入换算——换算属于
  输入适配层，归 U01-A 的 parameter_units）；
- **受限关系语法之外的关系不静默丢弃**：保留为 ``status="unknown"`` 显式
  透出（不支持通用表达式 DSL）；
- Plan hash 不匹配拒绝。

适配（旧数据只读进入新合同，不改旧行为）：旧 ``ModelingPlan``（视觉提取）
→ Observation；旧 ``GDLObjectPlan``（planner 策略）→ ExecutionPlan 候选
（validation_checks 无法映射执行器的 requirement 保持无 executor = unknown）。

prompt 合同：本模块不进入任何 LLM prompt；planner 模板切换归 U05-B。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, SCHEMA_VERSION})
REQUIREMENT_STRENGTHS = frozenset({"required", "advisory", "legacy"})

# ── 框架注册执行器（U03-A 平面入口；U03-B 起唯一权威在 contracts.bindings）──

from openbrep.contracts.bindings import (  # noqa: E402, I001  （模块底部适配，避免循环）
    BUILTIN_CHECK_EXECUTORS as _BUILTIN_EXECUTOR_IDS,
    known_check_executor as _bindings_known_executor,
    register_check_executor as _bindings_register,
    reset_executor_specs_for_tests as _bindings_reset,
)

BUILTIN_CHECK_EXECUTORS: frozenset[str] = frozenset(_BUILTIN_EXECUTOR_IDS)


def register_check_executor(check_id: str) -> bool:
    """注册框架检查执行器（U03-A 签名；U03-B 起委托 bindings 规格注册表）。"""
    return _bindings_register(check_id)


def known_check_executor(check_id: str) -> bool:
    return _bindings_known_executor(check_id)


def reset_check_executors_for_tests() -> None:
    """测试辅助：恢复到内置集（测试注册的执行器不外泄）。"""
    _bindings_reset()


# ── 受限关系语法 ─────────────────────────────────────────────

ALLOWED_RELATION_OPS: frozenset[str] = frozenset({"==", "!=", ">", ">=", "<", "<="})
RelationOperand = dict  # {"param": "<param_id>"} | {"const": <typed value>}


# ── 解析错误（字段路径合同）──────────────────────────────────


@dataclass(frozen=True)
class ContractError:
    code: str          # SCHEMA_VERSION/MISSING_FIELD/DUPLICATE_ID/UNKNOWN_EXECUTOR/
                       # DIMENSION_MISMATCH/ENUM_VALUE_INVALID/NOT_FINITE/INVALID_VALUE/
                       # HASH_MISMATCH
    field_path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field_path": self.field_path, "message": self.message}


@dataclass
class ParseResult:
    value: Any = None
    errors: list[ContractError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.value is not None


def _err(code: str, field_path: str, message: str) -> ContractError:
    return ContractError(code=code, field_path=field_path, message=message)


# ── 数据模型 ─────────────────────────────────────────────────


@dataclass
class ObservationItem:
    """一条观察：field_path 上的 typed 值，observed/inferred/unknown 三态。"""

    field_path: str
    status: str                      # observed | inferred | unknown
    value: Any = None
    unit: Optional[str] = None       # 规范单位（Length=m/Angle=deg），None=无量纲
    confidence: str = "unknown"      # high | low | unknown
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "status": self.status,
            "value": self.value,
            "unit": self.unit,
            "confidence": self.confidence,
            "note": self.note,
        }


@dataclass
class Observation:
    observation_id: str
    source: str                      # vision_extraction | user_typed | manual | synthetic
    source_refs: list[str] = field(default_factory=list)   # 提取工件 sha256 / 图像引用等
    items: list[ObservationItem] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "source": self.source,
            "source_refs": list(self.source_refs),
            "items": [i.to_dict() for i in self.items],
        }


@dataclass
class ParamSpec:
    """长期参数合同：稳定 ID → GDL 名 + 类型。**无当前值槽位**（HSF 权威）。"""

    param_id: str                    # 稳定 ID（如 "p.shelf_height"）
    gdl_name: str                    # 映射的 GDL 参数名
    type: str                        # Length | Angle | RealNum | Integer | Boolean | String | enum
    unit: Optional[str] = None       # 规范单位：Length→"m"，Angle→"deg"，其余 None
    description: str = ""
    enum_values: list = field(default_factory=list)   # type=enum 时必填
    default_value: Any = None        # 规范单位下的默认值（非当前值）
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "param_id": self.param_id,
            "gdl_name": self.gdl_name,
            "type": self.type,
            "unit": self.unit,
            "description": self.description,
            "enum_values": list(self.enum_values),
            "default_value": self.default_value,
            "required": self.required,
        }


@dataclass
class Requirement:
    """要求/检查声明：WHAT（文本）+ 可选的注册执行器 check_id。"""

    requirement_id: str
    text: str
    kind: str = "check"              # check | constraint | assumption
    check_id: Optional[str] = None   # 必须是框架注册执行器；None=advisory（unknown）
    params: dict = field(default_factory=dict)       # 执行器的 typed 参数
    status: str = "defined"          # defined | unknown（语法不支持时显式 unknown）
    strength: str = "legacy"         # required | advisory | legacy

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "text": self.text,
            "kind": self.kind,
            "check_id": self.check_id,
            "params": dict(self.params),
            "status": self.status,
            "strength": self.strength,
        }


@dataclass
class Relation:
    """受限关系：left <op> right；语法外 → status=unknown 显式保留。"""

    relation_id: str
    left: str                        # param_id
    op: str
    right: RelationOperand
    status: str = "defined"          # defined | unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "left": self.left,
            "op": self.op,
            "right": dict(self.right),
            "status": self.status,
        }


@dataclass
class ObjectSpec:
    spec_id: str
    object_type: str
    params: list[ParamSpec] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    source_observation_id: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "spec_id": self.spec_id,
            "object_type": self.object_type,
            "source_observation_id": self.source_observation_id,
            "params": [p.to_dict() for p in self.params],
            "requirements": [r.to_dict() for r in self.requirements],
            "relations": [r.to_dict() for r in self.relations],
        }


@dataclass
class PlanStep:
    step_id: str
    kind: str                        # create_project | modify_script | set_param | verify
    target: str                      # 脚本路径 / 参数名 / check_id
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
        }


@dataclass
class PlanRequirementMapping:
    """Trace how one requirement is covered by parts, parameters, scripts and scenarios."""

    requirement_id: str
    part_refs: list[str] = field(default_factory=list)
    parameter_refs: list[str] = field(default_factory=list)
    script_refs: list[str] = field(default_factory=list)
    scenario_refs: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "part_refs": list(self.part_refs),
            "parameter_refs": list(self.parameter_refs),
            "script_refs": list(self.script_refs),
            "scenario_refs": list(self.scenario_refs),
            "source_refs": list(self.source_refs),
        }


PLAN_STEP_KINDS = frozenset({"create_project", "modify_script", "set_param", "verify"})


@dataclass
class ExecutionPlan:
    plan_id: str
    spec_ref: str                    # ObjectSpec.spec_id
    observation_ref: str             # Observation.observation_id
    steps: list[PlanStep] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)   # 冻结自 spec
    requirement_mappings: list[PlanRequirementMapping] = field(default_factory=list)
    plan_hash: str = ""              # canonical JSON（不含 plan_hash 字段）的 sha256
    schema_version: int = SCHEMA_VERSION

    def to_dict(self, *, with_hash: bool = True) -> dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "spec_ref": self.spec_ref,
            "observation_ref": self.observation_ref,
            "steps": [s.to_dict() for s in self.steps],
            "requirements": [r.to_dict() for r in self.requirements],
        }
        if self.requirement_mappings:
            data["requirement_mappings"] = [m.to_dict() for m in self.requirement_mappings]
        if with_hash:
            data["plan_hash"] = self.plan_hash or execution_plan_hash_dict(data)
        return data


def execution_plan_hash_dict(plan_data: dict[str, Any]) -> str:
    """Plan hash：规范化 JSON（sort_keys，不含 plan_hash 键）的 sha256。"""
    canonical = {k: v for k, v in plan_data.items() if k != "plan_hash"}
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


# ── parse/validate ───────────────────────────────────────────

_PARAM_TYPES = frozenset({
    "Length", "Angle", "RealNum", "Integer", "Boolean", "String", "enum",
    "PenColor", "Material", "FillPattern", "LineType",
})
_CANONICAL_UNITS = {"Length": "m", "Angle": "deg"}
_INTEGER_INDEX_TYPES = frozenset({"Integer", "PenColor", "Material", "FillPattern", "LineType"})


def _validate_typed_spec_value(
    type_tag: str, enum_values: list, value: Any, field_path: str
) -> Optional[ContractError]:
    """spec 值必须是规范内部单位（不做输入换算；换算归 parameter_units 输入层）。"""
    if value is None:
        return None
    if type_tag == "enum":
        if value not in enum_values:
            return _err(
                "ENUM_VALUE_INVALID", field_path,
                f"枚举值 {value!r} 不在声明集合 {enum_values!r} 内",
            )
        return None
    if type_tag == "Boolean":
        if not isinstance(value, bool):
            return _err("INVALID_VALUE", field_path, "Boolean 值必须是 true/false")
        return None
    if type_tag == "String":
        if not isinstance(value, str):
            return _err("INVALID_VALUE", field_path, "String 值必须是字符串")
        return None
    # 数值域
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _err("INVALID_VALUE", field_path, f"{type_tag} 值必须是数字")
    if not math.isfinite(float(value)):
        return _err("NOT_FINITE", field_path, "值不能是 NaN 或无穷")
    if type_tag in _INTEGER_INDEX_TYPES and float(value) != int(value):
        return _err("INVALID_VALUE", field_path, "Integer 值必须是整数")
    return None


def parse_observation(data: dict[str, Any]) -> ParseResult:
    errors: list[ContractError] = []
    if int(data.get("schema_version") or 0) not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(_err("SCHEMA_VERSION", "schema_version", "仅支持 1/2"))
    observation_id = str(data.get("observation_id") or "").strip()
    if not observation_id:
        errors.append(_err("MISSING_FIELD", "observation_id", "缺 observation_id"))
    source = str(data.get("source") or "").strip()
    if not source:
        errors.append(_err("MISSING_FIELD", "source", "缺 source（观察来源）"))
    items: list[ObservationItem] = []
    seen_paths: set[str] = set()
    for idx, raw in enumerate(data.get("items") or []):
        path = f"items[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        field_path = str(raw.get("field_path") or "").strip()
        if not field_path:
            errors.append(_err("MISSING_FIELD", f"{path}.field_path", "缺 field_path"))
        elif field_path in seen_paths:
            errors.append(_err("DUPLICATE_ID", f"{path}.field_path", f"重复观察路径 {field_path!r}"))
        seen_paths.add(field_path)
        status = str(raw.get("status") or "observed")
        if status not in ("observed", "inferred", "unknown"):
            errors.append(_err(
                "INVALID_VALUE", f"{path}.status",
                f"status 必须是 observed/inferred/unknown，实际 {status!r}",
            ))
            status = "unknown"
        confidence = str(raw.get("confidence") or "unknown")
        if confidence not in ("high", "low", "unknown"):
            confidence = "unknown"
        unit = raw.get("unit")
        if unit is not None and not isinstance(unit, str):
            errors.append(_err("INVALID_VALUE", f"{path}.unit", "unit 必须是字符串或 null"))
            unit = None
        items.append(ObservationItem(
            field_path=field_path, status=status, value=raw.get("value"),
            unit=unit, confidence=confidence, note=str(raw.get("note") or ""),
        ))
    if errors:
        return ParseResult(errors=errors)
    return ParseResult(value=Observation(
        observation_id=observation_id, source=source,
        source_refs=[str(r) for r in (data.get("source_refs") or [])],
        items=items,
    ))


def parse_object_spec(data: dict[str, Any]) -> ParseResult:
    """ObjectSpec 解析 + 全量校验（重复 ID / 未知 executor / 量纲 / enum）。"""
    errors: list[ContractError] = []
    if int(data.get("schema_version") or 0) not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(_err("SCHEMA_VERSION", "schema_version", "仅支持 1/2"))
    input_schema_version = int(data.get("schema_version") or 0)
    spec_id = str(data.get("spec_id") or "").strip()
    if not spec_id:
        errors.append(_err("MISSING_FIELD", "spec_id", "缺 spec_id"))
    object_type = str(data.get("object_type") or "").strip()
    if not object_type:
        errors.append(_err("MISSING_FIELD", "object_type", "缺 object_type"))

    params: list[ParamSpec] = []
    param_ids: set[str] = set()
    gdl_names: set[str] = set()
    for idx, raw in enumerate(data.get("params") or []):
        path = f"params[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        param_id = str(raw.get("param_id") or "").strip()
        if not param_id:
            errors.append(_err("MISSING_FIELD", f"{path}.param_id", "缺 param_id"))
        elif param_id in param_ids:
            errors.append(_err("DUPLICATE_ID", f"{path}.param_id", f"重复 param_id {param_id!r}"))
        param_ids.add(param_id)
        gdl_name = str(raw.get("gdl_name") or "").strip()
        if not gdl_name:
            errors.append(_err("MISSING_FIELD", f"{path}.gdl_name", "缺 gdl_name"))
        elif gdl_name in gdl_names:
            errors.append(_err("DUPLICATE_ID", f"{path}.gdl_name", f"gdl_name 重复映射 {gdl_name!r}"))
        gdl_names.add(gdl_name)
        type_tag = str(raw.get("type") or "")
        if type_tag not in _PARAM_TYPES:
            errors.append(_err("INVALID_VALUE", f"{path}.type", f"未知参数类型 {type_tag!r}"))
            continue
        unit = raw.get("unit")
        canonical = _CANONICAL_UNITS.get(type_tag)
        if canonical is not None:
            if unit is not None and unit != canonical:
                errors.append(_err(
                    "DIMENSION_MISMATCH", f"{path}.unit",
                    f"{type_tag} 的规范单位是 {canonical!r}，实际 {unit!r}"
                    "（spec 只收规范内部单位；输入换算走 parameter_units）",
                ))
            unit = canonical
        elif unit is not None:
            errors.append(_err(
                "DIMENSION_MISMATCH", f"{path}.unit",
                f"{type_tag} 是无量纲类型，不允许 unit={unit!r}",
            ))
        enum_values = raw.get("enum_values") or []
        if type_tag == "enum" and not enum_values:
            errors.append(_err("MISSING_FIELD", f"{path}.enum_values", "enum 类型必须声明枚举集"))
        value_err = _validate_typed_spec_value(
            type_tag, enum_values, raw.get("default_value"), f"{path}.default_value"
        )
        if value_err:
            errors.append(value_err)
        params.append(ParamSpec(
            param_id=param_id, gdl_name=gdl_name, type=type_tag, unit=unit,
            description=str(raw.get("description") or ""),
            enum_values=list(enum_values), default_value=raw.get("default_value"),
            required=bool(raw.get("required", False)),
        ))

    requirements: list[Requirement] = []
    req_ids: set[str] = set()
    for idx, raw in enumerate(data.get("requirements") or []):
        path = f"requirements[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        requirement_id = str(raw.get("requirement_id") or "").strip()
        if not requirement_id:
            errors.append(_err("MISSING_FIELD", f"{path}.requirement_id", "缺 requirement_id"))
        elif requirement_id in req_ids:
            errors.append(_err(
                "DUPLICATE_ID", f"{path}.requirement_id", f"重复 requirement_id {requirement_id!r}"
            ))
        req_ids.add(requirement_id)
        text = str(raw.get("text") or "").strip()
        if not text:
            errors.append(_err("MISSING_FIELD", f"{path}.text", "缺 text（WHAT 声明）"))
        kind = str(raw.get("kind") or "check")
        if kind not in ("check", "constraint", "assumption"):
            errors.append(_err("INVALID_VALUE", f"{path}.kind", f"未知 kind {kind!r}"))
        requirement_status = str(raw.get("status") or (
            "unknown" if kind == "check" and not raw.get("check_id") else "defined"
        ))
        if requirement_status not in {"defined", "unknown"}:
            errors.append(_err("INVALID_VALUE", f"{path}.status", "status 必须是 defined/unknown"))
        strength = str(raw.get("strength") or "legacy")
        if input_schema_version >= 2 and "strength" not in raw:
            errors.append(_err(
                "MISSING_FIELD", f"{path}.strength",
                "schema v2 要求显式声明 required/advisory/legacy",
            ))
        if strength not in REQUIREMENT_STRENGTHS:
            errors.append(_err(
                "INVALID_VALUE", f"{path}.strength",
                "strength 必须是 required/advisory/legacy",
            ))
        check_id = raw.get("check_id")
        if check_id is not None:
            check_id = str(check_id).strip()
            if not known_check_executor(check_id):
                errors.append(_err(
                    "UNKNOWN_EXECUTOR", f"{path}.check_id",
                    f"check_id {check_id!r} 未在框架注册（check 只允许框架注册执行器）",
                ))
        requirements.append(Requirement(
            requirement_id=requirement_id, text=text, kind=kind,
            check_id=check_id or None, params=dict(raw.get("params") or {}),
            status=requirement_status, strength=strength,
        ))

    relations: list[Relation] = []
    rel_ids: set[str] = set()
    for idx, raw in enumerate(data.get("relations") or []):
        path = f"relations[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        relation_id = str(raw.get("relation_id") or "").strip()
        if not relation_id:
            errors.append(_err("MISSING_FIELD", f"{path}.relation_id", "缺 relation_id"))
        elif relation_id in rel_ids:
            errors.append(_err("DUPLICATE_ID", f"{path}.relation_id", f"重复 relation_id {relation_id!r}"))
        rel_ids.add(relation_id)
        op = str(raw.get("op") or "")
        right = raw.get("right") or {}
        # 受限语法之外：不拒绝、不静默丢弃——显式 unknown（不支持通用表达式 DSL）
        status = "defined"
        if op not in ALLOWED_RELATION_OPS or not isinstance(right, dict) or not right:
            status = "unknown"
        elif "param" not in right and "const" not in right:
            status = "unknown"
        elif "param" in right and str(right["param"]) not in param_ids:
            errors.append(_err(
                "INVALID_VALUE", f"{path}.right.param",
                f"关系引用了未声明的参数 {right['param']!r}",
            ))
        relations.append(Relation(
            relation_id=relation_id, left=str(raw.get("left") or ""),
            op=op, right=dict(right), status=status,
        ))

    if errors:
        return ParseResult(errors=errors)
    spec = ObjectSpec(
        spec_id=spec_id, object_type=object_type, params=params,
        requirements=requirements, relations=relations,
        source_observation_id=str(data.get("source_observation_id") or ""),
    )
    # 关系引用完整性（left 侧同样校验，放在 value 组装前统一报错）
    for idx, rel in enumerate(spec.relations):
        if rel.status == "defined" and rel.left not in param_ids:
            errors.append(_err(
                "INVALID_VALUE", f"relations[{idx}].left",
                f"关系引用了未声明的参数 {rel.left!r}",
            ))
    if errors:
        return ParseResult(errors=errors)
    return ParseResult(value=spec)


def parse_execution_plan(data: dict[str, Any]) -> ParseResult:
    errors: list[ContractError] = []
    if int(data.get("schema_version") or 0) not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(_err("SCHEMA_VERSION", "schema_version", "仅支持 1/2"))
    input_schema_version = int(data.get("schema_version") or 0)
    plan_id = str(data.get("plan_id") or "").strip()
    if not plan_id:
        errors.append(_err("MISSING_FIELD", "plan_id", "缺 plan_id"))
    spec_ref = str(data.get("spec_ref") or "").strip()
    if not spec_ref:
        errors.append(_err("MISSING_FIELD", "spec_ref", "缺 spec_ref（候选规格引用）"))

    steps: list[PlanStep] = []
    step_ids: set[str] = set()
    for idx, raw in enumerate(data.get("steps") or []):
        path = f"steps[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        step_id = str(raw.get("step_id") or "").strip()
        if not step_id:
            errors.append(_err("MISSING_FIELD", f"{path}.step_id", "缺 step_id"))
        elif step_id in step_ids:
            errors.append(_err("DUPLICATE_ID", f"{path}.step_id", f"重复 step_id {step_id!r}"))
        step_ids.add(step_id)
        kind = str(raw.get("kind") or "")
        if kind not in PLAN_STEP_KINDS:
            errors.append(_err("INVALID_VALUE", f"{path}.kind", f"未知步骤类型 {kind!r}"))
        steps.append(PlanStep(
            step_id=step_id, kind=kind,
            target=str(raw.get("target") or ""), detail=str(raw.get("detail") or ""),
        ))

    requirements: list[Requirement] = []
    req_ids: set[str] = set()
    for idx, raw in enumerate(data.get("requirements") or []):
        path = f"requirements[{idx}]"
        requirement_id = str(raw.get("requirement_id") or "").strip() if isinstance(raw, dict) else ""
        if not requirement_id:
            errors.append(_err("MISSING_FIELD", f"{path}.requirement_id", "缺 requirement_id"))
        elif requirement_id in req_ids:
            errors.append(_err("DUPLICATE_ID", f"{path}.requirement_id", f"重复 requirement_id {requirement_id!r}"))
        req_ids.add(requirement_id)
        check_id = raw.get("check_id") if isinstance(raw, dict) else None
        if check_id is not None and not known_check_executor(str(check_id).strip()):
            errors.append(_err(
                "UNKNOWN_EXECUTOR", f"{path}.check_id",
                f"check_id {check_id!r} 未在框架注册",
            ))
        requirement_kind = str(raw.get("kind") or "check") if isinstance(raw, dict) else "check"
        requirement_status = str(raw.get("status") or (
            "unknown" if requirement_kind == "check" and not check_id else "defined"
        )) if isinstance(raw, dict) else "unknown"
        strength = str(raw.get("strength") or "legacy") if isinstance(raw, dict) else "legacy"
        if input_schema_version >= 2 and isinstance(raw, dict) and "strength" not in raw:
            errors.append(_err(
                "MISSING_FIELD", f"{path}.strength",
                "schema v2 要求显式声明 required/advisory/legacy",
            ))
        if strength not in REQUIREMENT_STRENGTHS:
            errors.append(_err(
                "INVALID_VALUE", f"{path}.strength",
                "strength 必须是 required/advisory/legacy",
            ))
        requirements.append(Requirement(
            requirement_id=requirement_id,
            text=str(raw.get("text") or "") if isinstance(raw, dict) else "",
            kind=requirement_kind,
            check_id=str(check_id).strip() if check_id else None,
            params=dict(raw.get("params") or {}) if isinstance(raw, dict) else {},
            status=requirement_status,
            strength=strength,
        ))

    requirement_ids = {item.requirement_id for item in requirements}
    mappings: list[PlanRequirementMapping] = []
    mapping_ids: set[str] = set()
    for idx, raw in enumerate(data.get("requirement_mappings") or []):
        path = f"requirement_mappings[{idx}]"
        if not isinstance(raw, dict):
            errors.append(_err("INVALID_VALUE", path, "必须是对象"))
            continue
        allowed_fields = {
            "requirement_id", "part_refs", "parameter_refs", "script_refs",
            "scenario_refs", "source_refs",
        }
        for unknown in sorted(set(raw) - allowed_fields):
            errors.append(_err("INVALID_VALUE", f"{path}.{unknown}", "未知映射字段"))
        req_id = str(raw.get("requirement_id") or "").strip()
        if not req_id or req_id not in requirement_ids:
            errors.append(_err("INVALID_VALUE", f"{path}.requirement_id", "必须引用本计划中已声明的 requirement"))
        if req_id in mapping_ids:
            errors.append(_err("DUPLICATE_ID", f"{path}.requirement_id", f"重复映射 {req_id!r}"))
        mapping_ids.add(req_id)
        refs: dict[str, list[str]] = {}
        for field_name in ("part_refs", "parameter_refs", "script_refs", "scenario_refs", "source_refs"):
            raw_refs = raw.get(field_name) or []
            if not isinstance(raw_refs, list) or any(not isinstance(value, str) or not value.strip() for value in raw_refs):
                errors.append(_err("INVALID_VALUE", f"{path}.{field_name}", "必须是非空字符串数组"))
                refs[field_name] = []
            else:
                refs[field_name] = list(dict.fromkeys(value.strip() for value in raw_refs))
        for script_ref in refs["script_refs"]:
            if not script_ref.startswith("scripts/") or not script_ref.endswith(".gdl") or ".." in script_ref.split("/"):
                errors.append(_err("INVALID_VALUE", f"{path}.script_refs", f"不是受支持的 HSF 脚本路径：{script_ref!r}"))
        mappings.append(PlanRequirementMapping(requirement_id=req_id, **refs))

    if errors:
        return ParseResult(errors=errors)
    plan = ExecutionPlan(
        plan_id=plan_id, spec_ref=spec_ref,
        observation_ref=str(data.get("observation_ref") or ""),
        steps=steps, requirements=requirements,
        requirement_mappings=mappings,
        plan_hash=str(data.get("plan_hash") or ""),
    )
    provided_hash = str(data.get("plan_hash") or "")
    if provided_hash:
        actual = execution_plan_hash_dict(plan.to_dict(with_hash=False))
        if actual != provided_hash:
            return ParseResult(errors=[_err(
                "HASH_MISMATCH", "plan_hash",
                f"Plan hash 不匹配（声明 {provided_hash[:12]}… 实算 {actual[:12]}…）",
            )])
    return ParseResult(value=plan)


# ── 适配器（旧数据只读进入新合同）────────────────────────────


def observation_from_modeling_plan(plan, *, source_refs: Optional[list[str]] = None) -> Observation:
    """旧 ModelingPlan（视觉提取）→ Observation（只读适配；不改 hint 行为）。

    字段 → observed（confidence 透传）；degraded/critic_degraded → 全部
    unknown（提取不可信必须可见）；raw_description → inferred。
    """
    from openbrep.vision.extraction_store import plan_to_dict

    data = plan_to_dict(plan)
    items: list[ObservationItem] = []
    degraded = bool(data.get("degraded")) or bool(data.get("critic_degraded"))
    fields = data.get("fields") or {}
    domain_declarations: dict = {}
    domain_aliases: dict[str, tuple[str, ...]] = {}
    try:
        from openbrep.domain_skills import DomainSkillRegistry

        loaded = DomainSkillRegistry.builtin().load(str(data.get("schema_name") or ""))
        if loaded.ok and loaded.skill is not None:
            domain_declarations = loaded.skill.manifest["observation"]["fields"]
            if loaded.skill.skill_id == "lattice_window":
                from openbrep.vision.domain_skill_schema import (
                    domain_observation_aliases,
                    normalize_domain_observation_fields,
                )

                domain_aliases = domain_observation_aliases(loaded.skill.skill_id)
                fields = normalize_domain_observation_fields(loaded.skill.skill_id, fields)
    except Exception:
        domain_declarations = {}
    observed_paths = list(dict.fromkeys([*domain_declarations, *fields]))
    for key in observed_paths:
        value = fields.get(key)
        declaration = domain_declarations.get(key, {})
        confidence_map = data.get("confidence") or {}
        confidence = confidence_map.get(key, "unknown")
        if confidence == "unknown":
            confidence = next((confidence_map.get(alias) for alias in domain_aliases.get(key, ()) if alias in confidence_map), "unknown")
        evidence_map = data.get("evidence") or {}
        evidence = evidence_map.get(key)
        if not evidence:
            evidence = next((evidence_map.get(alias) for alias in domain_aliases.get(key, ()) if alias in evidence_map), None)
        if key not in fields or degraded or value is None:
            items.append(ObservationItem(
                field_path=key, status="unknown", value=None,
                confidence="unknown" if degraded else confidence,
                unit=declaration.get("unit"),
                note=(
                    "提取降级" if degraded else
                    "领域Skill要求澄清" if declaration.get("unknown_policy") == "ask" else
                    "遮挡或图像证据不足" if key not in fields else "提取值为 null"
                ),
            ))
        else:
            items.append(ObservationItem(
                field_path=key, status="observed", value=value,
                unit=declaration.get("unit"),
                confidence=confidence,
                note=str(evidence or ""),
            ))
    if data.get("raw_description"):
        items.append(ObservationItem(
            field_path="raw_description", status="inferred",
            value=str(data["raw_description"]), confidence="low",
        ))
    sha = str(data.get("sha256") or "")
    refs = list(source_refs or [])
    if sha:
        refs.append(f"extraction:{sha}")
    return Observation(
        observation_id=f"obs-{sha[:12]}" if sha else f"obs-{data.get('schema_name') or 'generic'}",
        source="vision_extraction", source_refs=refs, items=items,
    )


def execution_plan_from_gdl_object_plan(plan, *, plan_id: str, spec_ref: str) -> ParseResult:
    """旧 GDLObjectPlan（planner 策略内容）→ ExecutionPlan 候选。

    脚本策略 → modify_script 步骤；validation_checks → requirements——
    无法映射注册执行器的保持 check_id=None（执行期显式 unknown，不冒充可查）。
    """
    steps: list[PlanStep] = []
    for idx, text in enumerate(getattr(plan, "script_3d_strategy", []) or []):
        steps.append(PlanStep(
            step_id=f"step-3d-{idx + 1}", kind="modify_script",
            target="scripts/3d.gdl", detail=str(text),
        ))
    for idx, text in enumerate(getattr(plan, "script_2d_strategy", []) or []):
        steps.append(PlanStep(
            step_id=f"step-2d-{idx + 1}", kind="modify_script",
            target="scripts/2d.gdl", detail=str(text),
        ))
    for idx, text in enumerate(getattr(plan, "parameters", []) or []):
        steps.append(PlanStep(
            step_id=f"step-param-{idx + 1}", kind="set_param",
            target=str(text), detail="来自 planner 参数清单",
        ))
    requirements = [
        Requirement(
            requirement_id=f"req-plan-{idx + 1}", text=str(text), kind="check",
            check_id=None, status="unknown",
        )
        for idx, text in enumerate(getattr(plan, "validation_checks", []) or [])
    ]
    candidate = ExecutionPlan(
        plan_id=plan_id, spec_ref=spec_ref,
        observation_ref="", steps=steps, requirements=requirements,
    )
    candidate.plan_hash = execution_plan_hash_dict(candidate.to_dict(with_hash=False))
    return parse_execution_plan(candidate.to_dict())


_SYNTHETIC_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "object_contracts" / "minimal_shelf.json"
)


def load_synthetic_minimal_contract() -> ParseResult:
    """加载 synthetic 最小对象合同（openbrep/data/object_contracts/minimal_shelf.json）。

    只读规划报告与合同测试共用同一份数据（"同一规格"，不得手工硬编码）；
    解析/校验走同一套 parse_object_spec / parse_execution_plan。文件里的
    plan_hash 是内容自洽的（改动内容即 HASH_MISMATCH，被合同测试抓住）。
    """
    data = json.loads(_SYNTHETIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    spec_result = parse_object_spec(data.get("object_spec") or {})
    plan_result = parse_execution_plan(data.get("execution_plan") or {})
    errors = spec_result.errors + plan_result.errors
    if errors:
        return ParseResult(errors=errors)
    obs_result = parse_observation(data.get("observation") or {})
    return ParseResult(value={
        "object_spec": spec_result.value,
        "execution_plan": plan_result.value,
        "observation": obs_result.value,
    }, errors=obs_result.errors)
