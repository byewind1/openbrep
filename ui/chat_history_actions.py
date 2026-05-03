from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from openbrep.learning import ChatTranscriptEntry, ErrorLearningStore


_INVALID_HSF_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def sanitize_hsf_name(raw: str, fallback: str = "chat_hsf") -> str:
    name = _INVALID_HSF_NAME_RE.sub("_", str(raw or "").strip())
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" ._-")
    if not name:
        return fallback
    return name[:48].strip(" ._-") or fallback


def previous_user_content(history: list[dict], index: int) -> str:
    for i in range(index - 1, -1, -1):
        message = history[i] if i < len(history) else {}
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def plain_chat_record_text(text: str) -> str:
    without_code = _CODE_FENCE_RE.sub(" ", str(text or ""))
    without_md = re.sub(r"(?m)^\s*([#>*`|_-]+|\d+\.)\s*", " ", without_code)
    return re.sub(r"\s+", " ", without_md).strip()


def chat_record_summary(message: dict, max_len: int = 52) -> str:
    text = plain_chat_record_text(str((message or {}).get("content") or ""))
    if not text:
        text = str((message or {}).get("content") or "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text or "(空记录)"
    return text[: max_len - 1].rstrip() + "…"


def suggest_hsf_name_from_chat_record(
    history: list[dict],
    index: int,
    *,
    extract_gsm_name_candidate_fn: Callable[[str], str] | None = None,
    fallback: str = "chat_hsf",
) -> str:
    message = history[index] if 0 <= index < len(history) else {}
    candidates = [
        previous_user_content(history, index),
        str((message or {}).get("content") or ""),
    ]

    if extract_gsm_name_candidate_fn is not None:
        for candidate_source in candidates:
            candidate = extract_gsm_name_candidate_fn(candidate_source)
            if candidate:
                return sanitize_hsf_name(candidate, fallback=fallback)

    for candidate_source in candidates:
        text = plain_chat_record_text(candidate_source)
        if not text:
            continue
        first_sentence = re.split(r"[。！？!?，,\n]", text, maxsplit=1)[0]
        cleaned = re.sub(
            r"^(请|帮我|帮忙|生成|创建|制作|做一个|做个|建一个|建个|修改|修复|解释|看看|看下|这个|一个|个)+",
            "",
            first_sentence,
        )
        cleaned = cleaned.strip(" ：:，,。.!！?？")
        if cleaned:
            return sanitize_hsf_name(cleaned, fallback=fallback)

    return fallback


def build_chat_record_entries(
    history: list[dict],
    *,
    classify_code_blocks_fn: Callable[[str], dict[str, str]] | None = None,
) -> list[dict]:
    entries: list[dict] = []
    for idx, message in enumerate(history or []):
        role = message.get("role", "assistant")
        role_label = "用户" if role == "user" else "助手"
        content = str(message.get("content") or "")
        has_code = "```" in content
        if classify_code_blocks_fn is not None:
            has_code = bool(classify_code_blocks_fn(content))
        entries.append(
            {
                "index": idx,
                "role": role,
                "role_label": role_label,
                "summary": chat_record_summary(message),
                "has_code": has_code,
            }
        )
    return entries


def transcript_entries_to_chat_messages(entries: list[ChatTranscriptEntry], *, limit: int = 120) -> list[dict]:
    messages: list[dict] = []
    for entry in list(entries or [])[-limit:]:
        role = entry.role if entry.role in {"user", "assistant"} else "assistant"
        content = str(entry.content or "").strip()
        if not content:
            continue
        messages.append({"role": role, "content": content})
    return messages


def hydrate_chat_history_from_workspace_memory(
    session_state,
    work_dir: str,
    *,
    limit: int = 120,
    store_factory: Callable[[str | Path], ErrorLearningStore] = ErrorLearningStore,
) -> int:
    workspace = str(work_dir or "").strip()
    if not workspace:
        return 0

    if (
        session_state.get("chat_record_history_loaded_work_dir") == workspace
        and session_state.get("chat_record_history")
    ):
        return 0

    entries = store_factory(workspace).list_chat_transcript()
    messages = transcript_entries_to_chat_messages(entries, limit=limit)
    session_state.chat_record_history = messages
    session_state.chat_record_history_loaded_work_dir = workspace
    return len(messages)


def close_chat_record_browser(session_state) -> None:
    session_state.chat_record_browser_open = False
    session_state.chat_record_open_idx = None
    session_state.chat_record_delete_idx = None


def delete_chat_record_entry(
    session_state,
    index: int,
    work_dir: str,
    *,
    store_factory: Callable[[str | Path], ErrorLearningStore] = ErrorLearningStore,
) -> tuple[bool, str]:
    history = list(session_state.get("chat_record_history") or [])
    if not (0 <= index < len(history)):
        return False, "聊天记录不存在或已被删除"

    remaining = history[:index] + history[index + 1:]
    session_state.chat_record_history = remaining
    close_chat_record_browser(session_state)

    workspace = str(work_dir or "").strip()
    if not workspace:
        return True, "已从当前列表删除"

    project = session_state.get("project")
    project_name = getattr(project, "name", "") if project is not None else ""
    try:
        store_factory(workspace).rewrite_chat_transcript(
            remaining,
            project_name=project_name,
            source="ui_chat",
        )
        session_state.chat_record_history_loaded_work_dir = workspace
    except Exception as exc:
        return False, f"删除记录失败：{exc}"
    return True, "已删除聊天记录"
