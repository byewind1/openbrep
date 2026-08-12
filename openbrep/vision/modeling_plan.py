"""
modeling_plan — ModelingPlan（Vision Harness S4 合成产物，设计 §6 数据契约）

VisualStructure 的演进而非替换：generic schema 输出 = 现字段超集
（VisualStructure 包装在 fields["visual_structure"]，to_hint() 转调
visual_structure_to_gdl_hint，保证 generic 路径 hint 与现状逐字节一致）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openbrep.vision.image_to_plan import visual_structure_to_gdl_hint
from openbrep.vision.schema import VisualStructure

if TYPE_CHECKING:
    pass


@dataclass
class ModelingPlan:
    """一张参考图的定向提取结果（每图一个 plan；多图按序合并进 enriched_instruction）。

    schema_name:  "generic" | "lattice_window" | "furniture_stack" | ...
    fields:       schema 定义的字段值（generic 时 = {"visual_structure": VisualStructure}）
    confidence:   字段级置信度（P5b 全部填 "unknown"；P5c 再做 high/low）
    corrections:  critic 修正记录（P5c 启用；本单恒空）
    source_images: 参与合成的 ImageRef.sha256 列表
    raw_description: 降级 fallback / 补充说明文本
    degraded:     S2 提取失败已降级为 raw_description（设计 D8，hint 里必须可见）
    """

    schema_name: str
    fields: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    corrections: list = field(default_factory=list)
    source_images: list = field(default_factory=list)
    raw_description: str = ""
    degraded: bool = False

    def to_hint(self) -> str:
        """渲染注入 enriched_instruction 的结构化提示文本。

        generic → 转调现有 visual_structure_to_gdl_hint（逐字节一致）；
        其余 schema → 按 fields 顺序渲染 key: value；降级时带显式标记。
        """
        if self.schema_name == "generic":
            vs = self.fields.get("visual_structure")
            if isinstance(vs, VisualStructure):
                return visual_structure_to_gdl_hint(vs)
            fallback = self.raw_description or json.dumps(self.fields, ensure_ascii=False)
            return f"## 参考图建模计划\n{fallback}"

        lines = [f"## 参考图建模计划（{self.schema_name}）"]
        if self.degraded or (not self.fields and self.raw_description):
            lines.append("【分析失败已降级】")
            if self.raw_description:
                lines.append(f"原始分析文本：{self.raw_description}")
            return "\n".join(lines)

        for key, value in self.fields.items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False)
            else:
                rendered = "null" if value is None else str(value)
            lines.append(f"{key}: {rendered}")
        if self.raw_description:
            lines.append(f"\n补充说明：{self.raw_description}")
        return "\n".join(lines)
