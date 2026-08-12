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
from openbrep.vision.schema import VisualLayer, VisualStructure


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
    # P5d-2：schema 元数据随提取透出（前端可编辑卡片据此决定哪些字段可改）。
    # required + critic_checks 是 D4 规定的可编辑范围；generic 两者皆空 → 只读确认。
    required: list = field(default_factory=list)
    critic_checks: list = field(default_factory=list)

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


    @classmethod
    def from_dict(cls, data: dict) -> "ModelingPlan":
        """从 plan_to_dict 输出（确认重发 payload）重建 ModelingPlan。

        P5d-2 确认门：用户在前端编辑 fields 后，整份 extraction dict 随原
        body 重发，pipeline 用本方法重建 plans（跳过 harness，零 vision 重调）。
        generic 的 fields 内嵌 VisualStructure dict → 还原为实例，保证
        to_hint() 走原 visual_structure_to_gdl_hint 渲染（未编辑时逐字节一致）。
        """
        schema_name = str(data.get("schema_name") or "")
        fields: dict = data.get("fields") or {}
        if schema_name == "generic" and isinstance(fields, dict):
            vs_data = fields.get("visual_structure")
            if isinstance(vs_data, dict):
                fields = {"visual_structure": _visual_structure_from_dict(vs_data)}
        return cls(
            schema_name=schema_name,
            fields=fields,
            confidence=dict(data.get("confidence") or {}),
            corrections=list(data.get("corrections") or []),
            source_images=[str(data.get("sha256") or "")] if data.get("sha256") else [],
            raw_description=str(data.get("raw_description") or ""),
            degraded=bool(data.get("degraded")),
            critic_degraded=bool(data.get("critic_degraded")),
            required=list(data.get("required") or []),
            critic_checks=list(data.get("critic_checks") or []),
        )


def _visual_structure_from_dict(data: dict) -> VisualStructure:
    """plan_to_dict 序列化后的 VisualStructure dict → VisualStructure 实例。"""
    layers = []
    for raw in data.get("layers") or []:
        if not isinstance(raw, dict):
            continue
        layers.append(
            VisualLayer(
                name=str(raw.get("name") or ""),
                command=str(raw.get("command") or ""),
                description=str(raw.get("description") or ""),
                parametric=bool(raw.get("parametric", True)),
            )
        )
    return VisualStructure(
        component_type=str(data.get("component_type") or ""),
        main_form=str(data.get("main_form") or ""),
        layers=layers,
        symmetry=list(data.get("symmetry") or []),
        key_features=list(data.get("key_features") or []),
        dimension_hints=dict(data.get("dimension_hints") or {}),
        parametrize=list(data.get("parametrize") or []),
        fix_as_ratio=list(data.get("fix_as_ratio") or []),
        raw_description=str(data.get("raw_description") or ""),
    )
