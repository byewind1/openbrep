"""
harness — Vision Harness 装配（P5b S1/S2/S4，P5c 加 S3 critic 校验 + 字段级置信度）

run(images, intent, user_input, llm, on_event=None, critic_pass=True)
    -> list[ModelingPlan | None]
    - S1 分型：每图 derive_role（写回 ImageRef.role）+ 每任务 select_schema
    - S2 定向提取：generic → analyze_reference_image 平移（原函数原 prompt，
      输出与 P5a 现状逐字节一致）；lattice_window / furniture_stack →
      schema.extract_prompt 调 llm.generate_with_image，严格 JSON 解析；
      P5c 起输出信封 {fields, confidence, raw_description}，字段级置信度
      写入 ModelingPlan.confidence（不再全 unknown）。
    - S3 critic 校验（P5c，设计 D3，bounded 1 轮）：每图一次 generate_with_image，
      只核 schema.critic_checks 字段；"不匹配+依据" 改值并记 corrections，
      "无法判断" 标 low，"匹配" 标 high；critic 无权动 critic_checks 之外字段。
      触发条件：intent ∈ {CREATE, IMAGE} + critic_checks 非空 + critic_pass=on
      + 该图提取未降级。critic 调用失败 → 全字段 confidence=unknown 降级
      （设计 D8），不阻塞流程。
    - S4 合成：每图一个 ModelingPlan（与输入 images 对齐，无字节的图 → None），
      由 pipeline 按【图N】前缀拼入 enriched_instruction

降级（设计 D8，必须可见）：
    - schema 提取 JSON 解析失败 → raw_description 降级 + hint 带"【分析失败已降级】"
      标记 + on_event("vision_degraded")；降级图跳过 critic（无可信 JSON 可核）
    - critic 调用失败/输出不可解析 → 跳过校验，该图字段全标 confidence=unknown，
      hint 带"【critic 校验已降级】"标记 + on_event("vision_degraded")
    - 单图分析异常 → 该图 plan=None（跳过该图 hint，与 P5a 语义一致）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from openbrep.vision.extraction_store import plan_to_dict
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

# S3 critic 的系统提示：只出 JSON、只核对必核字段、不给 GDL 代码
_CRITIC_SYSTEM_PROMPT = """\
你是建筑构件参考图核对员。用户会提供一张参考图与一次机器提取的结构化结果，
你需要逐字段核对必核字段的提取值是否与图中信息一致。
【重要约束】
- 不要生成任何 GDL 代码
- 只核对列出的必核字段，不要修改其他字段
- 输出严格按 JSON 格式（不加任何 markdown 包裹）
"""


def run(
    images: list,
    intent: str,
    user_input: str,
    llm: Any,
    on_event: Optional[Callable] = None,
    critic_pass: bool = True,
) -> list[Optional[ModelingPlan]]:
    """对有序多图跑 Vision Harness（S1 分型 → S2 定向提取 → S3 critic → S4 合成）。

    critic_pass: [vision] critic_pass 开关（P5c，默认 on）。off 时 S3 不跑。

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
                "token": img.token or "",
                # P5d-1：事件 payload 携带提取摘要（只读卡片数据源，不改变 hint）
                "extraction": plan_to_dict(plan),
            })
        else:
            plan = _schema_plan(schema, img, user_input, llm)
            # S3 critic（设计 D3，bounded 1 轮）：
            #   触发 = 意图 CREATE/IMAGE + schema 声明 critic_checks + 开关 on
            #         + 该图提取未降级（degraded 无可信 JSON 可核，跳过）。
            if (
                plan is not None
                and not plan.degraded
                and schema.critic_checks
                and intent in ("CREATE", "IMAGE")
                and critic_pass
            ):
                plan = _critic_pass(schema, plan, img, user_input, llm, on_event=on_event)
            on_event("vision_analysis_done", {
                "schema_name": schema.name,
                "image_index": idx,
                "token": img.token or "",
                # P5d-1：事件 payload 携带提取摘要（只读卡片数据源，不改变 hint）
                "extraction": plan_to_dict(plan),
            })
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
    """schema 定向提取：extract_prompt + 用户说明 → 严格 JSON → ModelingPlan。

    P5c 输出信封 {fields, confidence, raw_description}：fields 按 schema.fields
    声明顺序收窄；confidence 按字段收窄并归一（high/low，null 值字段强制 low）；
    兼容旧平铺结构（无 fields 键 → 整对象视为 fields，confidence 全 unknown）。
    """
    text_prompt = f"{schema.extract_prompt.strip()}\n\n用户说明：{user_input or '（无额外说明）'}"
    raw = ""
    try:
        resp = llm.generate_with_image(
            text_prompt,
            img.b64,
            img.mime,
            system_prompt=_SCHEMA_SYSTEM_PROMPT,
            # 不传 temperature：部分 provider（如 kimi-k2.6）对 temperature 有
            # 硬约束（仅 0.6/1），硬编码 0.1 会被端点 400 拒绝——交给
            # LLMAdapter._effective_temperature 按 provider 条目级配置决定。
            max_tokens=1200,
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

    # 信封解析：{fields, confidence, raw_description}；兼容旧平铺结构
    envelope = data.get("fields")
    if isinstance(envelope, dict):
        fields_data = envelope
        confidence_data = data.get("confidence")
        raw_value = data.get("raw_description")
    else:
        fields_data = data  # 旧平铺结构（P5b 形状），confidence 全 unknown
        confidence_data = None
        raw_value = data.get("raw_description")

    # 按 schema.fields 声明顺序收窄字段（LLM 可能多吐/乱序）
    fields: dict[str, Any] = {
        key: fields_data[key] for key in schema.fields if key in fields_data
    }
    confidence = _extract_confidence(schema, fields, confidence_data)
    raw_description_out = str(raw_value or "") if raw_value is not None else ""

    return ModelingPlan(
        schema_name=schema.name,
        fields=fields,
        confidence=confidence,
        corrections=[],
        source_images=[img.sha256] if img.sha256 else [],
        raw_description=raw_description_out,
    )


def _extract_confidence(schema: VisionSchema, fields: dict, confidence_data: Any) -> dict:
    """把提取响应的 confidence 收窄到 fields 并归一（high/low/unknown）。

    规则：
    - 模型没给 confidence（旧平铺结构）→ 全 unknown；
    - 值非 high/low → unknown；
    - 字段值为 null（无法判断）→ 强制 low（对 null 不可能 high）。
    """
    if not isinstance(confidence_data, dict):
        return {k: "unknown" for k in fields}
    out: dict[str, str] = {}
    for key in fields:
        value = confidence_data.get(key)
        if isinstance(value, str) and value.strip().lower() in ("high", "low"):
            out[key] = value.strip().lower()
        else:
            out[key] = "unknown"
        if fields.get(key) is None and out[key] != "unknown":
            out[key] = "low"
    return out


# ── S3 critic 校验（P5c，设计 D3）─────────────────────────

def _critic_pass(
    schema: VisionSchema,
    plan: ModelingPlan,
    img,
    user_input: str,
    llm,
    *,
    on_event: Optional[Callable] = None,
) -> ModelingPlan:
    """每图一次 critic 调用：复读图 + 提取 JSON → 逐字段核对 critic_checks。

    修正语义（严格遵守设计 D3）：
    - "mismatch" + evidence + 修正值 → 改值，corrections 记 {field, old, new, evidence}，
      confidence 提为 high（critic 已核）；
    - "unknown" → confidence=low，不改值；
    - "match" → confidence=high；
    - mismatch 但缺依据/缺修正值 → confidence=low，不改值（零静默：标不可靠但不盲改）；
    - critic 无权动 critic_checks 之外的字段（返回里带了也忽略，越权防护）。

    降级（设计 D8）：critic 调用失败或输出不可解析 → 跳过校验，该图字段全标
    confidence=unknown，不阻塞流程，hint 带【critic 校验已降级】标记。
    """
    on_event = on_event or (lambda *_: None)
    try:
        resp = llm.generate_with_image(
            _critic_text_prompt(schema, plan),
            img.b64,
            img.mime,
            system_prompt=_CRITIC_SYSTEM_PROMPT,
            max_tokens=1200,
        )
        data = _parse_schema_json((resp.content or "").strip())
        verdicts = data.get("verdicts")
        if not isinstance(verdicts, dict):
            raise ValueError("critic response missing 'verdicts' object")
        _apply_verdicts(plan, schema, verdicts)
        on_event("vision_critic_done", {
            "schema_name": schema.name,
            "image_index": getattr(img, "token", ""),
            "corrections": list(plan.corrections),
            "checked": sorted(schema.critic_checks),
        })
    except Exception as exc:
        logger.warning(
            "vision harness: critic pass failed for %s (%s): %s",
            getattr(img, "token", "?"), schema.name, exc,
        )
        # D8：跳过校验，全字段 unknown，不阻塞流程
        plan.confidence = {k: "unknown" for k in plan.confidence}
        plan.critic_degraded = True
        on_event("vision_degraded", {
            "schema_name": schema.name,
            "image_index": getattr(img, "token", ""),
            "reason": f"critic pass failed, verification skipped: {exc}",
        })
    return plan


def _critic_text_prompt(schema: VisionSchema, plan: ModelingPlan) -> str:
    """critic 指令：图 + 提取 JSON + 必核字段清单（含当前值）。"""
    extraction = dict(plan.fields)
    if plan.raw_description:
        extraction["raw_description"] = plan.raw_description
    field_lines = []
    for path in schema.critic_checks:
        value = _get_path(plan.fields, path)
        field_lines.append(f"- {path} = {_render_value(value)}")
    return (
        "这是建筑构件参考图。下面是机器对这张图的提取结果与必核字段清单。\n"
        "请逐字段核对提取值是否与图中信息一致，只核对列出的必核字段。\n\n"
        f"提取结果 JSON：\n{json.dumps(extraction, ensure_ascii=False)}\n\n"
        "必核字段（含当前提取值）：\n"
        + "\n".join(field_lines)
        + "\n\n"
        "对每个必核字段输出 verdict：\n"
        '- "match"：提取值与图中一致（evidence 说明图中依据）\n'
        '- "mismatch"：提取值与图中不一致，必须给出 evidence 与修正后的 value\n'
        '- "unknown"：图中无法判断，evidence 说明原因\n\n'
        '输出 JSON（严格，不加 markdown 包裹）：\n'
        '{\n'
        '  "verdicts": {\n'
        '    "<字段路径>": {\n'
        '      "verdict": "match" | "mismatch" | "unknown",\n'
        '      "evidence": "图中依据（具体到可见特征）",\n'
        '      "value": <仅 mismatch 时填修正值，其余情况省略或 null>\n'
        '    }\n'
        '  }\n'
        '}\n'
    )


def _apply_verdicts(plan: ModelingPlan, schema: VisionSchema, verdicts: dict) -> None:
    """按 D3 语义应用 critic 逐字段裁决（含越权防护）。"""
    checks = set(schema.critic_checks)
    for field, raw in (verdicts or {}).items():
        if field not in checks:
            # 越权防护：critic 无权动 critic_checks 之外的字段，返回里带了也忽略
            logger.info(
                "vision harness: critic returned out-of-scope field %r, ignored (D3)", field
            )
            continue
        if not isinstance(raw, dict):
            plan.confidence[field] = "low"
            continue
        verdict = str(raw.get("verdict") or "").strip().lower()
        evidence = str(raw.get("evidence") or "").strip()
        if verdict == "match":
            plan.confidence[field] = "high"
        elif verdict == "unknown":
            plan.confidence[field] = "low"
        elif verdict == "mismatch":
            has_value = "value" in raw and raw.get("value") is not None
            if evidence and has_value:
                old = _get_path(plan.fields, field)
                new = _coerce_value(old, raw["value"])
                _set_path(plan.fields, field, new)
                plan.corrections.append({
                    "field": field,
                    "old": old,
                    "new": new,
                    "evidence": evidence,
                })
                plan.confidence[field] = "high"
            else:
                # 说"不匹配"却给不出依据或修正值 → 值不可靠，标 low 但不盲改
                plan.confidence[field] = "low"
        else:
            plan.confidence[field] = "low"


# ── 点路径工具 ────────────────────────────────────────────

def _get_path(fields: dict, path: str):
    """按点路径取嵌套值（grid_topology.rows → fields["grid_topology"]["rows"]）。"""
    node: Any = fields
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(fields: dict, path: str, value: Any) -> None:
    """按点路径写嵌套值；中间缺层用 dict 补齐。"""
    parts = path.split(".")
    node = fields
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _coerce_value(old, new):
    """修正值类型对齐：old 是 int/float 而新值是数字字符串时转回数字。"""
    if isinstance(old, bool) or isinstance(new, bool):
        return new
    if isinstance(old, int) and isinstance(new, str):
        try:
            return int(new)
        except (TypeError, ValueError):
            return new
    if isinstance(old, float) and isinstance(new, str):
        try:
            return float(new)
        except (TypeError, ValueError):
            return new
    return new


def _render_value(value: Any) -> str:
    """字段当前值渲染进 critic prompt（dict/list → JSON，None → null）。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


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
