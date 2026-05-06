"""Professional GDL object planning before code generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
        return "\n".join(lines)

    def to_user_summary(self) -> str:
        parts = [
            "### 生成前规划",
            f"- 对象类型：{self.object_type}",
        ]
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
        return "\n".join(parts)


def plan_gdl_object(
    llm,
    *,
    instruction: str,
    knowledge: str = "",
    skills: str = "",
) -> GDLObjectPlan:
    """
    Ask the LLM for a compact GDL object plan, with deterministic fallback.

    The fallback keeps offline tests and no-key local runs working while still
    giving the generation step a professional minimum contract.
    """
    fallback = infer_minimum_plan(instruction)
    try:
        messages = [
            {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": _build_planner_user_prompt(instruction, knowledge, skills)},
        ]
        raw = llm.generate(messages)
        content = raw.content if hasattr(raw, "content") else str(raw)
        return parse_gdl_object_plan(content, fallback=fallback)
    except Exception:
        return fallback


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
        geometry=_as_list(data.get("geometry")) or fallback.geometry,
        parameters=_as_list(data.get("parameters")) or fallback.parameters,
        assumptions=_as_list(data.get("assumptions")) or fallback.assumptions,
        parameter_groups=_as_list(data.get("parameter_groups")) or fallback.parameter_groups,
        derived_parameters=_as_list(data.get("derived_parameters")) or fallback.derived_parameters,
        geometry_parts=_as_list(data.get("geometry_parts")) or fallback.geometry_parts,
        command_candidates=_as_list(data.get("command_candidates")) or fallback.command_candidates,
        script_3d_strategy=_as_list(data.get("script_3d_strategy")) or fallback.script_3d_strategy,
        script_2d_strategy=_as_list(data.get("script_2d_strategy")) or fallback.script_2d_strategy,
        parameter_script_strategy=_as_list(data.get("parameter_script_strategy")) or fallback.parameter_script_strategy,
        ui_script_strategy=_as_list(data.get("ui_script_strategy")) or fallback.ui_script_strategy,
        material_strategy=_as_list(data.get("material_strategy")) or fallback.material_strategy,
        hotspots_and_editability=_as_list(data.get("hotspots_and_editability")) or fallback.hotspots_and_editability,
        validation_checks=_as_list(data.get("validation_checks")) or fallback.validation_checks,
        knowledge_sources=_as_list(data.get("knowledge_sources")) or fallback.knowledge_sources,
        risks=_as_list(data.get("risks")) or fallback.risks,
    )


def infer_minimum_plan(instruction: str) -> GDLObjectPlan:
    """Build a conservative plan without LLM access."""
    text = (instruction or "").lower()
    if any(word in text for word in ("书架", "bookshelf", "shelf")):
        return GDLObjectPlan(
            object_type="参数化书架 / bookshelf",
            geometry=[
                "左、右侧板作为主结构竖向构件",
                "顶板、底板和中间层板使用参数化厚度",
                "可选背板使用独立开关和厚度参数",
                "层板数量由参数控制，避免写死单一几何体",
            ],
            parameters=[
                "Length A = 总宽度",
                "Length B = 总深度",
                "Length ZZYZX = 总高度",
                "Length frame_thk = 侧板厚度",
                "Integer shelf_count = 层板总数",
                "Boolean has_back_panel = 是否带背板",
            ],
            assumptions=[
                "默认采用板式家具结构",
                "默认至少包含顶板、底板和两块侧板",
            ],
            parameter_groups=[
                "尺寸参数：A, B, ZZYZX, frame_thk, shelf_thickness",
                "构造参数：shelf_count, has_back_panel, back_thk",
                "材质参数：mat_frame, mat_shelf",
            ],
            derived_parameters=[
                "_inner_w = A - 2 * frame_thk",
                "_shelf_gap 根据 ZZYZX、shelf_thickness 和 shelf_count 计算",
            ],
            geometry_parts=[
                "left_side_panel",
                "right_side_panel",
                "bottom_panel",
                "top_panel",
                "middle_shelves",
                "optional_back_panel",
            ],
            command_candidates=[
                "BLOCK",
                "ADDX/ADDY/ADDZ",
                "DEL",
                "FOR/NEXT",
                "MATERIAL",
                "PROJECT2",
                "HOTSPOT2",
            ],
            script_3d_strategy=[
                "使用 BLOCK 组合板件，保持 ADD/DEL 平衡",
                "中间层板使用 FOR/NEXT 循环生成",
                "用 MAX 或前置计算避免内宽、间距为负",
            ],
            script_2d_strategy=[
                "至少输出 HOTSPOT2 边界点和 PROJECT2 3, 270, 2",
                "用 A/B 作为平面外包络，保证可选中和可缩放",
            ],
            parameter_script_strategy=[
                "对 shelf_count、frame_thk、shelf_thickness 做最小值保护",
                "派生参数不写入参数表，优先在脚本内计算",
            ],
            material_strategy=[
                "框架和层板材料分参数控制",
                "背板沿用框架材料或单独材质参数",
            ],
            hotspots_and_editability=[
                "2D 脚本设置四角 HOTSPOT2",
                "3D 几何保持在 A/B/ZZYZX 外包络内",
            ],
            validation_checks=[
                "检查 ADD/DEL 是否平衡",
                "检查 FOR/NEXT 是否配对",
                "检查 _inner_w 和 _shelf_gap 是否为正",
            ],
            knowledge_sources=[
                "archetype.bookshelf",
                "wiki.BLOCK",
                "wiki.ADD_DEL",
                "wiki.FOR_NEXT",
            ],
            risks=[
                "FOR 循环必须有 NEXT",
                "每个 ADDX/ADDY/ADDZ 必须有对应 DEL",
                "参数名必须与 paramlist.xml 完全一致",
            ],
        )

    return GDLObjectPlan(
        object_type="参数化 GDL 构件",
        geometry=[
            "用 A、B、ZZYZX 定义总体边界",
            "将对象拆成可维护的基本构件，而不是单一固定几何体",
        ],
        parameters=[
            "Length A = 总宽度",
            "Length B = 总深度",
            "Length ZZYZX = 总高度",
            "Material mat_main = 主材质",
        ],
        assumptions=[
            "默认生成可参数化对象，不生成一次性固定尺寸几何",
        ],
        command_candidates=[
            "BLOCK",
            "PRISM_",
            "MATERIAL",
            "PROJECT2",
            "HOTSPOT2",
        ],
        script_3d_strategy=[
            "优先使用清晰的 GDL 基础命令组合几何",
            "保持变换栈 ADD/DEL 平衡",
            "为后续修改保留语义化参数名",
        ],
        script_2d_strategy=[
            "输出 HOTSPOT2 外包络点",
            "包含 PROJECT2 3, 270, 2 保证平面可见",
        ],
        material_strategy=[
            "主要可见构件使用材质参数，不写死材质",
        ],
        validation_checks=[
            "检查 2D 脚本是否可见",
            "检查参数表和脚本参数名是否一致",
            "检查 3D 脚本是否 END 结束",
        ],
        risks=[
            "不要省略 2D 脚本",
            "不要使用无效参数类型",
            "3D 脚本必须以 END 结束",
        ],
    )


def _build_planner_user_prompt(instruction: str, knowledge: str, skills: str) -> str:
    parts = [
        f"用户目标：{instruction}",
        "",
        "请先自主规划一个能用于工程继续修改的 GDL 物件，不要要求用户提供过细参数。",
        "规划必须覆盖：构件假设、参数组、派生参数、几何拆解、GDL 命令选择、2D/3D 策略、材质、热点可编辑性、校验项和风险。",
    ]
    if knowledge:
        parts.append("可参考知识片段：\n" + knowledge[:6000])
    if skills:
        parts.append("可参考 skill 片段：\n" + skills[:3000])
    return "\n\n".join(parts)


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


def _append_section(lines: list[str], title: str, values: list[str]) -> None:
    if not values:
        return
    lines.append(f"- {title}:")
    for value in values:
        lines.append(f"  - {value}")


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
  "knowledge_sources": ["..."],
  "risks": ["..."]
}
"""
