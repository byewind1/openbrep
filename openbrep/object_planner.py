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
    script_3d_strategy: list[str] = field(default_factory=list)
    script_2d_strategy: list[str] = field(default_factory=list)
    material_strategy: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        lines = [
            "## GDL Object Plan",
            "",
            "以下规划是生成代码前的专业建模约束，生成脚本和参数表时必须遵守。",
            f"- Object type: {self.object_type}",
        ]
        _append_section(lines, "Geometry", self.geometry)
        _append_section(lines, "Parameters", self.parameters)
        _append_section(lines, "3D script strategy", self.script_3d_strategy)
        _append_section(lines, "2D script strategy", self.script_2d_strategy)
        _append_section(lines, "Materials and attributes", self.material_strategy)
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
        if self.script_3d_strategy:
            parts.append(f"- 3D 策略：{'；'.join(self.script_3d_strategy[:3])}")
        if self.script_2d_strategy:
            parts.append(f"- 2D 策略：{'；'.join(self.script_2d_strategy[:2])}")
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
        script_3d_strategy=_as_list(data.get("script_3d_strategy")) or fallback.script_3d_strategy,
        script_2d_strategy=_as_list(data.get("script_2d_strategy")) or fallback.script_2d_strategy,
        material_strategy=_as_list(data.get("material_strategy")) or fallback.material_strategy,
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
            script_3d_strategy=[
                "使用 BLOCK 组合板件，保持 ADD/DEL 平衡",
                "中间层板使用 FOR/NEXT 循环生成",
                "用 MAX 或前置计算避免内宽、间距为负",
            ],
            script_2d_strategy=[
                "至少输出 HOTSPOT2 边界点和 PROJECT2 3, 270, 2",
                "用 A/B 作为平面外包络，保证可选中和可缩放",
            ],
            material_strategy=[
                "框架和层板材料分参数控制",
                "背板沿用框架材料或单独材质参数",
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
  "geometry": ["..."],
  "parameters": ["..."],
  "script_3d_strategy": ["..."],
  "script_2d_strategy": ["..."],
  "material_strategy": ["..."],
  "risks": ["..."]
}
"""
