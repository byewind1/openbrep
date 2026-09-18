"""[FILE: path] 交付协议的共享识别器。

只有冒号后跟着非空路径的 ``[FILE: path]`` 才算交付块；空标记 ``[FILE:]``（例如在
解释文本里提及协议本身）不算，不能因此报协议错误（ST04 K09）。

codex 桥接（``modify_codex_bridge._has_file_blocks``）与 skill 提炼校验
（``skill_harvest._validate_proposal``）共用同一 matcher，避免各自的字符串包含
判断漂移。
"""

from __future__ import annotations

import re

# 冒号后必须是空白之外的真实路径字符，直到右方括号。
FILE_BLOCK_RE = re.compile(r"\[FILE:[ \t]*[^\]\s][^\]]*\]")


def has_file_blocks(text: str) -> bool:
    """True 仅当文本里存在真实 ``[FILE: path]`` 结构。"""
    return FILE_BLOCK_RE.search(text or "") is not None
