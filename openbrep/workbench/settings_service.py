from __future__ import annotations

import copy
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from openbrep.config import (
    ALL_MODELS,
    GDLAgentConfig,
    ensure_codex_provider_entry,
    is_codex_qualified_model,
    iter_custom_provider_model_entries,
    model_to_provider,
    provider_templates,
)


def load_workbench_config(config_path: Path) -> GDLAgentConfig:
    if config_path.exists():
        return GDLAgentConfig.load(str(config_path))
    return GDLAgentConfig()


def resolve_workbench_config_path(config_path: str | Path | None = None) -> Path:
    if config_path:
        return Path(config_path)
    env_path = str(os.environ.get("GDL_AGENT_CONFIG") or "").strip()
    if env_path:
        return Path(env_path)
    cwd = Path.cwd()
    try:
        common_dir = subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if common_dir:
            common_path = Path(common_dir)
            if not common_path.is_absolute():
                common_path = cwd / common_path
            if common_path.name == ".git":
                main_config = common_path.parent / "config.toml"
                if main_config.exists():
                    return main_config
    except Exception:
        pass
    return cwd / "config.toml"


def save_workbench_config(config: GDLAgentConfig, config_path: Path) -> None:
    config.save(str(config_path))


def apply_llm_credentials_to_config(
    config: GDLAgentConfig,
    *,
    model: str,
    api_key: str,
    api_base: str,
) -> None:
    custom_match = config.llm._find_custom_provider_match(model)
    if custom_match is not None:
        provider = custom_match.get("provider")
        if isinstance(provider, dict):
            # 空值表示"沿用已保存的配置"，不能覆盖 custom provider 里已有的 key/base，
            # 否则连接测试（不带凭据调用）会把已保存的 key 抹掉。
            if api_key:
                provider["api_key"] = api_key
            if api_base:
                provider["api"] = api_base
                provider["base_url"] = api_base
        config.llm.api_key = api_key or str(custom_match.get("api_key", "") or "")
        config.llm.api_base = api_base or str(custom_match.get("api", "") or custom_match.get("base_url", "") or "")
        return

    provider_name = model_to_provider(model)
    resolved_api_key = api_key
    if provider_name and provider_name != "custom":
        if api_key:
            config.llm.provider_keys[provider_name] = api_key
        else:
            resolved_api_key = str(config.llm.provider_keys.get(provider_name, "") or "")
    config.llm.api_key = resolved_api_key
    config.llm.api_base = api_base


def format_llm_exception_detail(exc: BaseException) -> str:
    """串起完整异常链与服务器响应体原文，供前端全量明文展示排错。

    llm.py 会把 litellm 异常包装成 RuntimeError（摘要），原始响应体挂在
    __cause__ 链上；这里沿链逐层收集 类名+消息+HTTP 响应体，不做截断。
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip() or "(no message)"
        parts.append(f"{current.__class__.__name__}: {message}")
        response = getattr(current, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            try:
                text = getattr(response, "text", None)
            except Exception:
                text = None
            if isinstance(text, str) and text.strip() and text.strip() not in message:
                header = f"HTTP {status} 响应原文：" if status is not None else "HTTP 响应原文："
                parts.append(f"{header}\n{text.strip()}")
        body = getattr(current, "body", None)
        if body and str(body) not in message:
            parts.append(f"body: {body}")
        current = current.__cause__ or current.__context__
    return "\n\n".join(parts)


def llm_model_available(
    config: GDLAgentConfig,
    model: str | None = None,
    *,
    codex_connected: bool | None = None,
) -> bool:
    """判断当前模型是否可立即调用。

    - openai-codex/*（ChatGPT 订阅）：只有已登录（codex_connected=True）才可用，
      绝不 fallback 到 API key / 环境变量；未提供登录态一律不可用（fail closed）。
    - 本地 ollama：无需 key。
    - 其余：需要可解析的 API key。
    """
    target = str(model if model is not None else config.llm.model or "").strip()
    if not target:
        return False
    if is_codex_qualified_model(target):
        return bool(codex_connected)
    if model_to_provider(target) == "ollama":
        return True
    return bool(config.llm.resolve_credentials(target).api_key)


def llm_model_groups(config: GDLAgentConfig) -> dict[str, list[dict[str, Any]]]:
    custom = custom_model_options(config)
    custom_ids = {option["id"] for option in custom}
    official = [
        {
            "id": model,
            "label": model,
            "kind": "official",
            "provider": model_to_provider(model),
            "has_api_key": bool(config.llm.resolve_api_key(model)),
        }
        for model in ALL_MODELS
        if model not in custom_ids
    ]
    return {"custom": custom, "official": official}


def custom_model_options(config: GDLAgentConfig) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider in config.llm.custom_providers or []:
        provider_name = str(provider.get("name", "") or "").strip() or "custom"
        protocol = str(provider.get("protocol", "openai") or "openai")
        api_base = str(provider.get("api", "") or provider.get("base_url", "") or "")
        has_api_key = bool(str(provider.get("api_key", "") or "").strip())
        for entry in iter_custom_provider_model_entries(provider):
            alias = entry["alias"]
            if alias in seen:
                continue
            seen.add(alias)
            options.append({
                "id": alias,
                "label": alias,
                "kind": "custom",
                "provider": provider_name,
                "target_model": entry["model"],
                "protocol": protocol,
                "api_base": api_base,
                "has_api_key": has_api_key,
            })
    return options


class WorkbenchSettingsService:
    def __init__(
        self,
        session: Any,
        *,
        llm_adapter_factory: Callable[[Any], Any],
        codex_provider: Any | None = None,
        codex_provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.session = session
        self.llm_adapter_factory = llm_adapter_factory
        # Codex BYOA（ChatGPT 订阅）：懒创建（首次 codex 路由调用时），
        # 测试注入 codex_provider / codex_provider_factory 使用 fake app-server。
        self.codex_provider = codex_provider
        self.codex_provider_factory = codex_provider_factory

    def compiler_settings(self) -> dict[str, str]:
        return {
            "mode": self.session.compiler_mode,
            "converter_path": self.session.converter_path,
            "output_dir": self.session.output_dir,
        }

    def update_compiler_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode") or self.session.compiler_mode).strip().lower()
        if mode not in {"mock", "lp"}:
            return {"ok": False, "error": f"Unsupported compiler mode: {mode}"}
        self.session.compiler_mode = mode
        # 缺键 ≠ 清空：只在表单显式传了字段时才覆盖，防止设置面板保存其他选项时
        # 把已存路径意外抹掉（2026-08-13 编译器路径反复丢失事故）
        if "converter_path" in body:
            self.session.converter_path = str(body.get("converter_path") or "").strip()
        if "output_dir" in body:
            self.session.output_dir = str(body.get("output_dir") or "").strip()
        self.session.config.compiler.mode = self.session.compiler_mode
        self.session.config.compiler.path = self.session.converter_path
        self.session.config.output_dir = self.session.output_dir or "./output"
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "compiler": self.compiler_settings()}

    def _codex_provider(self) -> Any:
        """懒创建 service 级 CodexProvider（仅 codex 路由调用；llm_settings 不隐式拉起）。"""
        if self.codex_provider is None:
            if self.codex_provider_factory is not None:
                self.codex_provider = self.codex_provider_factory()
            else:
                from openbrep.codex.provider import CodexProvider

                self.codex_provider = CodexProvider()
        return self.codex_provider

    def _known_codex_status(self) -> dict[str, Any] | None:
        """llm_settings 用的轻量 codex 状态：只在 provider 已存在时查询，
        绝不隐式拉起 app-server；异常降级为 error 状态，不影响整体设置。"""
        if self.codex_provider is None:
            return None
        try:
            return self.codex_provider.status()
        except Exception:
            return {"state": "error", "connected": False, "account": None}

    def codex_route(
        self,
        method: str,
        route: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """workbench_api 的 codex 路由分发（保持 workbench_api 行数阈值内）。"""
        method = method.upper()
        if method == "GET" and route == "/api/settings/llm/codex/status":
            return self.codex_status()
        if method == "POST" and route == "/api/settings/llm/codex/login/start":
            return self.codex_login_start()
        if method == "POST" and route == "/api/settings/llm/codex/logout":
            return self.codex_logout()
        if method == "GET" and route == "/api/settings/llm/codex/models":
            return self.codex_models()
        return {"ok": False, "error": f"Unknown route: {method} {route}"}

    def llm_settings(self) -> dict[str, Any]:
        groups = llm_model_groups(self.session.config)
        model_options = groups["custom"] + groups["official"]
        models = [option["id"] for option in model_options]
        codex = self._known_codex_status()
        codex_block = None
        if codex is not None:
            codex_block = {key: codex.get(key) for key in ("state", "connected", "codex_available", "account")}
            if codex.get("error"):
                codex_block["error"] = str(codex["error"])
        return {
            "model": self.session.llm_model,
            "model_available": llm_model_available(
                self.session.config,
                self.session.llm_model,
                codex_connected=codex.get("connected") if codex is not None else None,
            ),
            "models": models,
            "model_options": model_options,
            "model_groups": groups,
            "provider_templates": provider_templates(),
            "api_key": self.session.llm_api_key,
            "api_base": self.session.llm_api_base,
            "max_retries": self.session.max_retries,
            "assistant_settings": self.session.assistant_settings,
            "codex": codex_block,
        }

    def config_revision(self) -> dict[str, Any]:
        """Return a cheap fingerprint of config.toml so the UI can detect external edits.

        The revision changes whenever the file is written (mtime_ns or size differs).
        Reading it has no side effects and does not reload session state.
        """
        path = Path(self.session.config_path)
        try:
            stat = path.stat()
        except OSError:
            return {"ok": True, "revision": "missing"}
        return {"ok": True, "revision": f"{stat.st_mtime_ns}:{stat.st_size}"}

    def reload_runtime_settings(self) -> dict[str, Any]:
        self.session.config = load_workbench_config(self.session.config_path)
        self.session.compiler_mode = (
            self.session.config.compiler.mode if self.session.config.compiler.mode in {"mock", "lp"} else "mock"
        )
        self.session.converter_path = self.session.config.compiler.path or ""
        self.session.output_dir = "" if self.session.config.output_dir in {"", "./output"} else self.session.config.output_dir
        self.session.llm_model = self.session.config.llm.model
        self.session.llm_api_key = self.session.config.llm.resolve_api_key() or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base() or ""
        self.session.max_retries = self.session.config.agent.max_iterations
        self.session.assistant_settings = self.session.config.llm.assistant_settings or ""
        self.session.recent_project_paths = list(self.session.config.recent_projects or [])
        return {
            "ok": True,
            "compiler": self.compiler_settings(),
            "llm": self.llm_settings(),
        }

    def update_llm_model_only(self, body: dict[str, Any]) -> dict[str, Any]:
        """只切换 model 字段，不修改用户配置的 key，但会重新解析当前模型对应的 key。

        凭据模型（与 update_llm_settings 不同，这里不写顶层 api_key/api_base）：
        顶层 api_key/api_base 是全局兜底；provider_keys / custom_providers
        才是按模型的凭据表，运行时 resolve_api_key/api_base(model) 按模型解析。
        把解析结果写回顶层会让旧模型的 key 通过兜底逻辑"继承"给下一个模型
        （实测：deepseek→dk-old 后切 glm 会被误判为可用），所以不写。
        """
        model = str(body.get("model") or "").strip()
        if not model:
            return {"ok": False, "error": "Model is required."}
        if is_codex_qualified_model(model):
            # openai-codex 订阅模型：保证 provider 条目存在（api_mode=codex_app_server），
            # 否则解析链路会误落到 API-key/顶层凭据（fail closed 要求）。
            ensure_codex_provider_entry(self.session.config)
        self.session.llm_model = model
        self.session.config.llm.model = model
        self.session.llm_api_key = self.session.config.llm.resolve_api_key(model) or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base(model) or ""
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "llm": self.llm_settings()}

    def update_llm_api_key(self, body: dict[str, Any]) -> dict[str, Any]:
        """保存模型的 API key：官方模型写入 provider_keys，自定义代理写入对应 custom_providers 条目。"""
        model = str(body.get("model") or self.session.llm_model).strip()
        api_key = str(body.get("api_key") or "").strip()
        if not model:
            return {"ok": False, "error": "Model is required."}
        if not api_key:
            return {"ok": False, "error": "API key is required."}
        custom_match = self.session.config.llm._find_custom_provider_match(model)
        if custom_match is not None:
            provider = custom_match.get("provider")
            if isinstance(provider, dict):
                provider["api_key"] = api_key
        else:
            provider_name = model_to_provider(model)
            if not provider_name or provider_name == "custom":
                return {"ok": False, "error": f"Unknown provider for model: {model}"}
            self.session.config.llm.provider_keys[provider_name] = api_key
        # 重新解析当前会话模型的凭据，保存后立即可用
        self.session.llm_api_key = self.session.config.llm.resolve_api_key(self.session.llm_model) or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base(self.session.llm_model) or ""
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "llm": self.llm_settings()}

    def update_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.session.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required."}
        self.session.llm_model = model
        self.session.llm_api_key = str(body.get("api_key") or "").strip()
        self.session.llm_api_base = str(body.get("api_base") or "").strip()
        self.session.assistant_settings = str(body.get("assistant_settings") or "")
        try:
            self.session.max_retries = max(1, min(10, int(body.get("max_retries") or self.session.max_retries)))
        except (TypeError, ValueError):
            self.session.max_retries = 5

        self.session.config.llm.model = self.session.llm_model
        if is_codex_qualified_model(self.session.llm_model):
            ensure_codex_provider_entry(self.session.config)
        self.session.config.llm.assistant_settings = self.session.assistant_settings
        self.session.config.agent.max_iterations = self.session.max_retries
        apply_llm_credentials_to_config(
            self.session.config,
            model=self.session.llm_model,
            api_key=self.session.llm_api_key,
            api_base=self.session.llm_api_base,
        )
        self.session.llm_api_key = self.session.config.llm.resolve_api_key(self.session.llm_model) or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base(self.session.llm_model) or ""
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "llm": self.llm_settings()}

    # ── Codex BYOA（ChatGPT 订阅）───────────────────────────
    # 所有响应只含枚举化状态/脱敏信息；token、JWT、account id、authUrl、
    # auth 路径一律不离开 openbrep/codex（见 codex/app_server.py、provider.py）。

    def codex_status(self) -> dict[str, Any]:
        """GET /api/settings/llm/codex/status"""
        try:
            provider = self._codex_provider()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        try:
            status = provider.status()
        except Exception as exc:
            status = {"state": "error", "connected": False, "account": None, "error": str(exc)}
        payload: dict[str, Any] = {"ok": True, **status}
        payload["model"] = self.session.llm_model
        payload["model_available"] = llm_model_available(
            self.session.config,
            self.session.llm_model,
            codex_connected=status.get("connected"),
        )
        return payload

    def codex_login_start(self) -> dict[str, Any]:
        """POST /api/settings/llm/codex/login/start：只触发终端用户浏览器 flow。

        登录按钮绝不继承开发者账号：app-server 跑在独立 CODEX_HOME，
        login/start 返回的 authUrl 由后端直接打开浏览器，不返回给前端。
        """
        try:
            provider = self._codex_provider()
            result = provider.login_start()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    def codex_logout(self) -> dict[str, Any]:
        """POST /api/settings/llm/codex/logout：退出登录，模型立即不可用。"""
        try:
            provider = self._codex_provider()
            result = provider.logout()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    def codex_models(self) -> dict[str, Any]:
        """GET /api/settings/llm/codex/models：model/list 动态目录（不硬编码）。"""
        try:
            provider = self._codex_provider()
            models = provider.models()
        except Exception as exc:
            code = getattr(exc, "code", None)
            return {"ok": False, "code": code, "error": str(exc)}
        return {"ok": True, "models": models}

    def test_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.session.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required.", "category": "llm_configuration"}

        test_config = copy.deepcopy(self.session.config)
        test_config.llm.model = model
        test_config.llm.assistant_settings = str(body.get("assistant_settings") or self.session.assistant_settings)
        # 连接测试省 token 但不能砍太狠：强制思考的模型（如 kimi-k2.7-code）
        # 需要 reasoning 余量，max_tokens=8/16 会只出思考不出正文，误报失败
        test_config.llm.max_tokens = min(test_config.llm.max_tokens, 1024)
        test_config.llm.timeout = min(test_config.llm.timeout, 20)
        apply_llm_credentials_to_config(
            test_config,
            model=model,
            api_key=str(body.get("api_key") or "").strip(),
            api_base=str(body.get("api_base") or "").strip(),
        )

        start = time.perf_counter()
        try:
            # 不压 temperature=0：端点约束与 thinking 模式绑定（kimi-k2.6 关思考
            # 只允许 0.6、k2.7-code 只允许 1），硬塞 0 会 400 误报。temperature
            # 交给 adapter 按 provider 条目级/顶层解析（与生产调用同一行为）。
            response = self.llm_adapter_factory(test_config.llm).generate(
                [{"role": "user", "content": "Reply with OK."}],
                timeout=20,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc) or exc.__class__.__name__,
                "detail": format_llm_exception_detail(exc),
                "category": "llm_configuration",
                "model": model,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }

        return {
            "ok": True,
            "message": "LLM connection OK",
            "model": response.model or model,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
