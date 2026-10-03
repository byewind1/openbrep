"""模型发现服务（卡08）：可注入 transport 的独立网络模块。

冻结评审 §11 合同 5 与 §5 边界：
- 发现失败**不得判定服务商不可用**——404 只表示"发现不可用或路径待检查"，
  手输模型接入始终可用；
- 端点拼接规则统一在 ``build_models_url``（带/不带 /v1、代理前缀、防重复追加）；
- ``anthropic_messages`` 走 ``GET {api}/v1/models`` 并处理分页
  （has_more/last_id → after_id，上限 5 页）；OpenAI 兼容（chat_completions /
  responses）一次性 ``GET {api}/models``；
- 去重、总条数上限 500（超出 truncated=true）、总超时 ≤10s；
- 请求体 ``{name}``（从配置读，${ENV} 只读插值）或内联草稿
  ``{api, api_mode, api_key}``（key 用完即弃：不落盘、不进日志、不写配置）；
- 结果不写配置、不进 catalog（运行时判定仍走 model_catalog fail-closed 链）。

无锁合同：本模块只读 config / 只操作草稿副本，绝不调用
``CredentialPool.select()``、绝不触碰 session/project 状态——因此路由可以
加入 LOCK_FREE 例外（与 /api/settings/llm/test 同一合同）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from openbrep.config import API_MODE_CODEX_APP_SERVER, SUPPORTED_API_MODES, expand_env_ref
from openbrep.workbench.llm_diagnostics import classify_llm_error, redact_secrets

MAX_ITEMS = 500
MAX_PAGES = 5
TOTAL_TIMEOUT_SECONDS = 10.0

# transport 合同：method/url/headers/timeout → (status, body_text)；
# 网络层失败抛异常（URLError/TimeoutError/OSError…）。
Transport = Callable[[str, str, dict[str, str], float], tuple[int, str]]


def build_models_url(api: str, api_mode: str) -> str:
    """端点拼接规则（评审 §5 冻结；表驱动测试锁定）。

    - ``chat_completions`` / ``responses``：``{api}/models``；已以 ``/models``
      结尾不重复追加。
    - ``anthropic_messages``：``{api}/v1/models``；api 已带 ``/v1`` 只补
      ``/models``；已以 ``/models`` 结尾原样返回。
    - 代理前缀（如 ``https://gw.example/openai``）原样保留在路径中。
    """
    base = str(api or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/models"):
        return base
    if str(api_mode or "").strip() == "anthropic_messages":
        if base.endswith("/v1"):
            return base + "/models"
        return base + "/v1/models"
    return base + "/models"


def _default_transport(method: str, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 —— 读失败按空 body 分类
            body = ""
        return int(exc.code), body


def _extract_models(payload: Any) -> tuple[list[str], bool]:
    """从一次响应提取 (model_ids, is_paginated)。协议形状不匹配 → protocol 错误。"""
    if not isinstance(payload, dict):
        raise ValueError("响应不是 JSON 对象")
    data = payload.get("data")
    if data is None and "models" in payload:
        data = payload["models"]
    if not isinstance(data, list):
        raise ValueError("响应缺少 data/models 数组")
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id:
            ids.append(model_id)
    paginated = bool(payload.get("has_more"))
    return ids, paginated


def discover(
    url: str,
    headers: dict[str, str],
    *,
    transport: Transport | None = None,
    timeout: float = TOTAL_TIMEOUT_SECONDS,
    max_items: int = MAX_ITEMS,
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    """拉取 /models 列表：去重保序、总量上限、分页与总超时预算。

    成功：``{ok, models, raw_count, truncated, page_count}``。
    失败：``{ok: False, category, message, fix_hint}``（message/fix_hint 已按
    传入 headers 中的凭据脱敏）。
    """
    if not url:
        return {"ok": False, "category": "protocol", "message": "端点为空。", "fix_hint": "请先填写 API 端点。"}
    transport = transport or _default_transport
    # 脱敏候选：headers 里的凭据原值（Bearer 前缀剥掉），异常/响应体回显时替换
    secrets = [
        value.removeprefix("Bearer ").strip() for value in headers.values() if value and value.strip()
    ]

    models: list[str] = []
    seen: set[str] = set()
    raw_count = 0
    page_count = 0
    truncated = False
    current_url: str | None = url
    deadline = time.monotonic() + timeout

    try:
        while current_url is not None and page_count < max_pages:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                truncated = True
                break
            status, body = transport("GET", current_url, headers, min(remaining, timeout))
            page_count += 1
            if status >= 400:
                detail = classify_llm_error(None, body, status=status)
                return {
                    "ok": False,
                    "category": detail["category"],
                    "message": redact_secrets(detail["message"], secrets),
                    "fix_hint": redact_secrets(detail["fix_hint"], secrets),
                }
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                detail = classify_llm_error(exc)
                return {
                    "ok": False,
                    "category": detail["category"],
                    "message": redact_secrets(detail["message"], secrets),
                    "fix_hint": redact_secrets(detail["fix_hint"], secrets),
                }
            page_ids, paginated = _extract_models(payload)
            for model_id in page_ids:
                raw_count += 1
                if len(models) >= max_items:
                    truncated = True
                    break
                key = model_id.lower()
                if key not in seen:
                    seen.add(key)
                    models.append(model_id)
            if truncated or not paginated:
                break
            last_id = str(payload.get("last_id") or "").strip()
            current_url = _page_url(url, last_id) if last_id else None

        return {
            "ok": True,
            "models": models,
            "raw_count": raw_count,
            "truncated": truncated,
            "page_count": page_count,
        }
    except Exception as exc:  # noqa: BLE001 —— 网络层异常统一分类脱敏
        detail = classify_llm_error(exc)
        return {
            "ok": False,
            "category": detail["category"],
            "message": redact_secrets(str(exc) or exc.__class__.__name__, secrets),
            "fix_hint": redact_secrets(detail["fix_hint"], secrets),
        }


def _page_url(url: str, after_id: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}after_id={urllib.parse.quote(after_id)}"


def _normalize_query_api_mode(value: Any) -> str | None:
    """api_mode 归一化（沿用 _normalize_api_mode 报错语义，codex 不参与发现）。"""
    from openbrep.config import _normalize_api_mode

    try:
        mode = _normalize_api_mode(value)
    except ValueError:
        return None
    if mode == API_MODE_CODEX_APP_SERVER:
        return None
    if mode not in SUPPORTED_API_MODES:
        return None
    return mode


def _draft_headers(api_mode: str, api_key: str) -> dict[str, str]:
    if api_mode == "anthropic_messages":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if api_key:
            return headers
        return {key: value for key, value in headers.items() if key != "x-api-key"}
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def resolve_entry_credential(entry: dict) -> str:
    """条目凭据的只读解析（发现用）：池 → from_provider 只读取首条（绝不 select）；
    否则 api_key（${ENV} 只读插值）。"""
    from openbrep.credential_pool import CredentialPool

    if entry.get("credentials") or entry.get("api_keys"):
        pool = CredentialPool.from_provider(entry)
        if pool.credentials:
            return pool.credentials[0].value
        return ""
    return expand_env_ref(entry.get("api_key"))


def discover_provider_models(config: Any, body: dict[str, Any]) -> dict[str, Any]:
    """路由入口：``{name}``（配置条目）或内联草稿 ``{api, api_mode, api_key}``。

    草稿路径的 key 只在本调用内存中使用，用完即弃；两种路径都不写配置。
    """
    name = str(body.get("name") or "").strip()
    if name:
        entry = next(
            (
                item
                for item in config.llm.providers
                if str(item.get("name", "") or "").strip().lower() == name.lower()
            ),
            None,
        )
        if entry is None:
            return {
                "ok": False,
                "category": "not_found",
                "message": f"配置中不存在服务商 {name}。",
                "fix_hint": "请先保存服务商，或改用草稿参数直接发现。",
            }
        api = str(entry.get("api") or entry.get("base_url") or "").strip()
        api_mode = str(entry.get("api_mode") or "chat_completions")
        api_key = resolve_entry_credential(entry)
        display_name = str(entry.get("name") or "").strip()
    else:
        api = str(body.get("api") or "").strip()
        api_mode = str(body.get("api_mode") or "chat_completions")
        api_key = str(body.get("api_key") or "")
        display_name = "draft"

    normalized_mode = _normalize_query_api_mode(api_mode)
    if normalized_mode is None:
        return {
            "ok": False,
            "category": "protocol",
            "message": f"api_mode {api_mode!r} 不支持模型发现。",
            "fix_hint": "支持的 api_mode：chat_completions / responses / anthropic_messages。",
        }
    url = build_models_url(api, normalized_mode)
    result = discover(url, _draft_headers(normalized_mode, api_key))
    if result.get("ok"):
        result["provider"] = display_name
    return result
