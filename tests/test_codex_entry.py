"""双入口 Codex 链路（2026-09-17）契约测试。

覆盖：入口/home/锁位置解析、本机 Codex 配置的只读解析（含秘密零回显与
零写入）、三态判定、provider 的 local 入口行为、设置服务入口切换与路由门禁。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbrep.codex.entry import (
    DEFAULT_CODEX_ENTRY,
    ENTRY_LOCAL,
    ENTRY_MANAGED,
    codex_home_for_entry,
    codex_home_kind,
    entry_auth_source,
    is_codex_entry,
    lock_path_for_home,
    normalize_codex_entry,
)
from openbrep.codex.local_config import (
    local_entry_verdict,
    read_local_codex_config,
)
from openbrep.codex.provider import CodexEntryManagedOnlyError, CodexProvider
from openbrep.config import GDLAgentConfig
from openbrep.workbench.settings_service import WorkbenchSettingsService

_SECRET_TOKEN = "sk-supersecret-deepseek-token"
_SECRET_KEY = "sk-second-secret-key"

# 复用 provider 的秘密门禁口径（D1）：这些字段名与取值绝不允许出现在 API payload 里
_FORBIDDEN_KEYS = {
    "token",
    "jwt",
    "access_token",
    "auth_url",
    "authUrl",
    "loginId",
    "login_id",
    "auth_path",
    "codex_home",
    "authorization",
    "chatgpt_account_id",
    "accountId",
}
_FORBIDDEN_VALUES = ("sk-", "eyj", "auth.openai.com", "auth.json", ".codex", _SECRET_TOKEN, _SECRET_KEY)


def _assert_no_secrets(payload, where="payload"):
    def walk(value, path):
        if isinstance(value, dict):
            for key, val in value.items():
                assert str(key).lower() not in _FORBIDDEN_KEYS, f"{where} 泄露秘密字段: {path}.{key}"
                walk(val, f"{path}.{key}")
        elif isinstance(value, list):
            for index, val in enumerate(value):
                walk(val, f"{path}[{index}]")
        elif isinstance(value, str):
            low = value.lower()
            for bad in _FORBIDDEN_VALUES:
                assert bad.lower() not in low, f"{where} 泄露秘密值: {path} 含 {bad!r}"

    walk(payload, "$")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, float]]:
    """目录树快照（相对路径 → 大小/mtime），用于证明 local 入口零写入。"""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }


def _write_local_home(
    tmp_path: Path,
    *,
    config_text: str | None = None,
    catalog: dict | None = None,
    cache: dict | None = None,
    auth: bool = False,
    catalog_name: str = "catalog.json",
) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    if config_text is not None:
        (home / "config.toml").write_text(config_text, encoding="utf-8")
    if catalog is not None:
        (home / catalog_name).write_text(json.dumps(catalog), encoding="utf-8")
    if cache is not None:
        (home / "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    if auth:
        (home / "auth.json").write_text("{}", encoding="utf-8")
    return home


def _catalog(*slugs: str) -> dict:
    return {
        "models": [
            {"slug": slug, "display_name": slug.upper(), "visibility": "list"}
            for slug in slugs
        ]
    }


_CUSTOM_PROVIDER_CONFIG = f"""
model = "deepseek-v4-flash"
model_provider = "custom"
model_catalog_json = "catalog.json"
model_reasoning_effort = "high"

[model_providers.custom]
name = "deepseek"
base_url = "https://api.deepseek.com"
wire_api = "responses"
requires_openai_auth = false
experimental_bearer_token = "{_SECRET_TOKEN}"
"""


# ── 入口 / home / 锁位置 ────────────────────────────────────────────────────


def test_entry_normalization_and_defaults():
    assert normalize_codex_entry("LOCAL") == ENTRY_LOCAL
    assert normalize_codex_entry("managed") == ENTRY_MANAGED
    assert normalize_codex_entry("") == DEFAULT_CODEX_ENTRY == ENTRY_MANAGED
    assert normalize_codex_entry("chatgpt") == DEFAULT_CODEX_ENTRY
    assert is_codex_entry("local") and is_codex_entry("managed")
    assert not is_codex_entry("auto") and not is_codex_entry(None)


def test_local_home_follows_codex_home_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex"))
    assert codex_home_for_entry(ENTRY_LOCAL) == tmp_path / "custom-codex"
    # 托管入口不跟随 CODEX_HOME：两条链路绝不允许指向同一份 home
    monkeypatch.delenv("OPENBREP_CODEX_HOME", raising=False)
    assert codex_home_for_entry(ENTRY_MANAGED) == Path.home() / ".openbrep" / "codex"


def test_managed_home_override_is_openbrep_specific(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex"))
    monkeypatch.setenv("OPENBREP_CODEX_HOME", str(tmp_path / "managed"))
    assert codex_home_for_entry(ENTRY_MANAGED) == tmp_path / "managed"
    assert codex_home_for_entry(ENTRY_LOCAL) == tmp_path / "custom-codex"


def test_lock_path_lives_outside_the_codex_home(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENBREP_CODEX_LOCK_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".codex"
    lock = lock_path_for_home(home)
    assert lock.parent == tmp_path / ".openbrep" / "run"
    assert str(home) not in str(lock)
    # 同一 home 稳定、不同 home 不撞
    assert lock_path_for_home(home) == lock
    assert lock_path_for_home(tmp_path / ".openbrep" / "codex") != lock


def test_lock_dir_env_override_wins(monkeypatch, tmp_path):
    override = tmp_path / "locks"
    monkeypatch.setenv("OPENBREP_CODEX_LOCK_DIR", str(override))
    assert lock_path_for_home(tmp_path / ".codex").parent == override


def test_home_kind_is_symbolic_never_a_path(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert codex_home_kind(tmp_path / ".codex", ENTRY_LOCAL) == "user_default"
    assert codex_home_kind(tmp_path / "other", ENTRY_LOCAL) == "custom"
    assert codex_home_kind(tmp_path / ".openbrep" / "codex", ENTRY_MANAGED) == "managed"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    assert codex_home_kind(tmp_path / "elsewhere", ENTRY_LOCAL) == "env_override"


def test_entry_auth_source_labels():
    assert entry_auth_source(ENTRY_LOCAL) == "codex_config"
    assert entry_auth_source(ENTRY_MANAGED) == "openbrep_managed"


# ── 本机配置解析 ────────────────────────────────────────────────────────────


def test_read_local_config_prefers_declared_catalog(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash", "deepseek-v4-pro"),
    )
    data = read_local_codex_config(home)

    assert data["ok"] is True
    assert data["models_source"] == "model_catalog_json"
    assert [m["model"] for m in data["models"]] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert data["provider_id"] == "custom"
    assert data["provider_label"] == "deepseek"
    assert data["auth_required"] is False
    assert data["provider_credential"] is True
    assert data["chatgpt_auth"] is False


def test_read_local_config_never_echoes_credentials(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash"),
    )
    data = read_local_codex_config(home)
    text = repr(data)

    assert _SECRET_TOKEN not in text
    assert "experimental_bearer_token" not in text
    assert all("token" not in key for key in data)


def test_read_local_config_hidden_catalog_entries_are_skipped(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text='model = "gpt-5.6-luna"\n',
        catalog={"models": [
            {"slug": "gpt-5.6-luna", "display_name": "Luna", "visibility": "list"},
            {"slug": "gpt-reserve", "display_name": "Reserve", "visibility": "hide"},
        ]},
    )
    data = read_local_codex_config(home)
    assert [m["model"] for m in data["models"]] == ["gpt-5.6-luna"]


def test_read_local_config_falls_back_to_declared_model(tmp_path):
    home = _write_local_home(tmp_path, config_text='model = "deepseek-v4-pro"\n')
    data = read_local_codex_config(home)

    assert data["ok"] is True
    assert data["models_source"] == "config"
    assert [m["model"] for m in data["models"]] == ["deepseek-v4-pro"]
    # 没有 provider 声明 → 按默认 provider 处理，需要 Codex 登录态
    assert data["auth_required"] is True


def test_read_local_config_uses_codex_catalog_cache_for_default_provider(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text='model_provider = "openai"\n',
        cache={"models": [{"slug": "gpt-5.6-luna", "display_name": "Luna", "visibility": "list"}]},
    )
    data = read_local_codex_config(home)

    assert data["models_source"] == "models_cache"
    assert [m["model"] for m in data["models"]] == ["gpt-5.6-luna"]
    assert data["auth_required"] is True


def test_read_local_config_ignores_catalog_cache_for_custom_provider(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text=(
            'model_provider = "custom"\n'
            "[model_providers.custom]\n"
            'name = "deepseek"\n'
            'base_url = "https://api.deepseek.com"\n'
            "requires_openai_auth = false\n"
        ),
        cache={"models": [{"slug": "gpt-5.6-luna", "visibility": "list"}]},
    )
    data = read_local_codex_config(home)
    # 自定义 provider 下 ChatGPT 目录缓存不适用，也不该被当成可用模型
    assert data["models"] == []
    assert data["code"] == "config_no_models"


def test_read_local_config_missing_and_broken_files_degrade(tmp_path):
    missing = read_local_codex_config(tmp_path / "nope")
    assert missing["ok"] is False and missing["code"] == "config_missing"
    assert missing["config_present"] is False

    broken_home = _write_local_home(tmp_path, config_text="model = [unclosed")
    broken = read_local_codex_config(broken_home)
    assert broken["ok"] is False and broken["code"] == "config_unreadable"
    assert broken["config_present"] is True


def test_read_local_config_extracts_reasoning_efforts(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text='model = "gpt-5.6-luna"\nmodel_catalog_json = "catalog.json"\n',
        catalog={"models": [{
            "slug": "gpt-5.6-luna",
            "display_name": "Luna",
            "visibility": "list",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Fast"},
                {"effort": "high", "description": "Deep"},
            ],
            "default_reasoning_level": "high",
        }]},
    )
    data = read_local_codex_config(home)
    entry = data["models"][0]
    assert [e["effort"] for e in entry["efforts"]] == ["low", "high"]
    assert entry["default_effort"] == "high"


# ── 三态判定 ────────────────────────────────────────────────────────────────


def test_verdict_three_states(tmp_path):
    ready_home = _write_local_home(
        tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("deepseek-v4-flash")
    )
    data = read_local_codex_config(ready_home)
    assert local_entry_verdict(data, cli_available=False)["state"] == "no_cli"
    assert local_entry_verdict(data, cli_available=True)["state"] == "ready"

    # 默认 provider 但没有登录态 → signed_out（可操作提示指向终端 codex login）
    signed_out_home = _write_local_home(tmp_path / "s2", config_text='model = "gpt-5.6-luna"\n')
    verdict = local_entry_verdict(
        read_local_codex_config(signed_out_home), cli_available=True
    )
    assert verdict["state"] == "signed_out"
    assert "codex login" in verdict["error"]

    # 有 auth.json → 可用
    signed_in_home = _write_local_home(
        tmp_path / "s3", config_text='model = "gpt-5.6-luna"\n', auth=True
    )
    assert (
        local_entry_verdict(read_local_codex_config(signed_in_home), cli_available=True)["state"]
        == "ready"
    )

    # 没有配置 / 没有模型 → unconfigured
    assert (
        local_entry_verdict(read_local_codex_config(tmp_path / "none"), cli_available=True)["state"]
        == "unconfigured"
    )
    no_models_home = _write_local_home(tmp_path / "s4", config_text="# empty\n")
    assert (
        local_entry_verdict(read_local_codex_config(no_models_home), cli_available=True)["state"]
        == "unconfigured"
    )


# ── provider：local 入口 ───────────────────────────────────────────────────


def test_provider_local_status_and_models_without_app_server(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash", "deepseek-v4-pro"),
    )
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    try:
        status = provider.status()
        assert status["state"] == "ready" and status["connected"] is True
        assert status["entry"] == ENTRY_LOCAL
        assert status["codex_home_kind"] == "custom"
        assert status["auth_source"] == "codex_config"
        assert status["models_source"] == "model_catalog_json"
        # 不驱动 app-server：没有任何 client 被创建
        assert provider._client is None

        models = provider.models()
        assert [m["id"] for m in models] == [
            "openai-codex/deepseek-v4-flash",
            "openai-codex/deepseek-v4-pro",
        ]
        assert all(m["source"] == "codex_config" for m in models)
        _assert_no_secrets(status, "local status")
    finally:
        provider.close()


def test_provider_local_status_reports_no_cli(tmp_path):
    home = _write_local_home(tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("a"))
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=False)
    try:
        status = provider.status()
        assert status["state"] == "no_cli"
        assert status["code"] == "codex_cli_unavailable"
        assert status["codex_available"] is False
    finally:
        provider.close()


def test_provider_local_entry_never_writes_to_codex_home(tmp_path):
    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash"),
        auth=True,
    )
    before = _snapshot_tree(home)
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    try:
        provider.status(refresh=True)
        provider.models(refresh=True)
        provider.status(refresh=True)
    finally:
        provider.close()
    assert _snapshot_tree(home) == before


def test_provider_local_entry_never_creates_a_missing_home(tmp_path):
    missing = tmp_path / "no-codex-here"
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=missing, cli_available=True)
    try:
        status = provider.status()
        assert status["state"] == "unconfigured"
        assert not missing.exists()
    finally:
        provider.close()


def test_provider_local_entry_has_no_login_lifecycle(tmp_path):
    home = _write_local_home(tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("a"))
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    try:
        for call in (
            provider.login_start,
            provider.login_start_device_code,
            provider.login_cancel,
            provider.logout,
            provider.rate_limits,
        ):
            with pytest.raises(CodexEntryManagedOnlyError):
                call()
    finally:
        provider.close()


def test_provider_local_effort_is_delegated_to_codex_config(tmp_path):
    home = _write_local_home(tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("a"))
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    try:
        # 目录未声明 effort → 放行（由用户自己的 Codex 配置决定）
        provider.validate_reasoning_effort("openai-codex/a", "high")
        with pytest.raises(Exception):
            provider.validate_reasoning_effort("openai-codex/a", "high; rm -rf /")
    finally:
        provider.close()


def test_provider_set_entry_switches_home_and_closes_old_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "local-home"))
    monkeypatch.setenv("OPENBREP_CODEX_HOME", str(tmp_path / "managed-home"))
    closed: list[str] = []

    class _FakeClient:
        def start(self):
            return {}

        def close(self):
            closed.append("closed")

    provider = CodexProvider(entry=ENTRY_MANAGED, client_factory=lambda: _FakeClient())
    try:
        assert provider.codex_home == tmp_path / "managed-home"
        provider._get_client()  # 建立 app-server 连接（替身 client）
        assert provider.set_entry("local") == ENTRY_LOCAL
        assert provider.codex_home == tmp_path / "local-home"
        # 换 home 必须关掉旧 app-server（否则旧进程一直占着旧 home 的锁）
        assert closed == ["closed"]
        assert provider.set_entry("local") == ENTRY_LOCAL
        assert closed == ["closed"]
    finally:
        provider.close()


def test_provider_explicit_home_wins_over_entry(tmp_path):
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=tmp_path / "explicit")
    try:
        assert provider.set_entry("managed") == ENTRY_MANAGED
        assert provider.codex_home == tmp_path / "explicit"
    finally:
        provider.close()


def test_local_entry_app_server_never_creates_the_codex_home(tmp_path, monkeypatch):
    captured: dict = {}

    class _RecorderClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.transport = None
            self.server_version = (0, 147, 0)

        def start(self):
            return {}

        def close(self):
            return None

    monkeypatch.setattr("openbrep.codex.provider.CodexAppServerClient", _RecorderClient)
    home = tmp_path / "never-created"
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    try:
        provider._get_client()
    finally:
        provider.close()
    assert captured["entry"] == ENTRY_LOCAL
    assert captured["create_home"] is False
    assert not home.exists()


def test_local_entry_runs_the_same_app_server_turn(tmp_path):
    """本机配置入口不换协议：turn 仍走同一个 app-server 契约（fake app-server）。"""
    import contextlib
    import os
    import sys

    from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport

    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash"),
    )
    saved = {key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")}
    os.environ["FAKE_CODEX_TURN"] = "1"

    def factory():
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=home,
            entry=ENTRY_LOCAL,
            create_home=False,
            extra_args=(str(Path(__file__).resolve().parent / "fake_codex_app_server.py"),),
            rpc_timeout=5.0,
        )
        return CodexAppServerClient(transport=transport)

    provider = CodexProvider(
        entry=ENTRY_LOCAL, codex_home=home, client_factory=factory, cli_available=True
    )
    before = _snapshot_tree(home)
    try:
        result = provider.chat(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "你好"}],
            model="openai-codex/deepseek-v4-flash",
            reasoning_effort="high",
        )
        assert result.finish_reason == "stop"
        assert result.reasoning_effort == "high"
    finally:
        with contextlib.suppress(Exception):
            provider.close()
        for key in list(os.environ):
            if key.startswith("FAKE_CODEX_"):
                os.environ.pop(key, None)
        os.environ.update(saved)
    # 整个 turn 过程（含 app-server 启动握手）不向用户的 Codex home 写任何东西
    assert _snapshot_tree(home) == before


# ── codex 可执行文件解析（打包版精简 PATH）──────────────────────────────────


def test_resolve_codex_binary_falls_back_to_login_shell(monkeypatch, tmp_path):
    from openbrep.codex import app_server

    binary = tmp_path / "custom" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    calls: list[list[str]] = []

    class _Completed:
        stdout = f"/Users/dev/.nvm/versions/node/v20/bin/codex\n{binary}\n"

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _Completed()

    monkeypatch.setattr(app_server, "_LOGIN_SHELL_CACHE", {})
    monkeypatch.setattr(app_server.shutil, "which", lambda _name: None)
    monkeypatch.setattr(app_server.Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.setattr(app_server.subprocess, "run", fake_run)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("OPENBREP_DISABLE_LOGIN_SHELL", raising=False)

    assert app_server.resolve_codex_binary() == str(binary)
    assert len(calls) == 1
    assert calls[0][:2] == ["/bin/zsh", "-lic"]
    # 结果缓存：第二次不再开 shell
    assert app_server.resolve_codex_binary() == str(binary)
    assert len(calls) == 1


def test_resolve_codex_binary_rejects_non_executable_shell_output(monkeypatch, tmp_path):
    from openbrep.codex import app_server

    monkeypatch.setattr(app_server, "_LOGIN_SHELL_CACHE", {})
    monkeypatch.setattr(app_server.shutil, "which", lambda _name: None)
    monkeypatch.setattr(app_server.Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    class _Completed:
        stdout = "codex not found\n/does/not/exist/codex\n"

    monkeypatch.setattr(app_server.subprocess, "run", lambda argv, **kwargs: _Completed())
    assert app_server.resolve_codex_binary() is None


def test_resolve_codex_binary_login_shell_can_be_disabled(monkeypatch, tmp_path):
    from openbrep.codex import app_server

    monkeypatch.setattr(app_server, "_LOGIN_SHELL_CACHE", {})
    monkeypatch.setattr(app_server.shutil, "which", lambda _name: None)
    monkeypatch.setattr(app_server.Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.setenv("OPENBREP_DISABLE_LOGIN_SHELL", "1")

    def boom(*_args, **_kwargs):  # pragma: no cover —— 禁用后绝不该被调用
        raise AssertionError("login shell must not run when disabled")

    monkeypatch.setattr(app_server.subprocess, "run", boom)
    assert app_server.resolve_codex_binary() is None


# ── settings service：入口选择与门禁 ────────────────────────────────────────


class _FakeCodexProvider:
    """服务层替身：没有 set_entry（测试注入），入口绑定应静默跳过。"""

    def __init__(self):
        self.status_result = {
            "state": "signed_out",
            "connected": False,
            "codex_available": True,
            "account": None,
        }
        self.logout_calls = 0
        self.login_calls = 0

    def status(self, *, refresh=False):
        return dict(self.status_result)

    def models(self, *, refresh=False):
        return []

    def login_start(self):
        self.login_calls += 1
        return {"state": "login_started", "method": "chatgpt"}

    def logout(self):
        self.logout_calls += 1
        return {"state": "signed_out"}

    def restart(self):
        return dict(self.status_result)


def _service(config: GDLAgentConfig, config_path: Path, provider=None) -> WorkbenchSettingsService:
    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )
    return WorkbenchSettingsService(
        session,
        llm_adapter_factory=lambda _c: None,
        codex_provider=provider,
    )


def test_entry_route_defaults_to_managed_and_lists_both(tmp_path):
    config = GDLAgentConfig()
    service = _service(config, tmp_path / "config.toml", _FakeCodexProvider())

    response = service.codex_entry()
    assert response["ok"] is True
    assert response["entry"] == ENTRY_MANAGED
    assert {item["entry"] for item in response["entries"]} == {ENTRY_LOCAL, ENTRY_MANAGED}
    assert [item for item in response["entries"] if item["recommended"]][0]["entry"] == ENTRY_LOCAL
    # 默认不清真：既有配置不会因为升级而换链路
    assert config.llm.codex_entry == "managed"


def test_entry_route_switch_persists_and_is_idempotent(tmp_path):
    config = GDLAgentConfig()
    path = tmp_path / "config.toml"
    service = _service(config, path, _FakeCodexProvider())

    response = service.codex_entry({"entry": "local"})
    assert response["ok"] is True and response["entry"] == ENTRY_LOCAL
    assert config.llm.codex_entry == "local"
    assert 'codex_entry = "local"' in path.read_text(encoding="utf-8")

    first_mtime = path.stat().st_mtime_ns
    service.codex_entry({"entry": "local"})
    assert path.stat().st_mtime_ns == first_mtime  # 值没变就不落盘

    service.codex_entry({"entry": "managed"})
    assert config.llm.codex_entry == "managed"
    assert "codex_entry" not in path.read_text(encoding="utf-8")


def test_entry_route_rejects_unknown_entry(tmp_path):
    config = GDLAgentConfig()
    path = tmp_path / "config.toml"
    service = _service(config, path, _FakeCodexProvider())

    response = service.codex_entry({"entry": "anthropic"})
    assert response["ok"] is False
    assert response["code"] == "invalid_codex_entry"
    assert config.llm.codex_entry == "managed"
    assert not path.exists()


def test_llm_settings_exposes_entry_metadata(tmp_path):
    config = GDLAgentConfig()
    config.llm.codex_entry = "local"
    service = _service(config, tmp_path / "config.toml", _FakeCodexProvider())

    settings = service.llm_settings()
    assert settings["codex_entry"] == "local"
    # 替身没有 set_entry → 状态块保持替身返回的内容（不伪造入口字段）
    assert settings["codex"] is not None


def test_llm_settings_codex_block_carries_entry_for_real_provider(tmp_path):
    home = _write_local_home(
        tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("deepseek-v4-flash")
    )
    config = GDLAgentConfig()
    config.llm.codex_entry = "local"
    provider = CodexProvider(entry=ENTRY_LOCAL, codex_home=home, cli_available=True)
    service = _service(config, tmp_path / "config.toml", provider)
    try:
        block = service.llm_settings()["codex"]
        assert block["entry"] == ENTRY_LOCAL
        assert block["codex_home_kind"] == "custom"
        assert block["auth_source"] == "codex_config"
        assert block["state"] == "ready"
        _assert_no_secrets(block, "llm_settings.codex")
    finally:
        provider.close()


def test_service_binds_entry_from_config_to_shared_provider(tmp_path, monkeypatch):
    home = _write_local_home(
        tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("deepseek-v4-flash")
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    # 进程共享 provider 默认是托管入口；服务层必须按 config 把它绑到 local
    from openbrep.codex.provider import CodexProvider as _Provider

    provider = _Provider(entry=ENTRY_MANAGED, cli_available=True)
    config = GDLAgentConfig()
    config.llm.codex_entry = "local"
    service = _service(config, tmp_path / "config.toml", provider)
    try:
        status = service.codex_status()
        assert status["entry"] == ENTRY_LOCAL
        assert status["state"] == "ready"
        assert status["ok"] is True
    finally:
        provider.close()


def test_service_codex_models_route_returns_local_catalog(tmp_path, monkeypatch):
    home = _write_local_home(
        tmp_path,
        config_text=_CUSTOM_PROVIDER_CONFIG,
        catalog=_catalog("deepseek-v4-flash", "deepseek-v4-pro"),
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = GDLAgentConfig()
    config.llm.codex_entry = "local"
    provider = CodexProvider(entry=ENTRY_LOCAL, cli_available=True)
    service = _service(config, tmp_path / "config.toml", provider)
    try:
        payload = service.codex_route("GET", "/api/settings/llm/codex/models")
        assert payload["ok"] is True
        assert [m["id"] for m in payload["models"]] == [
            "openai-codex/deepseek-v4-flash",
            "openai-codex/deepseek-v4-pro",
        ]
        _assert_no_secrets(payload, "codex/models")
    finally:
        provider.close()


def test_local_entry_login_routes_fail_with_stable_code(tmp_path, monkeypatch):
    home = _write_local_home(
        tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("deepseek-v4-flash")
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = GDLAgentConfig()
    config.llm.codex_entry = "local"
    provider = CodexProvider(entry=ENTRY_LOCAL, cli_available=True)
    service = _service(config, tmp_path / "config.toml", provider)
    try:
        for method, route in (
            ("POST", "/api/settings/llm/codex/login/start"),
            ("POST", "/api/settings/llm/codex/login/device-code"),
            ("POST", "/api/settings/llm/codex/login/cancel"),
            ("POST", "/api/settings/llm/codex/logout"),
            ("GET", "/api/settings/llm/codex/rate-limits"),
        ):
            payload = service.codex_route(method, route, {})
            assert payload["ok"] is False, route
            assert payload["code"] == "codex_entry_managed_only", route
            # 文案指向可执行动作（终端 codex login / 切换入口），且零回显
            assert "Codex CLI" in payload["error"] or "托管" in payload["error"]
    finally:
        provider.close()


def test_local_codex_hint_reports_detection_without_paths(tmp_path, monkeypatch):
    home = _write_local_home(
        tmp_path, config_text=_CUSTOM_PROVIDER_CONFIG, catalog=_catalog("deepseek-v4-flash")
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = GDLAgentConfig()
    service = _service(config, tmp_path / "config.toml", _FakeCodexProvider())

    hint = service.codex_entry()["local_hint"]
    assert hint == {
        "detected": True,
        "state": "ready",
        "models": 1,
        "home_kind": "env_override",
    }
    _assert_no_secrets(hint, "local_hint")


def test_local_codex_hint_absent_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "not-configured"))
    config = GDLAgentConfig()
    service = _service(config, tmp_path / "config.toml", _FakeCodexProvider())

    hint = service.codex_entry()["local_hint"]
    assert hint["detected"] is False
    assert hint["state"] == "unconfigured"
    assert hint["models"] == 0
