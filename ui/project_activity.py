from __future__ import annotations

from datetime import datetime


MAX_PROJECT_ACTIVITY_LOG = 20


def is_project_activity_message(content: str) -> bool:
    text = str(content or "").lstrip()
    if text.startswith(("✅ 已导入", "✅ 已加载 HSF 项目", "❌ [IMPORT_GSM]")):
        return True
    return "HSF 文件列表" in text or "源目录:" in text


def record_project_activity(session_state, message: str) -> None:
    entries = list(session_state.get("project_activity_log") or [])
    entries.append(
        {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "message": str(message or ""),
        }
    )
    session_state.project_activity_log = entries[-MAX_PROJECT_ACTIVITY_LOG:]


def project_activity_entries(session_state) -> list[dict]:
    entries = list(session_state.get("project_activity_log") or [])
    legacy_messages = [
        {"timestamp": "", "message": str(msg.get("content", ""))}
        for msg in session_state.get("chat_history", [])
        if msg.get("role", "assistant") == "assistant"
        and is_project_activity_message(msg.get("content", ""))
    ]
    return (legacy_messages + entries)[-MAX_PROJECT_ACTIVITY_LOG:]
