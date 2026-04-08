"""
IntentRouter — classifies user input into task intents.

Adapted from ui/app.py's classify_and_extract() and _is_debug_intent().
No Streamlit dependencies — usable from CLI and pipeline contexts.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Keyword Sets ──────────────────────────────────────────

_GDL_KEYWORDS = [
    # Actions (Chinese)
    "创建", "生成", "制作", "做一个", "建一个", "写一个", "写个", "写一",
    "做个", "建个", "来个", "整个", "出一个", "出个",
    "修改", "更新", "添加", "删除", "调整", "优化", "重写", "补充",
    # Objects (Chinese)
    "书架", "柜子", "衣柜", "橱柜", "储物柜", "鞋柜", "电视柜",
    "桌子", "桌", "椅子", "椅", "沙发", "床", "茶几", "柜",
    "窗", "门", "墙", "楼梯", "柱", "梁", "板", "扶手", "栏杆",
    "屋顶", "天花", "地板", "灯", "管道",
    # Technical
    "参数", "parameter", "script", "gdl", "gsm", "hsf",
    "compile", "编译", "build", "create", "make", "add",
    "3d", "2d", "prism", "block", "sphere", "prism_", "body",
    "project2", "rect2", "poly2",
]

_CHAT_ONLY_PATTERNS = [
    r"^(你好|hello|hi|hey|嗨|哈喽)[!！。\s]*$",
    r"^(谢谢|感谢|thanks)[!！。\s]*$",
    r"^你(是谁|能做什么|有什么功能)",
    r"^(怎么|如何|什么是).*(gdl|archicad|hsf|构件)",
]

_CREATE_KEYWORDS = [
    "创建", "生成", "新建", "做一个", "建一个", "写一个",
    "make", "create", "generate",
]

_MODIFY_KEYWORDS = {
    "检查", "语法", "报错", "错误",
    "修复", "修改", "改一下", "调整",
}

_DEBUG_KEYWORDS = {
    "debug", "fix", "error", "bug", "wrong", "issue", "broken", "fail", "crash",
    "问题", "错误", "调试", "检查", "分析", "为什么", "帮我看", "看看", "出错",
    "不对", "不行", "哪里", "原因", "解释", "explain", "why", "what", "how",
    "review", "看一下", "看下", "告诉我", "这段", "这个脚本",
}

_ARCHICAD_ERROR_PATTERN = re.compile(
    r"(error|warning)\s+in\s+\w[\w\s]*script[,\s]+line\s+\d+",
    re.IGNORECASE,
)


def _is_pure_chat(text: str) -> bool:
    return any(re.search(p, text.strip(), re.IGNORECASE) for p in _CHAT_ONLY_PATTERNS)


def _is_gdl_intent(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _GDL_KEYWORDS)


def _is_debug_intent(text: str) -> bool:
    """True if text looks like a debug/error-analysis request."""
    if text.startswith("[DEBUG:editor]"):
        return True
    if _ARCHICAD_ERROR_PATTERN.search(text):
        return True
    t = text.lower()
    return any(kw in t for kw in _DEBUG_KEYWORDS)


def _is_modify_or_check(text: str) -> bool:
    """True if text is clearly a check/fix/modify request (should skip elicitation)."""
    low = (text or "").strip().lower()
    return any(token in low for token in _MODIFY_KEYWORDS)


def _is_create_intent(text: str) -> bool:
    return any(kw in text for kw in _CREATE_KEYWORDS)


class IntentRouter:
    """
    Classifies user input into a task intent string.

    Intent values:
      CREATE  — build a new GDL object from scratch
      MODIFY  — check / fix / adjust an existing script
      DEBUG   — debug an error (Archicad error log or explicit debug keyword)
      IMAGE   — image-based task (has_image=True)
      CHAT    — pure conversational message
    """

    INTENTS = ["CREATE", "MODIFY", "DEBUG", "IMAGE", "CHAT"]

    def classify(
        self,
        user_input: str,
        has_project: bool = False,
        has_image: bool = False,
        llm=None,
    ) -> str:
        """
        Classify user input.

        Args:
            user_input:  The user's text.
            has_project: Whether a project is already loaded.
            has_image:   Whether an image was attached.
            llm:         Optional LLM for ambiguous cases (pass None to skip).

        Returns:
            One of "CREATE", "MODIFY", "DEBUG", "IMAGE", "CHAT".
        """
        if _is_pure_chat(user_input):
            return "CHAT"

        # Archicad error log / explicit debug prefix always wins
        if _is_debug_intent(user_input) and not _is_create_intent(user_input):
            return "DEBUG"

        # Explicit modify/check keyword → skip elicitation path
        if _is_modify_or_check(user_input):
            return "MODIFY"

        # Explicit creation keyword
        if _is_create_intent(user_input):
            return "CREATE"

        # Generic GDL keyword without clearer signal
        if _is_gdl_intent(user_input):
            return "MODIFY" if has_project else "CREATE"

        # Image present but text intent unclear → assume reference modeling (CREATE)
        if has_image:
            return "IMAGE"

        # Project loaded → default to modify for ambiguous input
        if has_project:
            return "MODIFY"

        # No project, ambiguous → ask LLM if available
        if llm is not None:
            try:
                resp = llm.generate([
                    {
                        "role": "system",
                        "content": (
                            "你是意图分类器。判断用户是否想创建或修改 ArchiCAD GDL 构件。\n"
                            "只回复一个词：GDL 或 CHAT\n"
                            "GDL = 要创建/修改/编译构件\n"
                            "CHAT = 闲聊/打招呼/问用法"
                        ),
                    },
                    {"role": "user", "content": user_input},
                ], max_tokens=10, temperature=0.1)
                raw = resp.content.strip().upper()
                return "CREATE" if "GDL" in raw else "CHAT"
            except Exception:
                pass

        return "CHAT"
