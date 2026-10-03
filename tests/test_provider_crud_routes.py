"""卡03：Provider 创建/更新路由测试（POST/PUT，经卡00 原子提交）。"""

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.workbench.config_commit import file_revision
from openbrep.workbench.provider_service import ProviderSettingsService


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for name in (
        "ZAI_API_KEY", "ZHIPU_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY", "RELAY_KEY_VAR",
    ):
        monkeypatch.delenv(name, raising=False)


def _session(tmp_path, config: GDLAgentConfig | None = None):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = config or GDLAgentConfig()
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)),
        config_path=config_path,
        llm_model="glm-4-flash",
        llm_api_key="",
        llm_api_base="",
    )
    return ProviderSettingsService(session), session


def _create(service, provider: dict, expected_revision: str | None, **extra):
    body = {"provider": provider, **extra}
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    return service.route("POST", "/api/settings/llm/providers", body)


def _update(service, name: str, provider: dict, expected_revision: str, **extra):
    return service.route(
        "PUT", "/api/settings/llm/providers", {"name": name, "provider": provider, "expected_revision": expected_revision, **extra}
    )


# ── 创建 round-trip ──────────────────────────────────────────────


def test_create_provider_round_trip(tmp_path):
    service, session = _session(tmp_path)
    revision = file_revision(session.config_path)

    response = _create(
        service,
        {
            "name": "relay",
            "api": "https://relay.example/v1",
            "api_mode": "chat_completions",
            "api_key": "test-key-1234567890",
            "default_model": "relay-main",
            "models": ["relay-main", {"alias": "alias-one", "model": "real-one"}],
        },
        revision,
    )

    assert response["ok"] is True
    assert response["provider"]["name"] == "relay"
    assert response["provider"]["model_count"] == 2
    assert response["provider"]["key_display"] == "tes…7890"
    assert response["provider"]["credential"]["resolvable"] is True

    reloaded = GDLAgentConfig.load(str(session.config_path))
    entry = next(p for p in reloaded.llm.providers if p["name"] == "relay")
    assert entry["api"] == "https://relay.example/v1"
    assert entry["api_key"] == "test-key-1234567890"
    assert entry["default_model"] == "relay-main"
    assert {"alias": "alias-one", "model": "real-one"} in entry["models"]


def test_update_provider_round_trip_preserves_unsubmitted_fields(tmp_path):
    service, session = _session(tmp_path)
    create = _create(
        service,
        {
            "name": "relay",
            "api": "https://relay.example/v1",
            "api_key": "test-key-1234567890",
            "models": ["relay-main"],
            "temperature": 0.6,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
        file_revision(session.config_path),
    )
    assert create["ok"] is True

    updated = _update(
        service,
        "relay",
        {"api_key": "test-key-updated-0001", "models": ["relay-main", "extra-model"]},
        file_revision(session.config_path),
    )

    assert updated["ok"] is True
    assert updated["provider"]["model_count"] == 2
    reloaded = GDLAgentConfig.load(str(session.config_path))
    entry = next(p for p in reloaded.llm.providers if p["name"] == "relay")
    assert entry["api_key"] == "test-key-updated-0001"
    # 未提交字段原样保留（卡00 字段覆盖合同在路由层的表现）
    assert entry["temperature"] == 0.6
    assert entry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert entry["api"] == "https://relay.example/v1"


# ── revision 冲突零写入 ──────────────────────────────────────────


def test_create_with_stale_revision_rejected_without_write(tmp_path):
    service, session = _session(tmp_path)
    before = session.config_path.read_text(encoding="utf-8")

    response = _create(service, {"name": "relay", "models": ["m"]}, "stale-revision")

    assert response["ok"] is False
    assert response["code"] == "config_modified"
    assert session.config_path.read_text(encoding="utf-8") == before
    assert GDLAgentConfig.load(str(session.config_path)).llm.providers == []


def test_missing_expected_revision_is_invalid_request(tmp_path):
    service, _ = _session(tmp_path)

    response = _create(service, {"name": "relay"}, None)

    assert response["ok"] is False
    assert response["code"] == "invalid_request"


# ── key 三态（路由层各一例）──────────────────────────────────────


def _entry_after(session) -> dict:
    return GDLAgentConfig.load(str(session.config_path)).llm.providers[0]


def test_update_api_key_absent_keeps_existing(tmp_path):
    service, session = _session(tmp_path)
    _create(service, {"name": "relay", "api_key": "test-key-1234567890", "models": ["m"]}, file_revision(session.config_path))

    result = _update(service, "relay", {"models": ["m", "m2"]}, file_revision(session.config_path))

    assert result["ok"] is True
    assert _entry_after(session)["api_key"] == "test-key-1234567890"


def test_update_api_key_explicit_empty_clears(tmp_path):
    service, session = _session(tmp_path)
    _create(service, {"name": "relay", "api_key": "test-key-1234567890", "models": ["m"]}, file_revision(session.config_path))

    result = _update(service, "relay", {"api_key": ""}, file_revision(session.config_path))

    assert result["ok"] is True
    assert _entry_after(session)["api_key"] == ""


def test_update_api_key_nonempty_replaces(tmp_path):
    service, session = _session(tmp_path)
    _create(service, {"name": "relay", "api_key": "test-key-1234567890", "models": ["m"]}, file_revision(session.config_path))

    result = _update(service, "relay", {"api_key": "test-key-new-0000000"}, file_revision(session.config_path))

    assert result["ok"] is True
    assert _entry_after(session)["api_key"] == "test-key-new-0000000"


# ── 名称与保留身份 ───────────────────────────────────────────────


@pytest.mark.parametrize("bad_name", ["", "  ", "with space", "with/slash", "a\tb"])
def test_create_rejects_invalid_names(tmp_path, bad_name):
    service, _ = _session(tmp_path)

    response = _create(service, {"name": bad_name}, file_revision((service.session.config_path)))

    assert response["ok"] is False
    assert response["code"] == "invalid_name"


def test_create_rejects_codex_and_reserved_names(tmp_path):
    service, _ = _session(tmp_path)

    codex = _create(service, {"name": "openai-codex"}, file_revision(service.session.config_path))
    assert codex["ok"] is False
    assert codex["code"] == "codex_entry_protected"

    reserved = _create(service, {"name": "zhipu"}, file_revision(service.session.config_path))
    assert reserved["ok"] is False
    assert reserved["code"] == "reserved_name"


def test_create_rejects_duplicate_name_case_insensitive(tmp_path):
    service, session = _session(tmp_path)
    assert _create(service, {"name": "relay", "models": ["m"]}, file_revision(session.config_path))["ok"]

    duplicate = _create(service, {"name": "RELAY", "models": ["m"]}, file_revision(session.config_path))

    assert duplicate["ok"] is False
    assert duplicate["code"] == "name_conflict"


def test_create_rejects_unknown_api_mode_with_parser_message(tmp_path):
    service, _ = _session(tmp_path)

    response = _create(
        service,
        {"name": "relay", "api_mode": "grpc", "models": ["m"]},
        file_revision(service.session.config_path),
    )

    assert response["ok"] is False
    assert response["code"] == "invalid_request"
    assert "api_mode" in response["error"]


def test_update_codex_entry_rejected(tmp_path):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    GDLAgentConfig().save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)),
        config_path=config_path,
        llm_model="glm-4-flash",
    )
    # 模拟既有 codex 条目（由 codex 模型保存路径创建）
    session.config.llm.providers.append({
        "name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": [],
        "_explicit_base": True,
    })
    service = ProviderSettingsService(session)

    response = _update(service, "openai-codex", {"api_key": "x"}, file_revision(config_path))

    assert response["ok"] is False
    assert response["code"] == "codex_entry_protected"


def test_update_rejects_rename(tmp_path):
    service, session = _session(tmp_path)
    _create(service, {"name": "relay", "models": ["m"]}, file_revision(session.config_path))

    response = _update(
        service, "relay", {"name": "relay2", "models": ["m"]}, file_revision(session.config_path)
    )

    assert response["ok"] is False
    assert response["code"] == "rename_not_supported"
    assert GDLAgentConfig.load(str(session.config_path)).llm.providers[0]["name"] == "relay"


def test_update_missing_entry_not_found(tmp_path):
    service, session = _session(tmp_path)

    response = _update(service, "ghost", {"models": ["m"]}, file_revision(session.config_path))

    assert response["ok"] is False
    assert response["code"] == "not_found"


# ── 保存即迁移：TOML 只有规范键 ──────────────────────────────────


def test_saved_toml_has_only_canonical_keys(tmp_path):
    service, session = _session(tmp_path)
    _create(service, {"name": "relay", "api": "https://relay.example/v1", "models": ["m"]}, file_revision(session.config_path))

    text = session.config_path.read_text(encoding="utf-8")

    assert "[[llm.providers]]" in text
    assert "custom_providers" not in text  # 旧键不回流
    assert "base_url" not in text
    assert "protocol" not in text


def test_models_request_dedup_and_alias_pairs(tmp_path):
    service, session = _session(tmp_path)

    response = _create(
        service,
        {
            "name": "relay",
            "models": ["m1", "m1", {"alias": "A1", "model": "m1"}, {"alias": "a1", "model": "M1"}, {"alias": "x", "model": "y"}],
        },
        file_revision(session.config_path),
    )

    assert response["ok"] is True
    reloaded = GDLAgentConfig.load(str(session.config_path))
    entry = reloaded.llm.providers[0]
    assert {"alias": "m1", "model": "m1"} in entry["models"]
    assert {"alias": "x", "model": "y"} in entry["models"]
    # 大小写变体去重：两个 A1/m1 只保留先到的一个
    assert len([e for e in entry["models"] if e["alias"].lower() == "a1"]) == 1


if __name__ == "__main__":
    pytest.main([__file__])


# ── 卡04：删除 + 引用拦截 ────────────────────────────────────────


def _delete(service, name: str, expected_revision: str, **extra):
    return service.route(
        "POST", "/api/settings/llm/providers/delete", {"name": name, "expected_revision": expected_revision, **extra}
    )


def _relay_only_session(tmp_path):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "relay", "api": "https://relay.example/v1", "api_key": "test-key-1234567890",
        "models": [{"alias": "relay-main", "model": "relay-main"}, {"alias": "alias-one", "model": "real-one"}],
    })
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)),
        config_path=config_path,
        llm_model="glm-4-flash",
    )
    return ProviderSettingsService(session), session


def test_delete_success_round_trip(tmp_path):
    service, session = _relay_only_session(tmp_path)

    response = _delete(service, "relay", file_revision(session.config_path))

    assert response["ok"] is True
    assert response["deleted"] == "relay"
    assert response["providers"] == []
    reloaded = GDLAgentConfig.load(str(session.config_path))
    assert reloaded.llm.providers == []
    assert session.config.llm.providers == []  # 内存同步发布


def test_delete_missing_revision_conflict(tmp_path):
    service, session = _relay_only_session(tmp_path)
    before = session.config_path.read_text(encoding="utf-8")

    response = _delete(service, "relay", "stale-revision")

    assert response["ok"] is False
    assert response["code"] == "config_modified"
    assert session.config_path.read_text(encoding="utf-8") == before


def test_delete_not_found(tmp_path):
    service, session = _relay_only_session(tmp_path)

    response = _delete(service, "ghost", file_revision(session.config_path))

    assert response["ok"] is False
    assert response["code"] == "not_found"


def test_delete_codex_entry_rejected(tmp_path):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": [],
        "_explicit_base": True,
    })
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="glm-4-flash"
    )
    service = ProviderSettingsService(session)

    response = _delete(service, "openai-codex", file_revision(config_path))

    assert response["ok"] is False
    assert response["code"] == "codex_entry_protected"
    assert GDLAgentConfig.load(str(config_path)).llm.providers != []


@pytest.mark.parametrize(
    ("setup", "expected_location"),
    [
        (lambda c: setattr(c.llm, "model", "relay/real-one"), "llm.model"),
        (lambda c: setattr(c.llm, "retry", {"fallback_chains": {"modify": [{"model": "relay/real-one"}]}}), "llm.retry"),
        (lambda c: setattr(c.llm, "enabled_models", ["relay/real-one"]), "enabled_models"),
        (lambda c: setattr(c.llm, "disabled_providers", ["relay"]), "disabled_providers"),
    ],
)
def test_delete_blocked_by_each_reference_class(tmp_path, setup, expected_location):
    service, session = _relay_only_session(tmp_path)
    setup(session.config)

    response = _delete(service, "relay", file_revision(session.config_path))

    assert response["ok"] is False
    assert response["code"] == "in_use"
    locations = [r["location"] for r in response["refs"]]
    assert expected_location in locations
    # 前端可见性提示作为非阻塞项并入报告
    assert "frontend_visibility" in locations
    assert GDLAgentConfig.load(str(session.config_path)).llm.providers != []  # 零写入


def test_delete_blocked_by_session_model_override(tmp_path):
    from types import SimpleNamespace as _NS

    service, session = _relay_only_session(tmp_path)
    session.session_llm_model = "relay-main"

    response = _delete(service, "relay", file_revision(session.config_path))

    assert response["ok"] is False
    assert response["code"] == "in_use"
    assert "session_model" in [r["location"] for r in response["refs"]]
    # 只读探测：session_llm_model 不被引用检查改动
    assert session.session_llm_model == "relay-main"


def test_delete_refs_context_is_readable(tmp_path):
    service, session = _relay_only_session(tmp_path)
    session.config.llm.model = "relay/real-one"

    response = _delete(service, "relay", file_revision(session.config_path))

    ref = next(r for r in response["refs"] if r["location"] == "llm.model")
    assert ref["context"] == "relay/real-one"
    assert ref["blocking"] is True
    assert ref["detail"]


def test_delete_not_blocked_by_other_provider_references(tmp_path):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.extend([
        {"name": "relay", "api": "https://relay.example/v1", "models": ["relay-main"]},
        {"name": "alpha", "api": "https://alpha.example/v1", "models": ["alpha-model"]},
    ])
    config.llm.model = "alpha/alpha-model"
    config.llm.disabled_providers = ["alpha"]
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="alpha/alpha-model"
    )
    service = ProviderSettingsService(session)

    response = _delete(service, "relay", file_revision(config_path))

    assert response["ok"] is True
    assert [p["name"] for p in response["providers"]] == ["alpha"]


if __name__ == "__main__":
    pytest.main([__file__])


# ── 卡11：available 状态（fail-closed）──────────────────────────


def test_provider_available_ollama_without_key(tmp_path):
    """ollama 免 key：available=true。"""
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "ollama", "api": "http://127.0.0.1:11434/v1", "api_mode": "chat_completions",
        "api_key": "", "models": [{"alias": "qwen", "model": "qwen2.5:14b"}],
    })
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="glm-4-flash"
    )
    service = ProviderSettingsService(session)

    response = service.route("GET", "/api/settings/llm/providers")

    ollama = next(p for p in response["providers"] if p["name"] == "ollama")
    assert ollama["available"] is True
    assert ollama["credential"]["resolvable"] is False


def test_provider_available_codex_fail_closed_without_login(tmp_path):
    """codex 条目：无登录态时 available=false（fail-closed，绝不隐式拉起 app-server）。"""
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": [],
        "_explicit_base": True,
    })
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="glm-4-flash"
    )
    service = ProviderSettingsService(session)

    response = service.route("GET", "/api/settings/llm/providers")

    codex = next(p for p in response["providers"] if p["is_codex"])
    assert codex["available"] is False


def test_provider_available_requires_resolvable_credential(tmp_path):
    from types import SimpleNamespace

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "relay", "api": "https://relay.example/v1", "api_mode": "chat_completions",
        "api_key": "test-key-000000001", "models": [{"alias": "m", "model": "m"}],
    })
    config.llm.providers.append({
        "name": "dry", "api": "https://dry.example/v1", "api_mode": "chat_completions",
        "api_key": "", "models": [{"alias": "m", "model": "m"}],
    })
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="glm-4-flash"
    )
    service = ProviderSettingsService(session)

    response = service.route("GET", "/api/settings/llm/providers")

    by_name = {p["name"]: p for p in response["providers"]}
    assert by_name["relay"]["available"] is True
    assert by_name["dry"]["available"] is False
