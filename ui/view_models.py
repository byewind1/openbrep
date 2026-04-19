from __future__ import annotations

import base64
import re
from typing import Callable


def build_generation_reply(plain_text: str, result_prefix: str = "", code_blocks: list[str] | None = None) -> str:
    reply_parts = []
    if plain_text:
        reply_parts.append(plain_text)
    if result_prefix:
        joined_blocks = "\n\n".join(code_blocks or [])
        reply_parts.append(result_prefix + joined_blocks)
    if reply_parts:
        return "\n\n---\n\n".join(reply_parts)
    return "🤔 AI 未返回代码或分析，请换一种描述方式。"



_PARAM_TYPE_RE = re.compile(
    r'^\s*(Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material)'
    r'\s+\w+\s*=',
    re.IGNORECASE | re.MULTILINE,
)


def strip_md_fences(code: str) -> str:
    cleaned = re.sub(r'^```[a-zA-Z]*\s*\n?', '', (code or '').strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned.strip(), flags=re.MULTILINE)
    return cleaned.strip()


def classify_code_blocks(text: str) -> dict[str, str]:
    collected: dict[str, str] = {}
    code_block_pat = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", re.DOTALL)
    for match in code_block_pat.finditer(text or ""):
        block = match.group(1).strip()
        if not block:
            continue
        block_up = block.upper()
        if len(_PARAM_TYPE_RE.findall(block)) >= 2:
            path = "paramlist.xml"
        elif re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', block_up):
            path = "scripts/2d.gdl"
        elif re.search(r'\bVALUES\b|\bLOCK\b', block_up) and not re.search(r'\bBLOCK\b', block_up):
            path = "scripts/vl.gdl"
        elif re.search(r'\bGLOB_\w+\b', block_up):
            path = "scripts/1d.gdl"
        elif re.search(r'\bUI_CURRENT\b|\bDEFINE\s+STYLE\b|\bUI_DIALOG\b|\bUI_PAGE\b|\bUI_INFIELD\b|\bUI_OUTFIELD\b|\bUI_BUTTON\b|\bUI_GROUPBOX\b|\bUI_LISTFIELD\b|\bUI_SEPARATOR\b', block_up):
            path = "scripts/ui.gdl"
        else:
            path = "scripts/3d.gdl"
        collected[path] = block
    return collected


def extract_gdl_from_text(text: str) -> dict[str, str]:
    return classify_code_blocks(text)


def build_chat_script_anchors(history: list[dict]) -> list[dict]:
    anchors: list[dict] = []
    rev = 1
    for i, message in enumerate(history or []):
        if message.get("role") != "assistant":
            continue
        extracted = classify_code_blocks(message.get("content", ""))
        if not extracted:
            continue
        script_keys = [
            path.replace("scripts/", "").replace(".gdl", "").upper()
            for path in extracted.keys()
            if path.startswith("scripts/")
        ]
        parts = []
        if script_keys:
            parts.append("/".join(script_keys))
        if "paramlist.xml" in extracted:
            parts.append("PARAM")
        scope = " + ".join(parts) if parts else "CODE"
        anchors.append(
            {
                "rev": rev,
                "msg_idx": i,
                "label": f"r{rev} · {scope}",
                "paths": sorted(extracted.keys()),
            }
        )
        rev += 1
    return anchors


_INTENT_CLARIFY_ACTION_LABELS = {
    "1": "先快速解释脚本结构",
    "2": "先检查明显错误/风险",
    "3": "先给修改建议",
    "4": "按顺序都做，但先给简版总检",
}


def build_intent_clarification_message(recommended_option: str) -> str:
    recommendation = _INTENT_CLARIFY_ACTION_LABELS.get(
        recommended_option,
        _INTENT_CLARIFY_ACTION_LABELS["2"],
    )
    return (
        f"我猜你现在更像是想{recommendation}。\n"
        "你也可以选：\n"
        "1. 先快速解释脚本结构\n"
        "2. 先检查明显错误/风险\n"
        "3. 先给修改建议\n"
        "4. 按顺序都做，但先给简版总检\n"
        "回复数字就行，我再继续。"
    )


def build_post_clarification_input(original_user_input: str, option: str) -> str:
    label = _INTENT_CLARIFY_ACTION_LABELS[option]
    return (
        "基于刚才的用户确认，按下面目标继续处理：\n"
        f"用户原始请求：{(original_user_input or '').strip()}\n"
        f"本次确认目标：{label}"
    )


def consume_intent_clarification_choice(user_input: str, pending: dict | None) -> str | None:
    normalized = (user_input or "").strip()
    if not pending or normalized not in (pending.get("options") or {}):
        return None
    return build_post_clarification_input(pending.get("original_user_input", ""), normalized)


def clear_pending_intent_clarification(session_state) -> None:
    session_state["pending_intent_clarification"] = None


_EXPLAINER_FOLLOWUP_MODIFY_PATTERNS = (
    re.compile(r"^按你刚才说的改[吧啊呀]?$"),
    re.compile(r"^按这个思路改[吧啊呀]?$"),
    re.compile(r"^那就改吧$"),
    re.compile(r"^就按这个改$"),
    re.compile(r"^按这个修改$"),
)


def is_bridgeable_explainer_message(message: dict) -> bool:
    return (
        (message or {}).get("role") == "assistant"
        and (message or {}).get("bridgeable_action") == "modify_from_explainer"
        and bool((message or {}).get("content", "").strip())
    )


def is_explainer_followup_modify_request(text: str) -> bool:
    normalized = re.sub(r"\s+", "", (text or "").strip())
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _EXPLAINER_FOLLOWUP_MODIFY_PATTERNS)


def find_latest_bridgeable_explainer_message(history: list[dict]) -> dict | None:
    for message in reversed(history or []):
        if is_bridgeable_explainer_message(message):
            return message
    return None


def build_modify_bridge_prompt(message: dict, fallback_request: str = "") -> str:
    explanation = (message or {}).get("content", "").strip()
    source_request = (message or {}).get("bridge_source_user_input", "").strip()
    target_request = (fallback_request or "").strip() or "请按上面的解释做最小必要修改。"
    if source_request:
        return (
            "基于刚才的解释，按下面理解做最小修改：\n"
            f"原解释问题：{source_request}\n"
            f"解释结论：{explanation}\n"
            f"用户修改要求：{target_request}"
        )
    return (
        "基于刚才的解释，按下面理解做最小修改：\n"
        f"解释结论：{explanation}\n"
        f"用户修改要求：{target_request}"
    )


def maybe_build_followup_bridge_input(user_input: str, history: list[dict], has_project: bool) -> str | None:
    if not has_project or not is_explainer_followup_modify_request(user_input):
        return None
    bridge_message = find_latest_bridgeable_explainer_message(history)
    if not bridge_message:
        return None
    return build_modify_bridge_prompt(bridge_message, fallback_request=user_input)


def is_modify_bridge_prompt(text: str) -> bool:
    normalized = (text or "").strip()
    return normalized.startswith("基于刚才的解释，按下面理解做最小修改：")


def is_post_clarification_prompt(text: str) -> bool:
    normalized = (text or "").strip()
    return normalized.startswith("基于刚才的用户确认，按下面目标继续处理：")


def build_assistant_chat_message(content: str, intent: str, has_project: bool, source_user_input: str) -> dict:
    message = {"role": "assistant", "content": content}
    if intent == "CHAT" and has_project:
        message["bridgeable_action"] = "modify_from_explainer"
        message["bridge_source_user_input"] = source_user_input
    return message


def classify_vision_error(exc: Exception) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    lower_msg = msg.lower()
    if isinstance(exc, TimeoutError) or "timeout" in lower_msg or "timed out" in lower_msg:
        return "图片分析超时：请换更小的图片，或检查当前模型服务/代理是否响应正常。"
    if "配置错误" in msg or "api key" in lower_msg or "authentication" in lower_msg or "unauthorized" in lower_msg:
        return msg
    if any(token in lower_msg for token in ["payload", "too large", "413", "context length", "image too large", "request entity too large"]):
        return "图片过大或请求体过长：请压缩图片，或减少附带说明后重试。"
    if any(token in lower_msg for token in ["vision", "image_url", "image", "unsupported"]):
        return f"当前模型或网关不支持图片分析：{msg}"
    return f"图片分析失败：{msg}"


def validate_chat_image_size(raw_bytes: bytes, image_name: str, max_chat_image_bytes: int) -> str | None:
    if raw_bytes and len(raw_bytes) > max_chat_image_bytes:
        size_mb = len(raw_bytes) / (1024 * 1024)
        return f"图片 `{image_name}` 过大（{size_mb:.1f} MB），请压缩到 5 MB 以内再试。"
    return None


def trim_history_for_image(history: list[dict], limit: int = 4) -> list[dict]:
    if not history:
        return []
    return history[-limit:]


def thumb_image_bytes(image_b64: str) -> bytes | None:
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except Exception:
        return None


def detect_image_task_mode(user_text: str, image_name: str = "", has_project: bool = False) -> str:
    t = (user_text or "").lower()
    n = (image_name or "").lower()

    debug_tokens = [
        "debug", "error", "报错", "错误", "失败", "修复", "定位", "排查", "warning", "line ", "script",
        "screenshot", "截图", "log", "trace", "崩溃", "不显示", "异常",
    ]
    gen_tokens = [
        "生成", "创建", "建模", "构件", "参考", "外观", "照片", "photo", "reference", "design",
    ]

    if any(k in t for k in debug_tokens):
        return "debug"
    if any(k in t for k in gen_tokens):
        return "generate"

    if any(k in n for k in ["screenshot", "screen", "截屏", "截图", "error", "debug", "log"]):
        return "debug"
    if any(k in n for k in ["photo", "img", "image", "参考", "模型", "design"]):
        return "generate"

    if has_project:
        return "debug"
    return "generate"


def is_positive_confirmation(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(token in low for token in ["确认", "可以", "是", "对", "生成吧", "没问题", "好的", "开始"])


def is_negative_confirmation(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(token in low for token in ["不是", "不对", "重来", "修改", "不", "错了", "再改"])


def is_modify_or_check_intent(text: str, is_debug_intent: bool = False) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if is_debug_intent:
        return False
    if any(token in raw for token in ("检查", "校验", "语法", "语义")):
        return True
    modify_tokens = (
        "改", "修改", "调整", "更新", "优化", "重写", "补充", "添加", "删除", "修正",
    )
    return any(token in raw for token in modify_tokens)


def is_explainer_intent(
    text: str,
    *,
    is_post_clarification_prompt: Callable[[str], bool],
    is_debug_intent: Callable[[str], bool],
    is_modify_or_check_intent: Callable[[str], bool],
    explainer_keywords: set[str],
) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if is_post_clarification_prompt(raw):
        return "本次确认目标：先快速解释脚本结构" in text
    if is_debug_intent(raw):
        explainer_overrides = (
            "解释", "拆解", "分析", "代码分析", "逻辑分析", "命令分析",
            "负责什么", "控制什么", "作用", "什么意思",
        )
        if not any(token in raw for token in explainer_overrides):
            return False
    if is_modify_or_check_intent(raw):
        return False
    if any(token in raw for token in ("代码分析", "逻辑分析", "命令分析")):
        return True
    if re.search(r"\b(?:1d|2d|3d|param|ui|properties|property|master)\b", raw):
        script_question_tokens = ("解释", "分析", "负责什么", "作用", "逻辑", "命令", "脚本")
        if any(token in raw for token in script_question_tokens):
            return True
    return any(token in raw for token in explainer_keywords)


def should_clarify_intent(
    text: str,
    *,
    has_project: bool,
    is_modify_bridge_prompt: Callable[[str], bool],
    has_followup_bridge: bool,
    is_post_clarification_prompt: Callable[[str], bool],
    is_debug_intent: Callable[[str], bool],
    is_explainer_intent: Callable[[str], bool],
) -> bool:
    raw = (text or "").strip()
    if not raw or not has_project:
        return False
    if is_modify_bridge_prompt(raw):
        return False
    if has_followup_bridge:
        return False
    if is_post_clarification_prompt(raw):
        return False
    mixed_tokens = ("解释", "检查", "修改意见")
    if sum(token in raw for token in mixed_tokens) >= 2:
        return True
    if raw in {"看看这个", "这个怎么处理", "这个有问题吗"}:
        return True
    if is_debug_intent(raw) or is_explainer_intent(raw):
        return False
    if re.search(r"改成\s*\d+", raw):
        return False
    return False
