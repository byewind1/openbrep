"""Provider 设置工作流（卡01 只读总览；写路径/发现/导入按后续卡分批进入）。

边界（派单总原则 3）：本模块只承载 provider 配置工作流；网络发现是独立的
可注入 transport 模块（卡08 ``model_discovery``），诊断是纯函数模块（卡08
``llm_diagnostics``）——避免本 service 长成配置+网络+诊断大杂烩。

只读投影合同（评审 §4）：列表构造**绝不调用 ``resolve_credentials()`` /
``CredentialPool.select()``**——不创建租约、不改池状态；凭据状态来自
``credential_status`` 纯读函数。
"""

from __future__ import annotations

from typing import Any

from openbrep.config import (
    CODEX_PROVIDER_NAME,
    iter_custom_provider_model_entries,
    normalize_provider_entry,
)
from openbrep.workbench.config_commit import file_revision
from openbrep.workbench.credential_status import (
    credential_status,
    has_credential_pool,
    mask_secret,
    pool_entries,
)


def provider_key_display(entry: dict) -> str:
    """key_display：无 → ""；明文 → 掩码；${ENV} → 原样；池 → 池×N（不含 secret）。"""
    if has_credential_pool(entry):
        return f"池×{len(pool_entries(entry))}"
    return mask_secret(entry.get("api_key"))


def provider_info(entry: dict, config) -> dict[str, Any]:
    """单个 provider 条目的只读投影（ProviderInfo，§4 契约定型）。"""
    normalized = normalize_provider_entry(entry)
    name = str(normalized.get("name", "") or "")
    models: list[str] = []
    seen: set[str] = set()
    for model_entry in iter_custom_provider_model_entries(normalized):
        alias = model_entry["alias"]
        if alias not in seen:
            seen.add(alias)
            models.append(alias)
    return {
        "name": name,
        "api": str(normalized.get("api", "") or ""),
        "api_mode": str(normalized.get("api_mode", "") or ""),
        "default_model": str(normalized.get("default_model", "") or ""),
        "models": models,
        "model_count": len(models),
        "has_api_key": bool(credential_status(entry, config)["resolvable"]),
        "key_display": provider_key_display(entry),
        "is_codex": name == CODEX_PROVIDER_NAME,
        "credential": credential_status(entry, config),
    }


class ProviderSettingsService:
    """provider 设置工作流。卡01 交付只读部分；写路径自卡03 起进入。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    def route(
        self,
        method: str,
        route: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        if method == "GET" and route == "/api/settings/llm/providers":
            return self.list_providers()
        return {"ok": False, "error": f"Unknown route: {method} {route}"}

    def list_providers(self) -> dict[str, Any]:
        """GET /api/settings/llm/providers：只读投影 + 当前配置指纹。

        无副作用：不 resolve 凭据、不 select 池、不写盘、不改内存。
        """
        config = self.session.config
        providers = [provider_info(entry, config) for entry in config.llm.providers]
        return {
            "ok": True,
            "providers": providers,
            "revision": file_revision(self.session.config_path),
        }
