"""
harness — Vision Harness 装配（P5b，设计 §3 S1→S4 中的 S1/S2/S4）

run(images, intent, user_input, llm) -> list[ModelingPlan | None]
    - S1 分型：每图 derive_role（写回 ImageRef.role）+ 每任务 select_schema
    - S2 定向提取：generic → analyze_reference_image 平移（原函数原 prompt，
      输出与 P5a 现状逐字节一致）；lattice_window / furniture_stack →
      schema.extract_prompt 调 llm.generate_with_image，严格 JSON 解析
    - S4 合成：每图一个 ModelingPlan（与输入 images 对齐，无字节的图 → None），
      由 pipeline 按【图N】前缀拼入 enriched_instruction

降级（设计 D8，必须可见）：
    - schema 提取 JSON 解析失败 → raw_description 降级 + hint 带"【分析失败已降级】"
      标记 + on_event("vision_degraded")
    - 单图分析异常 → 该图 plan=None（跳过该图 hint，与 P5a 语义一致）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from openbrep.vision.image_to_plan import analyze_reference_image
from openbrep.vision.modeling_plan import ModelingPlan
from openbrep.vision.schema_registry import VisionSchema, load_all_schemas
from openbrep.vision.triage import derive_role, select_schema

logger = logging.getLogger(__name__)

# schema 定向提取的系统提示：只出 JSON、不给 GDL 代码（提取逻辑本体在 extract_prompt）
_SCHEMA_SYSTEM_PROMPT = """\
你是建筑构件视觉结构分析器。用户会提供一张参考图，你需要按给定的字段定义
提取 GDL 参数化建模必需的信息。
【重要约束】
- 不要生成任何 GDL 代码
- 只做结构分析，不做几何计算
- 输出严格按 JSON 格式（不加任何 markdown 包裹）
- 无法判断的字段用 null
"""


def run(
    images: list,
    intent: str,
    user_input: str,
    llm: Any,
    on_event: Optional[Callable] = None,
) -> list[Optional[ModelingPlan]]:
    """对有序多图跑 Vision Harness（S1 分型 → S2 定向提取 → S4 合成）。

    Returns:
        list[ModelingPlan | None] —— 与输入 images 一一对齐；无字节的图返回 None
        （pipeline 据此按【图N】位置前缀拼装，保证与 P5a 逐字节一致）。
    """
    on_event = on_event or (lambda *_: None)
    schemas = load_all_schemas()
    schema_name = select_schema(user_input, schemas)
    schema = schemas[schema_name]

    plans: list[Optional[ModelingPlan]] = []
    total = len(images)
    for idx, img in enumerate(images, start=1):
        if not img.b64:
            plans.append(None)
            continue
        role = derive_role(img.token, user_input, idx, total)
        img.role = role

        if schema_name == "generic":
            plan = _generic_plan(img, user_input, llm)
            if plan is None:
                plans.append(None)
                continue
            vs = plan.fields.get("visual_structure")
            on_event("vision_analysis_done", {
                "component_type": vs.component_type if vs is not None else "unknown",
                "image_index": idx,
            })
        else:
            plan = _schema_plan(schema, img, user_input, llm)
            on_event("vision_analysis_done", {"schema_name": schema.name, "image_index": idx})
            if plan.degraded:
                on_event("vision_degraded", {
                    "schema_name": schema.name,
                    "image_index": idx,
                    "reason": "schema extraction failed, degraded to raw description",
                })
        plans.append(plan)
    return plans


def _generic_plan(img, user_input: str, llm) -> Optional[ModelingPlan]:
    """generic 平移：原函数原 prompt，包装进 ModelingPlan，to_hint 转调现函数。"""
    try:
        vs = analyze_reference_image(img.b64, img.mime, user_input, llm)
    except Exception as exc:
        logger.warning("vision harness: generic analysis failed for %s: %s", img.token, exc)
        return None
    return ModelingPlan(
        schema_name="generic",
        fields={"visual_structure": vs},
        confidence={},
        corrections=[],
        source_images=[img.sha256] if img.sha256 else [],
        raw_description=vs.raw_description,
    )


def _schema_plan(schema: VisionSchema, img, user_input: str, llm) -> ModelingPlan:
    """schema 定向提取：extract_prompt + 用户说明 → 严格 JSON → ModelingPlan.fields。"""
    text_prompt = f"{schema.extract_prompt.strip()}\n\n用户说明：{user_input or '（无额外说明）'}"
    raw = ""
    try:
        resp = llm.generate_with_image(
            text_prompt,
            img.b64,
            img.mime,
            system_prompt=_SCHEMA_SYSTEM_PROMPT,
            max_tokens=1200,
            temperature=0.1,
        )
        raw = (resp.content or "").strip()
        data = _parse_schema_json(raw)
    except Exception as exc:
        logger.warning(
            "vision harness: schema %s extraction failed for %s: %s", schema.name, img.token, exc
        )
        return ModelingPlan(
            schema_name=schema.name,
            fields={},
            confidence={},
            corrections=[],
            source_images=[img.sha256] if img.sha256 else [],
            raw_description=raw[:500] or f"图像分析失败：{exc}",
            degraded=True,
        )

    # 按 schema.fields 声明顺序收窄字段（LLM 可能多吐/乱序），raw_description 单独取出
    fields: dict[str, Any] = {}
    raw_description = ""
    for key in schema.fields:
        if key not in data:
            continue
        if key == "raw_description":
            raw_description = str(data[key] or "")
            continue
        fields[key] = data[key]
    return ModelingPlan(
        schema_name=schema.name,
        fields=fields,
        confidence={k: "unknown" for k in fields},
        corrections=[],
        source_images=[img.sha256] if img.sha256 else [],
        raw_description=raw_description,
    )


def _parse_schema_json(raw: str) -> dict:
    """严格 JSON 解析：剥掉可能的 markdown 围栏与前后说明文字。"""
    text = raw.strip()
    if text.startswith("```"):
        # 剥 ```json ... ``` 围栏
        first = text.find("\n")
        last = text.rfind("```")
        if first != -1 and last > first:
            text = text[first + 1 : last].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in schema extraction response")
    data = json.loads(text[start:end])
    if not isinstance(data, dict):
        raise ValueError("Schema extraction JSON is not an object")
    return data
