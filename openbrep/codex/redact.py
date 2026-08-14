"""错误文本脱敏（D1 P0-R1）：API 边界统一擦除秘密形态。

上游错误原文可能携带 authUrl / loginId / token / auth 路径，且值不保证
是 URL/JWT/sk-*/UUID 等固定形态（如 access_token=SUPERSECRET、
loginId=opaque-secret、Bearer plain-secret-value）。因此除值形态擦除外，
还必须对「敏感字段名 + 值」整体替换，覆盖 JSON、key=value、key: value 与
Bearer 四种写法。settings_service 的 API 响应本身已改为稳定产品文案，
本模块是最后一道纵深防御。
"""

from __future__ import annotations

import re

# 敏感字段名（大小写不敏感）。authorization 也包含，防 Authorization 头泄漏。
_SENSITIVE_KEY_NAMES = (
    r"authurl|loginid|access[_-]?token|account[_-]?id|"
    r"chatgpt[_-]?account[_-]?id|authorization|api[_-]?key|secret"
)

# 值形态（先擦）：整条 URL / JWT / sk-* / UUID / auth 路径
_URL_RE = re.compile(r"https?://\S+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_PATH_RE = re.compile(r"(?i)([A-Za-z0-9_./~-]*auth\.json|\.codex)")

# key=value（引号 key 可选，值支持引号或裸 token）
_KV_ASSIGN_RE = re.compile(
    rf'(?i)(["\']?)(?:{_SENSITIVE_KEY_NAMES})\1\s*=\s*(?:["\']([^"\']*)["\']|[^\s,;}}\]]+)'
)
# key: value —— 值吞到行尾（宁可多擦不漏）；覆盖 JSON "key":"value" 与
# "Authorization: Bearer plain-secret-value" 等冒号写法
_KV_COLON_RE = re.compile(
    rf'(?i)(["\']?)(?:{_SENSITIVE_KEY_NAMES})\1\s*:\s*[^\n]+'
)
# 裸 Bearer token（无 Authorization 前缀时）
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")
# 兜底：只剩裸字段名（无值可擦）时也替换
_BARE_KEY_RE = re.compile(rf"(?i)\b(?:{_SENSITIVE_KEY_NAMES})\b")


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
    value = _KV_ASSIGN_RE.sub("<redacted>", value)
    value = _KV_COLON_RE.sub("<redacted>", value)
    value = _BEARER_RE.sub("<bearer>", value)
    value = _BARE_KEY_RE.sub("<key>", value)
    return value
