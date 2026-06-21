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

_KNOWLEDGE_QUESTION_PATTERNS = [
    r"(什么是|介绍|讲解|解释|说明|含义|概念|区别)",
    r"(怎么用|如何使用|用法|语法|命令|参数|示例|例子|教学)",
]

_QUESTION_PREFIX_RE = re.compile(r"^\s*(什么|怎么|怎样|如何|为何|为什么|请问)")
_COMMAND_NAME_RE = re.compile(r"\b[A-Z_]{3,}(?:\{\d+\})?\b")

_CREATE_KEYWORDS = [
    "创建", "生成", "新建", "做一个", "建一个", "写一个",
    "make", "create", "generate",
]

_MODIFY_KEYWORDS = {
    "检查", "语法", "报错", "错误",
    "修复", "修改", "改一下", "调整",
}

# Strong debug keywords: always route to DEBUG without requiring error log evidence.
# Keep only words that are unambiguously "debugging a broken thing".
_STRONG_DEBUG_KEYWORDS: frozenset[str] = frozenset({
    "debug", "bug", "broken", "crash",
    "调试", "出错", "报错",
})

# Weak debug keywords: only route to DEBUG when explicit error log evidence is present.
# Words like "fix", "error", "wrong" are too generic on their own ("fix the width").
_WEAK_DEBUG_KEYWORDS: frozenset[str] = frozenset({
    "fix", "error", "fail", "wrong", "issue",
    "错误", "问题", "不对",
})

# Explicit error log feature pattern (LP_XMLConverter / Archicad stderr format).
# Presence of any of these in the message is strong evidence the user is pasting an error log.
_ERROR_LOG_SIGNATURE_RE = re.compile(
    r"(?:error\s*[:：]"                 # "error:"
    r"|warning\s*[:：]"                 # "warning:"
    r"|\(\d+\)\s*:"                     # "(LINE) :" LP_XMLConverter row
    r"|line\s+\d+"                      # "line 42"
    r"|编译失败|compilation\s+error"
    r"|exit\s+code\s+\d+"
    r")",
    re.IGNORECASE,
)

_ARCHICAD_ERROR_PATTERN = re.compile(
    r"(error|warning)\s+in\s+\w[\w\s]*script[,\s]+line\s+\d+",
    re.IGNORECASE,
)


def _is_pure_chat(text: str) -> bool:
    return any(re.search(p, text.strip(), re.IGNORECASE) for p in _CHAT_ONLY_PATTERNS)


def _is_gdl_intent(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _GDL_KEYWORDS)


def _is_gdl_knowledge_question(text: str) -> bool:
    """True for GDL teaching/wiki questions that must stay in CHAT."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    up = raw.upper()
    has_gdl_topic = (
        _is_gdl_intent(raw)
        or bool(_COMMAND_NAME_RE.search(up))
        or any(cmd in up for cmd in ["CYLIND", "PRISM_", "BLOCK", "PROJECT2", "RECT2", "POLY2"])
    )
    if not has_gdl_topic:
        return False

    has_question_signal = (
        _QUESTION_PREFIX_RE.search(raw) is not None
        or any(re.search(pattern, raw, re.IGNORECASE) for pattern in _KNOWLEDGE_QUESTION_PATTERNS)
        or raw.endswith(("?", "？"))
    )
    if not has_question_signal:
        return False

    # Imperative edit/check requests should still go to MODIFY/DEBUG.
    if not _QUESTION_PREFIX_RE.search(raw):
        if re.search(r"(帮我|请|给我|直接|现在|把|将).*(修改|添加|删除|修复|编译|生成|创建|写入|替换|调整|更新|检查)", low):
            return False
        if re.search(r"^(修改|添加|删除|修复|编译|生成|创建|写入|替换|调整|更新|检查)", low):
            return False

    return True


def _is_debug_intent(text: str) -> bool:
    """True if text looks like a debug/error-analysis request.

    Three tiers:
    1. Explicit editor prefix or Archicad error pattern → always DEBUG.
    2. Strong debug keyword (bug/crash/报错/…) → always DEBUG.
    3. Weak debug keyword (error/fix/wrong/…) + explicit error log signature → DEBUG.
       Without an error log signature, these weak words alone fall through to MODIFY,
       avoiding false DEBUG routing for messages like "帮我fix宽度" or "看看这段脚本".
    """
    if text.startswith("[DEBUG:editor]"):
        return True
    if _ARCHICAD_ERROR_PATTERN.search(text):
        return True
    t = text.lower()
    if any(kw in t for kw in _STRONG_DEBUG_KEYWORDS):
        return True
    if any(kw in t for kw in _WEAK_DEBUG_KEYWORDS):
        return _ERROR_LOG_SIGNATURE_RE.search(text) is not None
    return False


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

        # GDL/wiki teaching questions may include code samples or command names.
        # They are explanatory chat, not source changes, even when a project is loaded.
        if _is_gdl_knowledge_question(user_input):
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
