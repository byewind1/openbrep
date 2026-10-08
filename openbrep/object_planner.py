"""Professional GDL object planning before code generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class GDLObjectPlan:
    """Structured design intent for a new GDL object."""

    object_type: str
    geometry: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    parameter_groups: list[str] = field(default_factory=list)
    derived_parameters: list[str] = field(default_factory=list)
    geometry_parts: list[str] = field(default_factory=list)
    command_candidates: list[str] = field(default_factory=list)
    script_3d_strategy: list[str] = field(default_factory=list)
    script_2d_strategy: list[str] = field(default_factory=list)
    parameter_script_strategy: list[str] = field(default_factory=list)
    ui_script_strategy: list[str] = field(default_factory=list)
    material_strategy: list[str] = field(default_factory=list)
    hotspots_and_editability: list[str] = field(default_factory=list)
    validation_checks: list[str] = field(default_factory=list)
    knowledge_sources: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    # U05-B: machine-validated candidate contract and traceable coverage.
    parts: list[dict[str, Any]] = field(default_factory=list)
    typed_parameters: list[dict[str, Any]] = field(default_factory=list)
    requirement_mappings: list[dict[str, Any]] = field(default_factory=list)
    # U02-A：planner 失败/解析失败回落最小规划时必须显式降级——
    # 报告据此追加 degraded 检查行，不冒充正常规划。
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type,
            "geometry": list(self.geometry),
            "parameters": list(self.parameters),
            "assumptions": list(self.assumptions),
            "parameter_groups": list(self.parameter_groups),
            "derived_parameters": list(self.derived_parameters),
            "geometry_parts": list(self.geometry_parts),
            "command_candidates": list(self.command_candidates),
            "script_3d_strategy": list(self.script_3d_strategy),
            "script_2d_strategy": list(self.script_2d_strategy),
            "parameter_script_strategy": list(self.parameter_script_strategy),
            "ui_script_strategy": list(self.ui_script_strategy),
            "material_strategy": list(self.material_strategy),
            "hotspots_and_editability": list(self.hotspots_and_editability),
            "validation_checks": list(self.validation_checks),
            "knowledge_sources": list(self.knowledge_sources),
            "risks": list(self.risks),
            "parts": [dict(item) for item in self.parts],
            "typed_parameters": [dict(item) for item in self.typed_parameters],
            "requirement_mappings": [dict(item) for item in self.requirement_mappings],
            "degraded": self.degraded,
        }

    def to_prompt(self) -> str:
        lines = [
            "## GDL Object Plan",
            "",
            "以下规划是生成代码前的专业建模约束，生成脚本和参数表时必须遵守。",
            f"- Object type: {self.object_type}",
        ]
        _append_section(lines, "Assumptions", self.assumptions)
        _append_section(lines, "Geometry", self.geometry)
        _append_section(lines, "Geometry parts", self.geometry_parts)
        _append_section(lines, "Parameters", self.parameters)
        _append_section(lines, "Parameter groups", self.parameter_groups)
        _append_section(lines, "Derived parameters", self.derived_parameters)
        _append_section(lines, "Command candidates", self.command_candidates)
        _append_section(lines, "3D script strategy", self.script_3d_strategy)
        _append_section(lines, "2D script strategy", self.script_2d_strategy)
        _append_section(lines, "Parameter script strategy", self.parameter_script_strategy)
        _append_section(lines, "UI script strategy", self.ui_script_strategy)
        _append_section(lines, "Materials and attributes", self.material_strategy)
        _append_section(lines, "Hotspots and editability", self.hotspots_and_editability)
        _append_section(lines, "Validation checks", self.validation_checks)
        _append_section(lines, "Knowledge sources", self.knowledge_sources)
        _append_section(lines, "Risks to avoid", self.risks)
        if self.parts:
            lines.extend(["", "## Traceable Plan Contract"])
            lines.append("- Parts: " + "; ".join(
                f"{item.get('part_id')}: {item.get('description', '')}" for item in self.parts
            ))
            lines.append("- Typed parameters:")
            for item in self.typed_parameters:
                unit = f" {item.get('unit')}" if item.get("unit") else ""
                default = f" = {item.get('default_value')}{unit}" if item.get("default_value") is not None else ""
                lines.append(
                    f"  - {item.get('param_id')} → {item.get('gdl_name')} "
                    f"({item.get('type')}){default}; {item.get('description', '')}"
                )
            lines.append("- Requirement mappings:")
            for item in self.requirement_mappings:
                lines.append(
                    f"  - {item.get('requirement_id')}: {item.get('text', '')}; "
                    f"parts={item.get('part_refs', [])}; parameters={item.get('parameter_refs', [])}; "
                    f"scripts={item.get('script_refs', [])}; check={item.get('check_id')}; "
                    f"scenarios={item.get('scenario_refs', [])}; sources={item.get('source_refs', [])}"
                )
        return "\n".join(lines)

    def to_user_summary(self, planning_artifact: dict[str, Any] | None = None) -> str:
        parts = [
            "### 生成前规划",
            f"- 对象类型：{self.object_type}",
        ]
        if self.degraded:
            parts.append("- 规划状态：降级候选；部分结构化依据缺失，不能视为完整 typed Plan。")
        elif planning_artifact and planning_artifact.get("status") == "needs_input":
            parts.append("- 规划状态：需要补充确认；存在冲突或输入歧义。")
        elif planning_artifact and planning_artifact.get("status") == "degraded":
            parts.append("- 规划状态：降级候选；部分要求尚未映射或验证。")
        elif planning_artifact and planning_artifact.get("status") == "ready":
            parts.append("- 规划状态：结构化计划已通过合同校验；这不代表生成结果已验证。")
        if self.geometry:
            parts.append(f"- 几何组成：{'；'.join(self.geometry[:4])}")
        if self.parameters:
            parts.append(f"- 参数重点：{'；'.join(self.parameters[:5])}")
        if self.command_candidates:
            parts.append(f"- 命令选择：{'；'.join(self.command_candidates[:5])}")
        if self.script_3d_strategy:
            parts.append(f"- 3D 策略：{'；'.join(self.script_3d_strategy[:3])}")
        if self.script_2d_strategy:
            parts.append(f"- 2D 策略：{'；'.join(self.script_2d_strategy[:2])}")
        if self.knowledge_sources:
            parts.append(f"- 本次使用知识：{'；'.join(self.knowledge_sources[:8])}")
        if planning_artifact:
            spec = planning_artifact.get("candidate_spec") or {}
            params = spec.get("params") if isinstance(spec, dict) else []
            if params:
                parts.append("- 候选参数：" + "；".join(
                    f"{item.get('gdl_name')} ({item.get('type')}{', ' + str(item.get('unit')) if item.get('unit') else ''})"
                    for item in params if isinstance(item, dict)
                ))
            execution = planning_artifact.get("execution_plan") or {}
            mappings = execution.get("requirement_mappings") if isinstance(execution, dict) else []
            if mappings:
                parts.append("- 要求覆盖：" + "；".join(
                    f"{item.get('requirement_id')} → 部件 {item.get('part_refs') or '无'} / 参数 {item.get('parameter_refs') or '无'} / 脚本 {item.get('script_refs') or '无'} / 场景 {item.get('scenario_refs') or '无'}"
                    for item in mappings if isinstance(item, dict)
                ))
            issues = planning_artifact.get("issues") or []
            if issues:
                parts.append("- 规划缺口：" + "；".join(
                    f"{item.get('field_path') or 'plan'}：{item.get('message') or item.get('code') or '未说明'}"
                    for item in issues if isinstance(item, dict)
                ))
            if isinstance(execution, dict) and execution.get("plan_hash"):
                parts.append(f"- Plan hash：{execution['plan_hash']}")
        return "\n".join(parts)


def plan_gdl_object(
    llm,
    *,
    instruction: str,
    knowledge: str = "",
    skills: str = "",
    planner_knowledge_max_chars: int | None = None,
    planner_skills_max_chars: int | None = 3000,
    llm_kwargs: dict | None = None,
) -> GDLObjectPlan:
    """
    Ask the LLM for a compact GDL object plan, with deterministic fallback.

    The fallback keeps offline tests and no-key local runs working while still
    giving the generation step a professional minimum contract.

    ``llm_kwargs``（D4）：附加在 llm.generate() 上的专用 kwargs（如 Codex 的
    codex_intent/codex_should_cancel/codex_on_event）；None = 现有调用形态
    逐字节不变（非 codex 路径不受影响）。
    """
    fallback = infer_minimum_plan(instruction)
    try:
        messages = [
            {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_planner_user_prompt(
                    instruction,
                    knowledge,
                    skills,
                    knowledge_max_chars=planner_knowledge_max_chars,
                    skills_max_chars=planner_skills_max_chars,
                ),
            },
        ]
        raw = llm.generate(messages, **(llm_kwargs or {}))
        content = raw.content if hasattr(raw, "content") else str(raw)
        plan = parse_gdl_object_plan(content, fallback=fallback)
        if plan is fallback:
            # U02-A：LLM 输出不可解析、回落最小规划 = 规划降级（显式可见）
            plan = replace(plan, degraded=True)
        return plan
    except Exception:
        # U02-A：planner 调用失败 = 规划降级（显式可见，不冒充正常规划）
        return replace(fallback, degraded=True)


def parse_gdl_object_plan(text: str, *, fallback: GDLObjectPlan | None = None) -> GDLObjectPlan:
    """Parse planner JSON; degrade to fallback for malformed responses."""
    fallback = fallback or infer_minimum_plan("")
    payload = _extract_json_object(text)
    if not payload:
        return fallback
    try:
        data = json.loads(payload)
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback

    object_type = _as_text(data.get("object_type")) or fallback.object_type
    return GDLObjectPlan(
        object_type=object_type,
        geometry=_plan_list(data, "geometry", fallback.geometry),
        parameters=_plan_list(data, "parameters", fallback.parameters),
        assumptions=_plan_list(data, "assumptions", fallback.assumptions),
        parameter_groups=_plan_list(data, "parameter_groups", fallback.parameter_groups),
        derived_parameters=_plan_list(data, "derived_parameters", fallback.derived_parameters),
        geometry_parts=_plan_list(data, "geometry_parts", fallback.geometry_parts),
        command_candidates=_plan_list(data, "command_candidates", fallback.command_candidates),
        script_3d_strategy=_plan_list(data, "script_3d_strategy", fallback.script_3d_strategy),
        script_2d_strategy=_plan_list(data, "script_2d_strategy", fallback.script_2d_strategy),
        parameter_script_strategy=_plan_list(data, "parameter_script_strategy", fallback.parameter_script_strategy),
        ui_script_strategy=_plan_list(data, "ui_script_strategy", fallback.ui_script_strategy),
        material_strategy=_plan_list(data, "material_strategy", fallback.material_strategy),
        hotspots_and_editability=_plan_list(data, "hotspots_and_editability", fallback.hotspots_and_editability),
        validation_checks=_plan_list(data, "validation_checks", fallback.validation_checks),
        knowledge_sources=_plan_list(data, "knowledge_sources", fallback.knowledge_sources),
        risks=_plan_list(data, "risks", fallback.risks),
        parts=_as_object_list(data.get("parts")),
        typed_parameters=_as_object_list(data.get("typed_parameters")),
        requirement_mappings=_as_object_list(data.get("requirement_mappings")),
    )


def infer_minimum_plan(instruction: str) -> GDLObjectPlan:
    """Build a domain-neutral plan without inventing object construction."""
    return GDLObjectPlan(
        object_type="GDL 构件（类型未确定）",
        geometry=[],
        parameters=[
            "Length A = 总宽度",
            "Length B = 总深度",
            "Length ZZYZX = 总高度",
        ],
        assumptions=[],
        validation_checks=["执行适用的 GDL 静态检查和编译检查"],
        risks=["构造、部件和材质未指定；遵循作者明确要求，缺失信息保持未确定。"],
    )


def _build_planner_user_prompt(
    instruction: str,
    knowledge: str,
    skills: str,
    *,
    knowledge_max_chars: int | None = None,
    skills_max_chars: int | None = 3000,
) -> str:
    parts = [
        f"用户目标：{instruction}",
        "",
        "规划必须服从用户明确描述与保持项。不得根据对象名称补入未说明的构造、部件、门板、材质或数量；对象类型只用于理解，不等于设计授权。",
        "缺少的信息保持未确定或标记为 assumption；只在用户目标需要时提出澄清。可用的几何、参数、脚本策略、材质和校验字段可以显式留空。",
        "另请输出 typed_parameters、parts 与 requirement_mappings。参数使用规范单位并关联稳定 param_id；每条用户/图像要求要映射到实际 part_refs、parameter_refs、script_refs、check_id（只用已提供的框架检查 ID；不确定则 null）、scenario_refs 与 source_refs。",
        "把图像推断与用户明确尺寸分开：用户明确值优先；未观察到的内部结构必须标为 assumption 或要求澄清，不能写成观察事实。",
    ]
    from openbrep.contracts.bindings import BUILTIN_CHECK_EXECUTORS

    parts.append(
        "当前可引用的框架 check_id："
        + ", ".join(sorted(BUILTIN_CHECK_EXECUTORS))
        + "。不得创造列表外 check_id；没有适用执行器时 check_id 必须为 null。"
    )
    if knowledge:
        parts.append("可参考知识片段：\n" + _limit_text(knowledge, knowledge_max_chars))
    if skills:
        parts.append("可参考 skill 片段：\n" + _limit_text(skills, skills_max_chars))
    return "\n\n".join(parts)


def _limit_text(text: str, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0:
        return text
    return text[:max_chars]


def _extract_json_object(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return ""


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _plan_list(data: dict[str, Any], key: str, fallback: list[str]) -> list[str]:
    """Missing means compatibility fallback; explicit [] means author/model chose none."""
    return _as_list(data[key]) if key in data else list(fallback)


def _as_object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _append_section(lines: list[str], title: str, values: list[str]) -> None:
    if not values:
        return
    lines.append(f"- {title}:")
    for value in values:
        lines.append(f"  - {value}")


# 命令选择阶梯摘要（注入 planner system prompt；全量规则见
# knowledge/core/gdl_command_selection.md，经 planner_context 进入 user 消息）。
# 摘要给 LLM 一个强约束：能低不高，升级必须给出正当理由。
_COMMAND_LADDER_SUMMARY = """\
## GDL Command Selection Ladder (pick the LOWEST justifiable level)
1 BLOCK/BRICK/CPRISM_ — box/panel: default, zero justification needed.
2 CYLIND/CONE/SPHERE/ELLIPS — analytic cylinders/cones/spheres.
3 PRISM_/BPRISM_ — arbitrary 2D profile extruded along Z (chamfered/odd panels).
4 EXTRUDE — profile extrusion with a tilted/translated top face.
5 REVOLVE — profile rotated around an axis: only for real bodies of revolution.
6 SWEEP — profile along a bent path: only when the path is truly curved.
7 TUBE — constant cross-section along a 3D path: hardest, last resort.
8 RULED/COONS — skin between two profiles / free-form surfaces.
9 MESH/PGON — explicit mesh patches: last resort, import-only.
Subtraction: prefer CUTPLANE/CUTPOLY/CUTFORM/GROUP booleans for chamfers and
cutouts instead of building many small bodies and assembling them.
Rule: stating the justification for going up a level is mandatory in the plan.
Never default to BODY/EDGE-level construction or GROUP boolean as the main strategy.
"""


_PLANNER_SYSTEM_PROMPT = """\
You are an expert Archicad GDL object architect.

Before code generation, produce a professional GDL object plan. Do not ask the
user for unnecessary detail. Infer sensible defaults from the object type and
GDL conventions.

Return only compact JSON with this schema:
{
  "object_type": "...",
  "assumptions": ["..."],
  "geometry": ["..."],
  "geometry_parts": ["..."],
  "parameters": ["..."],
  "parameter_groups": ["..."],
  "derived_parameters": ["..."],
  "command_candidates": ["..."],
  "script_3d_strategy": ["..."],
  "script_2d_strategy": ["..."],
  "parameter_script_strategy": ["..."],
  "ui_script_strategy": ["..."],
  "material_strategy": ["..."],
  "hotspots_and_editability": ["..."],
  "validation_checks": ["..."],
  "parts": [{"part_id": "stable_snake_case_id", "description": "..."}],
  "typed_parameters": [{"param_id": "p.stable_id", "gdl_name": "A", "type": "Length", "unit": "m", "description": "...", "default_value": 1.2, "required": true}],
  "requirement_mappings": [{"requirement_id": "stable_req_id", "text": "...", "kind": "check|constraint|assumption", "part_refs": ["part_id"], "parameter_refs": ["p.stable_id"], "script_refs": ["scripts/3d.gdl"], "check_id": "registered_check_or_null", "scenario_refs": ["scenario-id"], "source_refs": ["user:... or observation:..."]}],
  "knowledge_sources": ["..."],
  "risks": ["..."]
}

""" + _COMMAND_LADDER_SUMMARY + "\n\n"
