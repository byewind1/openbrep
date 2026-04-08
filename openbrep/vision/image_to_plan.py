"""
image_to_plan — 参考图 → VisualStructure → GDL 建模提示

Phase 1 核心：
1. analyze_reference_image()  — 图像 → VisualStructure（调用 LLM vision）
2. visual_structure_to_gdl_hint() — VisualStructure → 注入 GDL 生成的结构化提示文本
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from openbrep.vision.schema import VisualLayer, VisualStructure

if TYPE_CHECKING:
    from openbrep.llm import LLMAdapter

logger = logging.getLogger(__name__)

# ── 提示词 ────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是建筑构件视觉结构分析器。
用户会提供一张参考图（可能是照片、手绘、CAD截图或实物图），
你需要分析图中构件的几何结构，输出供 GDL 参数化建模使用的结构描述。

【重要约束】
- 不要生成任何 GDL 代码
- 只做结构分析，不做几何计算
- 尺寸给估算范围即可，不要精确数值
- fix_as_ratio 每一项必须是单一推荐值（如 0.35），不要写范围（不要写 0.30 - 0.40）
- 如果图片不清晰，优先描述能看清的部分

输出严格按以下 JSON 格式（不加任何 markdown 包裹）：
{
  "component_type": "构件中文名",
  "main_form": "整体形态的英文简短描述（snake_case）",
  "layers": [
    {
      "name": "层名（英文，如 base / waist / slot_body / lug）",
      "command": "推荐 GDL 命令（BLOCK / PRISM_ / CYLIND / CONE / SWEEP_）",
      "description": "这一层的中文说明",
      "parametric": true
    }
  ],
  "symmetry": ["x", "y"],
  "key_features": ["收分台座", "十字槽开口", "侧耳"],
  "dimension_hints": {"width": "约 0.6m", "height": "约 0.15m"},
  "parametrize": ["width", "depth", "height"],
  "fix_as_ratio": ["slot_width = width * 0.34", "base_height = height * 0.40"],
  "raw_description": "一句话描述这个构件"
}
"""

_USER_PROMPT_TEMPLATE = """\
请分析这张参考图中的建筑构件。

用户说明：{user_hint}

按 JSON 格式输出结构化描述，重点关注：
1. 构件名称和用途
2. 几何层次（从下往上，每层对应哪个 GDL 命令）
3. 哪些维度应参数化，哪些固定比例
4. 关键形态特征（收分、槽口、挑出部分等）
"""


# ── 主函数 ────────────────────────────────────────────────

def analyze_reference_image(
    image_b64: str,
    image_mime: str,
    user_hint: str,
    llm: "LLMAdapter",
) -> VisualStructure:
    """
    调用 LLM 对参考图做结构化分析，返回 VisualStructure。

    Args:
        image_b64:  base64 编码的图像数据
        image_mime: 图像 MIME 类型，如 "image/png"
        user_hint:  用户文字提示，如 "做一个斗"
        llm:        LLMAdapter 实例（需支持 vision）

    Returns:
        VisualStructure 对象，失败时返回带 raw_description 的最小结构
    """
    user_content: list = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
        },
        {
            "type": "text",
            "text": _USER_PROMPT_TEMPLATE.format(user_hint=user_hint or "（无额外说明）"),
        },
    ]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = llm.generate(messages, max_tokens=1200, temperature=0.1)
        raw = resp.content.strip()
        return _parse_response(raw)
    except Exception as exc:
        logger.warning("analyze_reference_image LLM call failed: %s", exc)
        return VisualStructure(
            component_type="未知构件",
            main_form="unknown",
            raw_description=f"图像分析失败：{exc}",
        )


def visual_structure_to_gdl_hint(vs: VisualStructure) -> str:
    """
    把 VisualStructure 转成注入 GDL 生成提示的结构化文本。

    这段文本会追加到用户指令之后，替代"直接看图生成"，
    给 GDL agent 提供明确的建模计划。
    """
    lines = ["## 参考图建模计划（请严格按此计划生成 GDL）"]
    lines.append(f"构件：{vs.component_type}")

    if vs.main_form:
        lines.append(f"整体形态：{vs.main_form}")

    if vs.key_features:
        lines.append(f"关键特征：{'、'.join(vs.key_features)}")

    if vs.symmetry:
        lines.append(f"对称性：{', '.join(vs.symmetry)} 轴对称")

    if vs.layers:
        lines.append("\n几何层次（从下往上，按顺序实现）：")
        for i, layer in enumerate(vs.layers, 1):
            param_tag = "（参数化）" if layer.parametric else "（固定比例）"
            lines.append(f"  {i}. [{layer.command}] {layer.name} — {layer.description} {param_tag}")

    if vs.parametrize:
        lines.append(f"\n主参数（必须出现在 paramlist.xml）：{', '.join(vs.parametrize)}")

    if vs.fix_as_ratio:
        lines.append("固定比例派生量（在 Master Script 或 3D Script 顶部计算）：")
        for ratio in vs.fix_as_ratio:
            lines.append(f"  {ratio}")

    if vs.dimension_hints:
        hints = "、".join(f"{k}: {v}" for k, v in vs.dimension_hints.items())
        lines.append(f"\n尺寸参考：{hints}（仅供比例参考，以参数为准）")

    if vs.raw_description:
        lines.append(f"\n补充说明：{vs.raw_description}")

    return "\n".join(lines)


# ── 解析辅助 ──────────────────────────────────────────────

def _fix_json_newlines(s: str) -> str:
    """把 JSON 字符串值里的裸换行替换为空格（LLM 有时会在 description 里换行）。"""
    result = []
    in_string = False
    escaped = False
    for ch in s:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\" and in_string:
            result.append(ch)
            escaped = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif ch == "\n" and in_string:
            result.append(" ")
        else:
            result.append(ch)
    return "".join(result)


def _parse_response(raw: str) -> VisualStructure:
    """
    解析 LLM 返回的 JSON，构建 VisualStructure。
    宽容解析：字段缺失时用默认值，不抛异常。
    """
    # 提取 JSON（LLM 有时会在前后加说明文字）
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        logger.warning("No JSON found in vision response, using raw text")
        return VisualStructure(
            component_type="未知构件",
            main_form="unknown",
            raw_description=raw[:500],
        )

    try:
        data = json.loads(_fix_json_newlines(raw[start:end]))
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed: %s | raw: %s", exc, raw[:200])
        return VisualStructure(
            component_type="未知构件",
            main_form="unknown",
            raw_description=raw[:500],
        )

    layers = []
    for item in data.get("layers", []):
        layers.append(VisualLayer(
            name=item.get("name", "unknown"),
            command=item.get("command", "BLOCK"),
            description=item.get("description", ""),
            parametric=bool(item.get("parametric", True)),
        ))

    return VisualStructure(
        component_type=data.get("component_type", "未知构件"),
        main_form=data.get("main_form", ""),
        layers=layers,
        symmetry=data.get("symmetry", []),
        key_features=data.get("key_features", []),
        dimension_hints=data.get("dimension_hints", {}),
        parametrize=data.get("parametrize", []),
        fix_as_ratio=data.get("fix_as_ratio", []),
        raw_description=data.get("raw_description", ""),
    )
