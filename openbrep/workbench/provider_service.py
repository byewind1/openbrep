"""Provider 设置工作流（卡01 只读总览 + 卡03 创建/更新写路径；发现/导入按后续卡分批进入）。

边界（派单总原则 3）：本模块只承载 provider 配置工作流；网络发现是独立的
可注入 transport 模块（卡08 ``model_discovery``），诊断是纯函数模块（卡08
``llm_diagnostics``），引用检查在 ``provider_refs``——避免本 service 长成
配置+网络+诊断大杂烩。

只读投影合同（评审 §4）：列表构造**绝不调用 ``resolve_credentials()`` /
``CredentialPool.select()``**——不创建租约、不改池状态；凭据状态来自
``credential_status`` 纯读函数。

写路径合同（卡00/卡03）：全部写入经 ``commit_config_change`` 原子提交
（expected_revision 冲突检测 + 副本验证 + 原子替换 + 再发布）；key 三态
（缺省=保持 / 显式空=清除 / 非空=替换）与 api 显式空语义由卡00 原语解释；
名称大小写规则与解析器一致；第一版禁止重命名；openai-codex 保留条目
POST/PUT 一律拒绝。
"""

from __future__ import annotations

import re
from typing import Any

from openbrep.config import (
    CODEX_PROVIDER_NAME,
    PROVIDER_PROFILES,
    _normalize_api_mode,
    iter_custom_provider_model_entries,
    normalize_provider_entry,
)
from openbrep.workbench.config_commit import (
    ConfigCommitError,
    apply_endpoint_update,
    commit_config_change,
    file_revision,
    resolve_api_key_update,
)
from openbrep.workbench.credential_status import (
    credential_status,
    has_credential_pool,
    mask_secret,
    pool_entries,
)
from openbrep.workbench.model_discovery import discover_provider_models
from openbrep.workbench.provider_refs import find_provider_refs, frontend_visibility_hint

_NAME_FORBIDDEN_RE = re.compile(r"[\s/]")
# 请求对象允许进入新建条目的透传字段（与 provider_entry_to_toml 的持久化键一致）
_PASSTHROUGH_FIELDS = ("temperature", "extra_body", "credentials", "api_keys", "native_prefix")


def provider_key_display(entry: dict) -> str:
    """key_display：无 → ""；明文 → 掩码；${ENV} → 原样；池 → 池×N（不含 secret）。"""
    if has_credential_pool(entry):
        return f"池×{len(pool_entries(entry))}"
    return mask_secret(entry.get("api_key"))


def provider_available(config: Any, name: str, codex_available: bool | None = None) -> bool:
    """provider 是否有立即可调用的模型（卡11）：复用 llm_model_available 的
    fail-closed 语义——codex 订阅模型必须已登录且在目录中；ollama 免 key；
    其余需要可解析凭据。条目无模型时按 default_model 判定。

    池条目例外：resolve_credentials/llm_model_available 会 select 池（创建租约、
    推进轮转指针），只读展示绝不允许——池条目改用 credential_status 的只读
    peek（from_provider 同款展开，绝不 select），零副作用语义不变。
    """
    from openbrep.workbench.credential_status import credential_status
    from openbrep.workbench.settings_service import llm_model_available

    entry = locate_provider_entry(list(config.llm.providers), name)
    if entry is None:
        return False
    if has_credential_pool(entry):
        return bool(credential_status(entry, config)["resolvable"])
    candidates: list[str] = []
    default_model = str(entry.get("default_model") or "").strip()
    if default_model:
        candidates.append(f"{name}/{default_model}")
    for model_entry in iter_custom_provider_model_entries(entry):
        candidates.append(f"{name}/{model_entry['model']}")
        if len(candidates) >= 2:
            break
    if not candidates:
        candidates.append(f"{name}/{name}")
    return any(
        llm_model_available(config, candidate, codex_available=codex_available)
        for candidate in candidates[:2]
    )


def provider_info(entry: dict, config, codex_available: bool | None = None) -> dict[str, Any]:
    """单个 provider 条目的只读投影（ProviderInfo，§4 契约定型）。"""
    normalized = normalize_provider_entry(entry)
    name = str(normalized.get("name", "") or "")
    models: list[str] = []
    model_entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for model_entry in iter_custom_provider_model_entries(normalized):
        alias = model_entry["alias"]
        model_entries.append({"alias": alias, "model": model_entry["model"]})
        if alias not in seen:
            seen.add(alias)
            models.append(alias)
    return {
        "name": name,
        "api": str(normalized.get("api", "") or ""),
        "api_mode": str(normalized.get("api_mode", "") or ""),
        "default_model": str(normalized.get("default_model", "") or ""),
        "models": models,
        # 卡06：编辑表单需要 alias/model 对才能无损往返（models 只含 alias）
        "model_entries": model_entries,
        "model_count": len(models),
        "has_api_key": bool(credential_status(entry, config)["resolvable"]),
        "key_display": provider_key_display(entry),
        "is_codex": name == CODEX_PROVIDER_NAME,
        "credential": credential_status(entry, config),
        # 卡11：configured/available 状态（fail-closed，复用 llm_model_available）
        "available": provider_available(config, name, codex_available=codex_available),
    }


def reserved_provider_names() -> set[str]:
    """不可被新条目占用的保留名：openai-codex 与官方 profile 名（小写）。"""
    names = {profile.name.lower() for profile in PROVIDER_PROFILES}
    names.add(CODEX_PROVIDER_NAME.lower())
    return names


def locate_provider_entry(providers: list[dict], name: str) -> dict | None:
    """按解析器同口径（大小写不敏感）定位条目；无则 None。"""
    target = str(name or "").strip().lower()
    for entry in providers:
        if str(entry.get("name", "") or "").strip().lower() == target:
            return entry
    return None


def normalize_models_request(raw: Any) -> list[dict[str, str]]:
    """models 请求值 → {alias, model} 对列表；字符串与对象两可，去重保序。

    去重键为 (alias, model) 小写对——与解析器的小写匹配一致，避免同义
    大小写变体造成的选择歧义。
    """
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    items = raw if isinstance(raw, list) else []
    for item in items:
        if isinstance(item, dict):
            alias = str(item.get("alias") or item.get("name") or item.get("model") or "").strip()
            model = str(item.get("model") or item.get("alias") or "").strip()
        else:
            value = str(item or "").strip()
            alias, model = value, value
        if not alias and not model:
            continue
        key = (alias.lower(), model.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append({"alias": alias or model, "model": model or alias})
    return entries


class ProviderSettingsService:
    """provider 设置工作流：只读总览（卡01）+ 创建/更新（卡03）。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    def route(
        self,
        method: str,
        route: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        body = body or {}
        if method == "GET" and route == "/api/settings/llm/providers":
            return self.list_providers()
        if method == "POST" and route == "/api/settings/llm/providers":
            return self.create_provider(body)
        if method in ("PUT", "PATCH") and route == "/api/settings/llm/providers":
            return self.update_provider(body)
        if method == "POST" and route == "/api/settings/llm/providers/delete":
            return self.delete_provider(body)
        if method == "POST" and route == "/api/settings/llm/providers/discover-models":
            # 卡08：无锁路由（request_gate 例外）——只读配置/只操作草稿副本，
            # 绝不 select 凭据池、不触碰 session/project 状态。
            return discover_provider_models(self.session.config, body)
        return {"ok": False, "error": f"Unknown route: {method} {route}"}

    # ── 只读总览（卡01）────────────────────────────────────────

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

    # ── 写路径（卡03）──────────────────────────────────────────

    def create_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/settings/llm/providers：新建条目（expected_revision 必填）。"""
        provider_req = body.get("provider")
        if not isinstance(provider_req, dict):
            return {"ok": False, "code": "invalid_request", "error": "provider 对象必填。"}
        guard = self._require_revision(body)
        if guard is not None:
            return guard
        name = str(provider_req.get("name") or "").strip()
        name_error = self._validate_new_name(self.session.config, name)
        if name_error is not None:
            return name_error

        def mutate(working: Any) -> None:
            # 锁内重验重名：revision 检测覆盖外部文件变更，但同进程内
            # 其他路由的并发写入也必须在 mutator 里再次拦截。
            conflict = self._validate_new_name(working, name)
            if conflict is not None:
                raise ConfigCommitError(conflict["code"], conflict["error"])
            working.llm.providers.append(self._build_entry_from_request(name, provider_req))

        return self._commit(mutate, expected_revision=str(body.get("expected_revision")), name=name)

    def update_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/settings/llm/providers：按 body.name 定位并更新（禁止改名）。"""
        provider_req = body.get("provider")
        name = str(body.get("name") or "").strip()
        if not isinstance(provider_req, dict):
            return {"ok": False, "code": "invalid_request", "error": "provider 对象必填。"}
        if not name:
            return {"ok": False, "code": "invalid_request", "error": "name 必填（定位既有条目）。"}
        guard = self._require_revision(body)
        if guard is not None:
            return guard
        if name.lower() == CODEX_PROVIDER_NAME.lower():
            return {
                "ok": False,
                "code": "codex_entry_protected",
                "error": "openai-codex 是保留订阅身份，不能通过 provider 设置修改。",
            }
        requested_name = str(provider_req.get("name") or "").strip()
        if requested_name and requested_name.lower() != name.lower():
            return {
                "ok": False,
                "code": "rename_not_supported",
                "error": "第一版不支持重命名：服务商名称创建后只读。",
            }

        def mutate(working: Any) -> None:
            entry = locate_provider_entry(working.llm.providers, name)
            if entry is None:
                raise ConfigCommitError("not_found", f"服务商 {name} 不存在。")
            self._apply_update_to_entry(entry, provider_req)

        return self._commit(mutate, expected_revision=str(body.get("expected_revision")), name=name)

    def delete_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/settings/llm/providers/delete：引用命中即拒绝并列出全部引用点。

        引用检查（卡02 ``find_provider_refs``）在本路由内执行——POST 写路由由
        会话锁串行化，检查与提交之间不会被其他会话变更插入；外部文件修改由
        expected_revision 拦截。前端可见性引用（localStorage，后端不可见）按
        卡02 语义作非阻塞提示项并入 refs 报告，不阻塞删除。
        """
        name = str(body.get("name") or "").strip()
        if not name:
            return {"ok": False, "code": "invalid_request", "error": "name 必填。"}
        guard = self._require_revision(body)
        if guard is not None:
            return guard
        if name.lower() == CODEX_PROVIDER_NAME.lower():
            return {
                "ok": False,
                "code": "codex_entry_protected",
                "error": "openai-codex 是保留订阅身份，不能删除。",
            }
        if locate_provider_entry(list(self.session.config.llm.providers), name) is None:
            return {"ok": False, "code": "not_found", "error": f"服务商 {name} 不存在。"}

        refs = find_provider_refs(self.session.config, name, session=self.session)
        if refs:
            return {
                "ok": False,
                "code": "in_use",
                "error": f"服务商 {name} 仍被引用，请先处理以下引用再删除。",
                "refs": [*refs, frontend_visibility_hint(name)],
            }

        def mutate(working: Any) -> None:
            entry = locate_provider_entry(working.llm.providers, name)
            if entry is None:
                raise ConfigCommitError("not_found", f"服务商 {name} 不存在。")
            if str(entry.get("name", "") or "").strip().lower() == CODEX_PROVIDER_NAME.lower():
                raise ConfigCommitError(
                    "codex_entry_protected", "openai-codex 是保留订阅身份，不能删除。"
                )
            working.llm.providers.remove(entry)

        result = commit_config_change(
            self.session.config,
            self.session.config_path,
            expected_revision=str(body.get("expected_revision")),
            mutate=mutate,
            on_committed=self._publish_config,
        )
        if not result.get("ok"):
            return result
        config = self.session.config
        return {
            "ok": True,
            "deleted": name,
            "providers": [provider_info(item, config) for item in config.llm.providers],
            "revision": result["revision"],
        }

    # ── 内部：校验、条目构造与提交 ─────────────────────────────

    @staticmethod
    def _require_revision(body: dict[str, Any]) -> dict[str, Any] | None:
        if body.get("expected_revision") is None:
            return {
                "ok": False,
                "code": "invalid_request",
                "error": "expected_revision 必填（先 GET /api/settings/llm/providers 获取）。",
            }
        return None

    @staticmethod
    def _validate_new_name(config: Any, name: str) -> dict[str, Any] | None:
        """新名称校验：非空、无 / 与空白、不占用保留名、不与既有条目重名
        （大小写规则与解析器一致——小写比较）。"""
        if not name:
            return {"ok": False, "code": "invalid_name", "error": "服务商名称必填。"}
        if _NAME_FORBIDDEN_RE.search(name):
            return {
                "ok": False,
                "code": "invalid_name",
                "error": "服务商名称不能包含空白或 /（名称参与 provider/model 身份解析）。",
            }
        if name.lower() == CODEX_PROVIDER_NAME.lower():
            return {
                "ok": False,
                "code": "codex_entry_protected",
                "error": "openai-codex 是保留订阅身份，不能创建同名服务商。",
            }
        if name.lower() in reserved_provider_names():
            return {
                "ok": False,
                "code": "reserved_name",
                "error": f"名称 {name} 是内置服务商保留名，不能使用。",
            }
        if locate_provider_entry(list(config.llm.providers), name) is not None:
            return {
                "ok": False,
                "code": "name_conflict",
                "error": f"服务商 {name} 已存在（名称比较不区分大小写）。",
            }
        return None

    @staticmethod
    def _build_entry_from_request(name: str, provider_req: dict[str, Any]) -> dict[str, Any]:
        """请求对象 → 新条目。字段覆盖合同：只写入明确提交的字段。

        - api：缺省 = 不写 api 键（允许顶层 api_base 兜底）；显式值（含空串）
          经 apply_endpoint_update 标记 _explicit_base。
        - api_key：缺省 = 空（新条目无既有值）；显式空 = 清除；非空 = 设置。
        """
        entry: dict[str, Any] = {"name": name}
        apply_endpoint_update(entry, provider_req)
        if "api_mode" in provider_req:
            entry["api_mode"] = _normalize_api_mode(provider_req.get("api_mode"))
        entry["api_key"] = resolve_api_key_update("", provider_req)
        if "default_model" in provider_req:
            default_model = str(provider_req.get("default_model") or "").strip()
            if default_model:
                entry["default_model"] = default_model
        entry["models"] = normalize_models_request(provider_req.get("models", []))
        for field in _PASSTHROUGH_FIELDS:
            if field in provider_req:
                entry[field] = provider_req[field]
        return entry

    @staticmethod
    def _apply_update_to_entry(entry: dict[str, Any], provider_req: dict[str, Any]) -> None:
        """请求对象 → 既有条目的字段覆盖。缺省字段原样保留（含 temperature /
        extra_body / credentials 等高级字段）；key 三态与 api 显式空按卡00 合同。"""
        entry["api_key"] = resolve_api_key_update(entry.get("api_key"), provider_req)
        apply_endpoint_update(entry, provider_req)
        if "api_mode" in provider_req:
            entry["api_mode"] = _normalize_api_mode(provider_req.get("api_mode"))
        if "default_model" in provider_req:
            default_model = str(provider_req.get("default_model") or "").strip()
            if default_model:
                entry["default_model"] = default_model
            else:
                entry.pop("default_model", None)
        if "models" in provider_req:
            entry["models"] = normalize_models_request(provider_req.get("models"))
        for field in _PASSTHROUGH_FIELDS:
            if field in provider_req:
                entry[field] = provider_req[field]

    def _commit(self, mutate: Any, *, expected_revision: str, name: str) -> dict[str, Any]:
        """经卡00 原语原子提交，成功后发布运行时状态并返回新投影。"""
        result = commit_config_change(
            self.session.config,
            self.session.config_path,
            expected_revision=expected_revision,
            mutate=mutate,
            on_committed=self._publish_config,
        )
        if not result.get("ok"):
            return result
        config = self.session.config
        entry = locate_provider_entry(list(config.llm.providers), name)
        return {
            "ok": True,
            "provider": provider_info(entry, config) if entry is not None else None,
            "providers": [provider_info(item, config) for item in config.llm.providers],
            "revision": result["revision"],
        }

    def _publish_config(self, committed: Any) -> None:
        """写盘成功后发布：替换 session.config 并重解析会话模型凭据。

        与 reload_runtime_settings 的 llm 部分同口径：会话模型覆盖优先，
        凭据按生效模型重新解析（顶层 api_key/api_base 不回写）。
        """
        from openbrep.workbench.settings_service import session_llm_model_override

        self.session.config = committed
        self.session.llm_model = session_llm_model_override(self.session) or committed.llm.model
        self.session.llm_api_key = committed.llm.resolve_api_key(self.session.llm_model) or ""
        self.session.llm_api_base = committed.llm.resolve_api_base(self.session.llm_model) or ""
