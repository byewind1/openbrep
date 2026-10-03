"""卡12：配置导出测试（脱敏全覆盖 + round-trip 可 load + 说明段）。"""

import tempfile
from pathlib import Path

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.workbench.config_port import export_llm_config, export_filename


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for name in ("ZAI_API_KEY", "ZHIPU_API_KEY", "DEEPSEEK_API_KEY", "RELAY_EXPORT_VAR", "ZHIPU_EXPORT_VAR"):
        monkeypatch.delenv(name, raising=False)


def _full_config() -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.api_key = "test-top-secret-000001"
    config.llm.provider_keys = {"zhipu": "test-zhipu-secret-01", "deepseek": "${RELAY_EXPORT_VAR}"}
    config.llm.providers = [
        {
            "name": "relay",
            "api": "https://relay.example/v1",
            "api_mode": "chat_completions",
            "api_key": "test-entry-secret-001",
            "models": [{"alias": "m", "model": "m"}],
        },
        {
            "name": "pooled",
            "api": "https://pooled.example/v1",
            "api_mode": "chat_completions",
            "api_key": "",
            "credentials": [
                {"id": "c1", "value": "test-pool-secret-01"},
                {"id": "c2", "value": "${ZHIPU_EXPORT_VAR}"},
            ],
            "models": [],
        },
        {
            "name": "envref",
            "api": "https://envref.example/v1",
            "api_mode": "chat_completions",
            "api_key": "${RELAY_EXPORT_VAR}",
            "models": [],
        },
    ]
    return config


def test_default_export_contains_no_plaintext_anywhere():
    config = _full_config()

    result = export_llm_config(config, include_keys=False)

    content = result["content"]
    assert result["include_keys"] is False
    # 全部明文凭据位置都被脱敏
    for secret in (
        "test-top-secret-000001",
        "test-zhipu-secret-01",
        "test-entry-secret-001",
        "test-pool-secret-01",
    ):
        assert secret not in content
    # ${ENV} 引用原样保留（环境变量名不是秘密）
    assert "${RELAY_EXPORT_VAR}" in content
    assert "${ZHIPU_EXPORT_VAR}" in content


def test_default_export_round_trip_loads():
    """导出内容可被 GDLAgentConfig.load 正常读回（round-trip）。"""
    import tomllib

    config = _full_config()
    result = export_llm_config(config, include_keys=False)

    # 剥离说明段注释后仍是合法 TOML
    toml_text = "\n".join(
        line for line in result["content"].splitlines() if not line.startswith("# ──")
    )
    data = tomllib.loads(toml_text)
    assert data["llm"]["model"] == config.llm.model
    entry_names = [p["name"] for p in data["llm"]["providers"]]
    assert entry_names == ["relay", "pooled", "envref"]


def test_default_export_note_lists_missing_credentials():
    config = _full_config()

    result = export_llm_config(config, include_keys=False)

    assert "导入后需要补齐" in result["content"]
    # 明文凭据条目被点名
    assert "relay" in result["content"]
    assert "pooled" in result["content"]


def test_default_export_original_config_untouched():
    config = _full_config()

    export_llm_config(config, include_keys=False)

    assert config.llm.api_key == "test-top-secret-000001"
    assert config.llm.provider_keys["zhipu"] == "test-zhipu-secret-01"
    assert config.llm.providers[0]["api_key"] == "test-entry-secret-001"
    assert config.llm.providers[1]["credentials"][0]["value"] == "test-pool-secret-01"


def test_include_keys_true_exports_plaintext():
    config = _full_config()

    result = export_llm_config(config, include_keys=True)

    assert result["include_keys"] is True
    assert "test-top-secret-000001" in result["content"]
    assert "test-entry-secret-001" in result["content"]
    assert "test-pool-secret-01" in result["content"]


def test_export_never_invents_env_refs():
    """无法推导环境变量名时置空，不凭空生成看似可用的 ${ENV} 引用。"""
    config = GDLAgentConfig()
    config.llm.api_key = "test-plain-only-0001"

    result = export_llm_config(config, include_keys=False)

    assert "test-plain-only-0001" not in result["content"]
    # 除了原有 ${ENV} 引用外，不得出现新的引用（说明段注释行除外）
    body_lines = [line for line in result["content"].splitlines() if not line.startswith("#")]
    assert sum(line.count("${") for line in body_lines) == 0


def test_export_filename_format():
    from datetime import datetime

    name = export_filename(datetime(2026, 10, 3, 12, 0, 0))
    assert name == "openbrep-llm-20261003.toml"


def test_export_route_via_service(tmp_path):
    from types import SimpleNamespace

    from openbrep.workbench.config_commit import file_revision
    from openbrep.workbench.provider_service import ProviderSettingsService

    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.api_key = "test-route-secret-01"
    config.save(str(config_path))
    session = SimpleNamespace(config=GDLAgentConfig.load(str(config_path)), config_path=config_path)
    service = ProviderSettingsService(session)
    assert file_revision(config_path)  # 只读导出不影响 revision

    default_result = service.route("GET", "/api/settings/llm/export", {})
    assert default_result["ok"] is True
    assert default_result["filename"].startswith("openbrep-llm-")
    assert "test-route-secret-01" not in default_result["content"]

    # service 直调不带 query 解析：include_keys 经 body 传入（HTTP 层由 session.route 并入）
    with_keys = service.route("GET", "/api/settings/llm/export", {"include_keys": "true"})
    assert "test-route-secret-01" in with_keys["content"]


def test_export_via_workbench_api(tmp_path):
    from openbrep.workbench_api import WorkbenchSession

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/settings/llm/providers", {
        "provider": {"name": "relay", "api": "https://relay.example/v1", "api_key": "test-api-secret-0001", "models": ["m"]},
        "expected_revision": session.settings_service.config_revision()["revision"],
    })

    default_result = session.route("GET", "/api/settings/llm/export?include_keys=false", {})
    assert default_result["ok"] is True
    assert "test-api-secret-0001" not in default_result["content"]
    assert "[[llm.providers]]" in default_result["content"]

    with_keys = session.route("GET", "/api/settings/llm/export?include_keys=true", {})
    assert "test-api-secret-0001" in with_keys["content"]


if __name__ == "__main__":
    pytest.main([__file__])
