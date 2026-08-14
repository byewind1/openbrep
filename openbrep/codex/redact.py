"""错误文本脱敏（D1 P0-2）：API 边界统一擦除秘密形态。

app-server 的上游错误原文可能携带 authUrl / loginId / token / auth 路径，
settings_service 把这些异常文本返回前端前必须经过本模块清洗。
"""

from __future__ import annotations

import re

# 顺序敏感：先整条 URL（可能内嵌 query/token），再 JWT / key / UUID / 路径。
# URL 用 \S+ 保守吞到空白为止：宁可多擦（整段被 <url> 替换）也不漏。
_URL_RE = re.compile(r"https?://\S+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_PATH_RE = re.compile(r"(?i)([A-Za-z0-9_./~-]*auth\.json|\.codex)")
# 敏感字段名本身也擦除（错误原文里的 "authUrl":"<url>" 仍会暴露字段形态）
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)\b(authurl|loginid|access[_ -]?token|account[_ -]?id|chatgpt[_ -]?account[_ -]?id|authorization)\b"
)


def redact_secrets(text) -> str:
    """把文本中的秘密形态替换为占位符；无匹配时原样返回。"""
    if not text:
        return str(text or "")
    value = str(text)
    value = _URL_RE.sub("<url>", value)
    value = _JWT_RE.sub("<jwt>", value)
    value = _KEY_RE.sub("<key>", value)
    value = _UUID_RE.sub("<id>", value)
    value = _PATH_RE.sub("<path>", value)
    value = _SENSITIVE_KEY_RE.sub("<key>", value)
    return value
