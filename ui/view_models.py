from __future__ import annotations

import re


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
