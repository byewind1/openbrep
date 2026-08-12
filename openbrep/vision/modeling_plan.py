"""
modeling_plan — ModelingPlan（Vision Harness S4 合成产物，设计 §6 数据契约）

VisualStructure 的演进而非替换：generic schema 输出 = 现字段超集
（VisualStructure 包装在 fields["visual_structure"]，to_hint() 转调
visual_structure_to_gdl_hint，保证 generic 路径 hint 与现状逐字节一致）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openbrep.vision.image_to_plan import visual_structure_to_gdl_hint
from openbrep.vision.schema import VisualStructure


@dataclass
class ModelingPlan:
    """一张参考图的定向提取结果（每图一个 plan；多图按序合并进 enriched_instruction）。

    schema_name:  "generic" | "lattice_window" | "furniture_stack" | ...
    fields:       schema 定义的字段值（generic 时 = {"visual_structure": VisualStructure}）
    confidence:   字段级置信度。键与 critic_checks 同风格——顶层字段用字段名，
                  嵌套字段用点路径（如 "grid_topology.rows"）。取值：
                  "high"（提取置信 / critic 已核）/ "low"（无法判断或核对不确定）/
                  "unknown"（critic 校验不可用降级，设计 D8）。
    corrections:  critic 修正记录（P5c 启用）：[{"field", "old", "new", "evidence"}]，
                  field 为点路径。critic 无权动 critic_checks 之外的字段（D3）。
    source_images: 参与合成的 ImageRef.sha256 列表
    raw_description: 降级 fallback / 补充说明文本
    degraded:     S2 提取失败已降级为 raw_description（设计 D8，hint 里必须可见）
    critic_degraded: S3 critic 校验不可用（调用失败/输出不可解析）→ 该图字段全标
                  confidence=unknown，不阻塞流程，hint 里可见（设计 D8）
    """

    schema_name: str
    fields: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    corrections: list = field(default_factory=list)
    source_images: list = field(default_factory=list)
    raw_description: str = ""
    degraded: bool = False
    critic_degraded: bool = False

    # ── hint 标记格式（P5c，测试钉死）──────────────────────────
    # low 置信顶层字段        → key: value（低置信）
    # low 置信嵌套路径        → key: {...}（低置信：grid_topology.rows）
    # critic 修正（顶层字段）  → key: new_value（critic 修正：4→3）
    # critic 修正（嵌套路径）  → key: {...}（critic 修正：grid_topology.rows 4→3）
    # critic 校验不可用        → 单独一行 【critic 校验已降级】

    def to_hint(self) -> str:
        """渲染注入 enriched_instruction 的结构化提示文本。

        generic → 转调现有 visual_structure_to_gdl_hint（逐字节一致）；
        其余 schema → 按 fields 顺序渲染 key: value，附置信度/修正标记；
        降级时带显式标记。
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

        if self.critic_degraded:
            lines.append("【critic 校验已降级】")

        for key, value in self.fields.items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False)
            else:
                rendered = "null" if value is None else str(value)
            markers = self._hint_markers(key)
            line = f"{key}: {rendered}"
            if markers:
                line += "".join(markers)
            lines.append(line)
        if self.raw_description:
            lines.append(f"\n补充说明：{self.raw_description}")
        return "\n".join(lines)

    def _hint_markers(self, key: str) -> list[str]:
        """字段行的置信度/修正标记（顺序固定：修正在前，低置信在后）。"""
        markers: list[str] = []
        prefix = key + "."
        for corr in self.corrections:
            field_path = str(corr.get("field") or "")
            if field_path == key:
                markers.append(f"（critic 修正：{corr.get('old')}→{corr.get('new')}）")
            elif field_path.startswith(prefix):
                markers.append(f"（critic 修正：{field_path} {corr.get('old')}→{corr.get('new')}）")
        if self.confidence.get(key) == "low":
            markers.append("（低置信）")
        else:
            for path in sorted(
                p for p in self.confidence if p.startswith(prefix) and self.confidence[p] == "low"
            ):
                markers.append(f"（低置信：{path}）")
        return markers
