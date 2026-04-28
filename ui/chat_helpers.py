from __future__ import annotations

import subprocess
import sys
from typing import Callable


def extract_gdl_from_chat(session_state, classify_code_blocks_fn: Callable[[str], dict]) -> dict:
    """Scan assistant messages in chat history; last block per type wins."""
    collected: dict[str, str] = {}
    for msg in session_state.get("chat_history", []):
        if msg.get("role") != "assistant":
            continue
        for path, block in classify_code_blocks_fn(msg.get("content", "")).items():
            collected[path] = block
    return collected


def should_show_copyable_chat_content(message: dict) -> bool:
    return message.get("role") == "assistant" and bool((message.get("content") or "").strip())


def copyable_chat_text(message: dict) -> str:
    if not should_show_copyable_chat_content(message):
        return ""
    return str(message.get("content") or "")


def copy_text_to_system_clipboard(
    text: str,
    *,
    platform: str | None = None,
    run_fn: Callable[..., object] | None = None,
) -> tuple[bool, str]:
    payload = (text or "").strip()
    if not payload:
        return False, "当前消息无可复制内容"

    platform_name = platform if platform is not None else sys.platform
    run = run_fn or subprocess.run
    try:
        if platform_name == "darwin":
            run(["pbcopy"], input=payload, text=True, check=True, timeout=2)
            return True, "已复制本条回复"
        if platform_name.startswith("linux"):
            run(
                ["xclip", "-selection", "clipboard"],
                input=payload,
                text=True,
                check=True,
                timeout=2,
            )
            return True, "已复制本条回复"
        if platform_name.startswith("win"):
            run(["clip"], input=payload, text=True, check=True, timeout=2)
            return True, "已复制本条回复"
        return False, "当前系统暂不支持自动复制"
    except Exception:
        return False, "复制失败，请重试"
