"""LLM 错误结构化分类（卡08）：纯函数，无网络、无状态。

``classify_llm_error`` 把异常或 HTTP 状态/响应体归类为七类之一，并给出
"可能原因"式的中文修复提示——不断定根因（评审 §5：401/404 等提示应提供
可能原因，而非一概断定）。``redact_secrets`` 供调用方在输出前脱敏。
"""

from __future__ import annotations

from typing import Any

CATEGORIES = ("auth", "not_found", "timeout", "network", "protocol", "rate_limited", "unknown")

_FIX_HINTS: dict[str, str] = {
    "auth": "API Key 可能无效、过期，或没有访问该资源的权限；也可能 Key 未随请求发送。",
    "not_found": (
        "发现接口不可用或路径待检查（尝试在 api 末尾补 /v1）。"
        "这只代表模型发现不可用，不代表服务商不可用——仍可手输模型完成接入。"
    ),
    "timeout": "请求超时：端点可能不可达或网络拥塞，可稍后重试或检查代理设置。",
    "network": "网络连接失败：DNS 解析、代理或防火墙可能阻断了请求。",
    "protocol": "响应不是预期的模型列表/补全格式：api_mode 与端点协议可能不匹配。",
    "rate_limited": "触发限流：请稍后重试，或降低请求频率。",
    "unknown": "发生未知错误；可展开技术详情查看完整异常链。",
}


def classify_llm_error(
    exc: BaseException | None = None,
    http_body: str | None = None,
    *,
    status: int | None = None,
) -> dict[str, Any]:
    """异常/HTTP 状态 → ``{category, message, fix_hint}`` 纯分类。

    - ``status`` 提供时按状态码 + 响应体关键词分类（发现/HTTP 路径）；
    - 否则按异常类型与消息分类（连接测试路径，卡10 复用）。
    ``message`` 摘要不含任何 secret（调用方仍应先经 ``redact_secrets``）。
    """
    if status is not None:
        return _classify_status(status, http_body)
    if exc is not None:
        return _classify_exception(exc, http_body)
    return _result("unknown", "未知错误。")


def classify_http_status(status: int, http_body: str | None = None) -> dict[str, Any]:
    """HTTP 状态码分类（discover 的传输层结果走这里）。"""
    return _classify_status(status, http_body)


def redact_secrets(text: str, secrets: list[str] | tuple[str, ...]) -> str:
    """把已知 secret 从文本中替换为 ***（异常/响应体可能回显请求内容）。"""
    result = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in result:
            result = result.replace(secret, "***")
    return result


def _result(category: str, message: str) -> dict[str, Any]:
    return {"category": category, "message": message, "fix_hint": _FIX_HINTS[category]}


def _classify_status(status: int, http_body: str | None) -> dict[str, Any]:
    body = (http_body or "").lower()
    if status in (401, 403) or "invalid_api_key" in body or "unauthorized" in body or "invalid x-api-key" in body:
        return _result("auth", f"HTTP {status}：认证被拒绝。")
    if status == 404:
        return _result("not_found", "HTTP 404：端点路径不存在。")
    if status == 429:
        return _result("rate_limited", "HTTP 429：请求过于频繁。")
    if 400 <= status < 500:
        return _result("protocol", f"HTTP {status}：请求被端点拒绝（协议/参数可能不匹配）。")
    if status >= 500:
        return _result("unknown", f"HTTP {status}：服务端错误。")
    return _result("unknown", f"HTTP {status}：非预期状态。")


def _classify_exception(exc: BaseException, http_body: str | None) -> dict[str, Any]:
    import json

    message = str(exc) or exc.__class__.__name__
    lowered = message.lower() + " " + (http_body or "").lower()

    # HTTP 状态挂在异常上（litellm/HTTPError 风格）：优先按状态分类
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        body_text = http_body
        if body_text is None:
            try:
                body_text = getattr(response, "text", None)
            except Exception:  # noqa: BLE001 —— response.text 读取失败按 None 处理
                body_text = None
        return _classify_status(status, body_text)

    import socket

    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in lowered or "timeout" in lowered:
        return _result("timeout", message)
    if isinstance(exc, (ConnectionError, OSError)) or "connection" in lowered or "name or service not known" in lowered or "getaddrinfo" in lowered:
        return _result("network", message)
    if "401" in lowered or "403" in lowered or "unauthorized" in lowered or "invalid_api_key" in lowered or "api key" in lowered:
        return _result("auth", message)
    if "429" in lowered or "rate limit" in lowered:
        return _result("rate_limited", message)
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError, TypeError)):
        return _result("protocol", message)
    return _result("unknown", message)
