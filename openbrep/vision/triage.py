"""
triage — Vision Harness S1 本地分型（P5b，设计 §10-D1：默认零额外 LLM 调用）

角色推导（每图一个 role，写回 ImageRef.role）：
    用户文本显式引用（"图1的轮廓/纹样/材质"） > 位置启发（第 1 张默认 outline，其余 auto）

schema 选择（每任务一个 schema）：
    用户显式指定（trigger.keywords 命中） > generic 兜底

设计 D1 里的"合并式 triage LLM 调用"本单不做（多图 + 无文本引用 + 关键词未命中
时仍走位置启发 + generic，零额外调用）。
"""

from __future__ import annotations

import re
from typing import Mapping

from openbrep.vision.schema_registry import VisionSchema

# 显式引用 → 角色。同一条文本同时命中多个角色时按列表顺序取第一个（outline 优先）。
_ROLE_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("轮廓", "外形", "造型", "outline"), "outline"),
    (("纹样", "图案", "花色", "pattern"), "pattern"),
    (("材质", "材料", "质感", "material"), "material"),
]

# 图 token 与关键词之间允许的修饰字符（"图1的轮廓" / "图1轮廓" / "图 1 的轮廓" 都命中）
_TOKEN_KW_SEP = r"\s*(?:的|之|里|中|上|内)?\s*"


def derive_role(image_token: str, user_input: str, position: int, total: int) -> str:
    """推导单张图的角色。

    image_token: "图1" / "图2"（与用户文本中的 [图N] 引用对应）
    user_input:  用户指令文本
    position:    1-based 位置（第 1 张默认 outline）
    total:       图片总数
    """
    text = user_input or ""
    for keywords, role in _ROLE_PATTERNS:
        for keyword in keywords:
            pattern = re.compile(
                rf"{re.escape(image_token)}{_TOKEN_KW_SEP}{re.escape(keyword)}",
                re.IGNORECASE,
            )
            if pattern.search(text):
                return role
    if position == 1:
        return "outline"
    return "auto"


def select_schema(
    user_input: str,
    schemas: Mapping[str, VisionSchema],
    default: str = "generic",
) -> str:
    """每任务选择一个提取 schema：用户显式指定（关键词命中）> generic 兜底。

    schemas: registry 的全部 schema（load_all_schemas() 返回，确定性排序）。
    命中顺序 = schemas 迭代顺序（registry 按文件名排序），首个命中即返回。
    """
    text = user_input or ""
    for name, schema in schemas.items():
        if name == default:
            continue
        for keyword in schema.trigger_keywords or []:
            if keyword and keyword in text:
                return name
    return default
