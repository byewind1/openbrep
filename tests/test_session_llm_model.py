"""D16：会话级模型切换 API（POST /api/session/llm/model）。

核心不变量：会话切换只改 session 生效模型（+ 会话级 effort），
config.toml 逐字节不变；写配置仍只有 PATCH /api/settings/llm/model 一扇门。
"""

from types import SimpleNamespace

from openbrep.config import GDLAgentConfig
from openbrep.workbench.settings_service import (
    WorkbenchSettingsService,
    effective_session_reasoning_effort,
)
from openbrep.workbench_api import WorkbenchSession
from tests.test_workbench_services import (
    _clear_llm_env_keys,
    _codex_models_payload,
    _FakeCodexProvider,
    _signed_in_status,
)


def _make_service(config, config_path, provider=None):
    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key=config.llm.resolve_api_key() or "",
        llm_api_base=config.llm.resolve_api_base() or "",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
        session_llm_model=None,
        session_reasoning_effort=None,
    )
    return WorkbenchSettingsService(
        session,
        llm_adapter_factory=lambda _c: None,
        codex_provider=provider,
    )


def _write_config(config_path, config) -> bytes:
    config.save(str(config_path))
    return config_path.read_bytes()


def test_session_model_switch_leaves_config_byte_identical(tmp_path, monkeypatch):
    """红队 1：会话切换后 config.toml 逐字节不变（读文件断言）。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    service = _make_service(config, config_path)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "glm-4-flash"})

    assert response["ok"] is True
    assert response["llm"]["model"] == "glm-4-flash"
    assert response["llm"]["session_model"] == "glm-4-flash"
    assert service.session.llm_model == "glm-4-flash"
    assert service.session.llm_api_key == "zk-1"
    assert config_path.read_bytes() == before
    # config 内存对象同样不被触碰
    assert config.llm.model == "deepseek-chat"


def test_session_model_clear_override_restores_config_default(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    service = _make_service(config, config_path)
    before = _write_config(config_path, config)

    service.update_session_llm_model({"model": "glm-4-flash"})
    cleared = service.update_session_llm_model({"model": None})

    assert cleared["ok"] is True
    assert cleared["llm"]["model"] == "deepseek-chat"
    assert cleared["llm"]["session_model"] is None
    assert service.session.llm_model == "deepseek-chat"
    assert service.session.llm_api_key == "dk-1"
    assert config_path.read_bytes() == before


def test_session_model_unknown_model_fails_closed(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    service = _make_service(config, config_path)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "not-a-real-model"})

    assert response["ok"] is False
    assert response["code"] == "unknown_model"
    assert service.session.llm_model == "deepseek-chat"
    assert service.session.session_llm_model is None
    assert config_path.read_bytes() == before


def test_session_model_missing_model_key_rejected(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    service = _make_service(config, config_path)

    response = service.update_session_llm_model({})

    assert response["ok"] is False
    assert response["code"] == "model_required"


def test_session_model_codex_fails_closed_when_signed_out(tmp_path):
    """红队：未登录 codex 模型一律 fail closed，session 与 config 都不动。"""
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status={
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    })
    service = _make_service(config, config_path, provider)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "openai-codex/gpt-5.6-luna"})

    assert response["ok"] is False
    assert response["code"]  # 稳定非空错误码（codex_error 体系）
    assert service.session.llm_model == "glm-4-flash"
    assert service.session.session_llm_model is None
    assert config_path.read_bytes() == before


def test_session_model_codex_not_in_account_catalog_rejected(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_service(config, config_path, provider)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "openai-codex/not-a-real-account-model"})

    assert response["ok"] is False
    assert response["code"] == "model_not_in_catalog"
    assert config_path.read_bytes() == before


def test_session_model_codex_switch_with_session_effort(tmp_path):
    """codex 会话切换 + 会话级 effort：session 生效，config 的 model/effort 都不写。"""
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_service(config, config_path, provider)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({
        "model": "openai-codex/gpt-5.6-luna",
        "reasoning_effort": "high",
    })

    assert response["ok"] is True
    assert response["llm"]["model"] == "openai-codex/gpt-5.6-luna"
    assert response["llm"]["session_model"] == "openai-codex/gpt-5.6-luna"
    assert effective_session_reasoning_effort(service.session) == "high"
    assert config.llm.reasoning_effort == ""
    assert config.llm.model == "glm-4-flash"
    assert config_path.read_bytes() == before
    # 会话切换不得落 codex provider 条目（那是写配置路径的行为）
    assert not config.llm.providers


def test_session_model_codex_rejects_unsupported_effort(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_service(config, config_path, provider)
    before = _write_config(config_path, config)

    # terra 不支持 low（luna 独有）
    response = service.update_session_llm_model({
        "model": "openai-codex/gpt-5.6-terra",
        "reasoning_effort": "low",
    })

    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    assert service.session.session_llm_model is None
    assert config_path.read_bytes() == before


def test_session_model_rejects_effort_for_non_codex_model(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    service = _make_service(config, config_path)

    response = service.update_session_llm_model({
        "model": "glm-4-flash", "reasoning_effort": "high",
    })

    assert response["ok"] is False
    assert response["code"] == "effort_not_for_codex"
    assert service.session.session_llm_model is None


def test_session_model_codex_switch_rejects_stale_saved_effort(tmp_path):
    """未显式传 effort = 跟随 config 已保存 effort；残留不兼容显式拒绝
    （与 update_llm_model_only 同口径）。"""
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.reasoning_effort = "low"  # terra 不支持
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_service(config, config_path, provider)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "openai-codex/gpt-5.6-terra"})

    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    assert config_path.read_bytes() == before


def test_session_model_unconfigured_official_model_reports_unavailable(tmp_path, monkeypatch):
    """红队 5：未配 key 的官方模型会话切换成功（目录内），但 model_available=false
    ——「模型不可用」口径随响应给出，前端 pill 据此显示 ⚠，不静默失败。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1"}
    service = _make_service(config, config_path)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "glm-4-flash"})

    assert response["ok"] is True
    assert response["llm"]["model"] == "glm-4-flash"
    assert response["llm"]["model_available"] is False
    assert config_path.read_bytes() == before


def test_session_model_ollama_freeform_id_allowed(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    service = _make_service(config, config_path)

    response = service.update_session_llm_model({"model": "ollama/my-local-model:7b"})

    assert response["ok"] is True
    assert response["llm"]["session_model"] == "ollama/my-local-model:7b"
    assert response["llm"]["model_available"] is True


def test_session_model_custom_provider_model_allowed(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.custom_providers = [{
        "name": "ymg",
        "api": "https://ymg.example/v1",
        "api_key": "ymg-key",
        "models": ["ymg/deepseek-v3"],
    }]
    service = _make_service(config, config_path)
    before = _write_config(config_path, config)

    response = service.update_session_llm_model({"model": "ymg/deepseek-v3"})

    assert response["ok"] is True
    assert service.session.llm_api_key == "ymg-key"
    assert config_path.read_bytes() == before


def test_settings_default_save_clears_session_override(tmp_path, monkeypatch):
    """设置页显式保存新默认（写配置那扇门）= 用户显式选模型，会话覆盖随之清除。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    service = _make_service(config, config_path)
    _write_config(config_path, config)

    service.update_session_llm_model({"model": "glm-4-flash"})
    assert service.session.session_llm_model == "glm-4-flash"

    saved = service.update_llm_model_only({"model": "deepseek-chat"})

    assert saved["ok"] is True
    assert service.session.session_llm_model is None
    assert saved["llm"]["session_model"] is None
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "deepseek-chat"


def test_reload_runtime_settings_preserves_session_override(tmp_path, monkeypatch):
    """外部编辑 config.toml 触发的重载不清会话覆盖；凭据按生效模型解析。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    service = _make_service(config, config_path)
    _write_config(config_path, config)
    service.update_session_llm_model({"model": "glm-4-flash"})

    reloaded = service.reload_runtime_settings()

    assert reloaded["ok"] is True
    assert service.session.llm_model == "glm-4-flash"
    assert service.session.llm_api_key == "zk-1"
    assert reloaded["llm"]["session_model"] == "glm-4-flash"


def test_session_route_via_workbench_session(tmp_path, monkeypatch):
    """路由级：POST /api/session/llm/model 走 WorkbenchSession.route，config 零写入。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    config.save(str(config_path))
    before = config_path.read_bytes()
    session = WorkbenchSession(config_path=config_path)

    response = session.route("POST", "/api/session/llm/model", {"model": "glm-4-flash"})

    assert response["ok"] is True
    assert session.llm_model == "glm-4-flash"
    assert session.session_llm_model == "glm-4-flash"
    assert config_path.read_bytes() == before
    # snapshot 的 llm 块带 session_model；model 仍是「生效模型」口径
    snap = session.snapshot()
    assert snap["llm"]["model"] == "glm-4-flash"
    assert snap["llm"]["session_model"] == "glm-4-flash"

    cleared = session.route("POST", "/api/session/llm/model", {"model": None})
    assert cleared["ok"] is True
    snap = session.snapshot()
    assert snap["llm"]["model"] == "deepseek-chat"
    assert snap["llm"]["session_model"] is None
    assert config_path.read_bytes() == before


def test_existing_settings_route_still_writes_config(tmp_path, monkeypatch):
    """对照组：写配置的既有路由行为不变（仍是「设为默认」唯一门）。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-1", "zhipu": "zk-1"}
    config.save(str(config_path))
    session = WorkbenchSession(config_path=config_path)

    response = session.route("PATCH", "/api/settings/llm/model", {"model": "glm-4-flash"})

    assert response["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "glm-4-flash"
