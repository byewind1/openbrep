"""
VisualStructure — 参考图结构化表示

把视觉信息转化为 GDL 建模可用的结构化描述。
不存储像素，存储"建模意图"。
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class VisualLayer:
    """构件的一个几何层次。"""
    name: str           # 层名，如 "base", "waist", "slot_body", "lug"
    command: str        # 推荐 GDL 命令，如 "PRISM_", "BLOCK", "CYLIND"
    description: str    # 这一层在做什么
    parametric: bool = True  # 是否应参数化（False = 固定比例）


@dataclass
class VisualStructure:
    """
    参考图的结构化建模表示。

    由 image_to_plan.analyze_reference_image() 生成，
    由 visual_structure_to_gdl_hint() 转成 GDL 提示文本。
    """
    component_type: str                      # 构件中文名，如 "斗", "拱", "书架"
    main_form: str                           # 整体形态描述，如 "tapered_block_with_cross_slot"
    layers: list[VisualLayer] = field(default_factory=list)  # 从下往上的几何层
    symmetry: list[str] = field(default_factory=list)        # 对称轴，如 ["x", "y"]
    key_features: list[str] = field(default_factory=list)    # 关键形态特征
    dimension_hints: dict = field(default_factory=dict)      # 尺寸估算，如 {"width": "~0.6m"}
    parametrize: list[str] = field(default_factory=list)     # 应参数化的维度
    fix_as_ratio: list[str] = field(default_factory=list)    # 固定比例的派生量
    raw_description: str = ""                                 # LLM 原始描述（fallback）
