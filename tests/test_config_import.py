"""卡13：配置导入测试（dry-run 零写入 + 提交复查 revision + key 保留两分支）。"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.workbench.config_commit import file_revision
from openbrep.workbench.config_port import apply_import, parse_import_content, plan_import
from openbrep.workbench.provider_service import ProviderSettingsService


def _service(tmp_path, config: GDLAgentConfig):
    config_path = tmp_path / "config.toml"
    config.save(str(config_path))
    session = SimpleNamespace(
        config=GDLAgentConfig.load(str(config_path)), config_path=config_path, llm_model="glm-4-flash"
    )
    return ProviderSettingsService(session), session


def _config_with_relay() -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "relay", "api": "https://relay.example/v1", "api_mode": "chat_completions",
        "api_key": "test-existing-key-0001",
        "default_model": "old-default",
        "models": [{"alias": "m", "model": "m"}],
    })
    return config


def test_parse_accepts_full_llm_table_and_fragments():
    full = parse_import_content("""
[llm]
model = "relay/m"
[[llm.providers]]
name = "a"
api = "https://a.example/v1"
""")
    assert [e["name"] for e in full["entries"]] == ["a"]
    assert full["incoming_model"] == "relay/m"

    legacy = parse_import_content("""
[[llm.custom_providers]]
name = "b"
""")
    assert [e["name"] for e in legacy["entries"]] == ["b"]

    fragment = parse_import_content("""
[[providers]]
name = "c"
""")
    assert [e["name"] for e in fragment["entries"]] == ["c"]

    broken = parse_import_content("not [valid toml")
    assert broken["entries"] == []
    assert broken["errors"]


def test_preview_zero_write_and_diff_fields(tmp_path):
    service, session = _service(tmp_path, _config_with_relay())
    content = """
[[llm.providers]]
name = "relay"
api = "https://changed.example/v1"
api_key = "test-incoming-key-0001"
models = ["m", "m2"]

[[llm.providers]]
name = "fresh"
api = "https://fresh.example/v1"
"""
    before = session.config_path.read_text(encoding="utf-8")

    response = service.route("POST", "/api/settings/llm/import", {"content": content, "confirm": False})

    assert response["ok"] is True
    assert response["confirm"] is False
    assert response["revision"] == file_revision(session.config_path)
    # 预览零写入
    assert session.config_path.read_text(encoding="utf-8") == before
    # to_update 逐字段差异；api_key diff 显示掩码不泄露明文
    update = next(u for u in response["to_update"] if u["name"] == "relay")
    fields = {c["field"]: c for c in update["changes"]}
    assert fields["api"]["old"] == "https://relay.example/v1"
    assert fields["api"]["new"] == "https://changed.example/v1"
    assert fields["api_key"]["new"] == "tes…0001"
    assert "test-incoming-key-0001" not in __import__("json").dumps(response, ensure_ascii=False)
    assert response["to_add"] and response["to_add"][0]["name"] == "fresh"


def test_preview_default_model_conflict_keeps_existing():
    plan = plan_import(_config_with_relay(), """
[[llm.providers]]
name = "relay"
default_model = "new-default"
""")
    assert plan["to_update"] == []  # 默认模型冲突不算字段更新
    conflict = plan["conflicts"][0]
    assert conflict["rule"] == "keep_existing"
    assert conflict["incoming"] == "new-default"
    assert conflict["existing"] == "old-default"


def test_preview_codex_and_reserved_skipped():
    plan = plan_import(_config_with_relay(), """
[[llm.providers]]
name = "openai-codex"
api_mode = "codex_app_server"

[[llm.providers]]
name = "zhipu"
api = "https://x"

[[llm.providers]]
name = "bad name"
""")
    reasons = {s["name"]: s["reason"] for s in plan["skipped"]}
    assert "codex_entry_protected" in reasons["openai-codex"]
    assert "reserved_name" in reasons["zhipu"]
    assert "invalid_name" in reasons["bad name"]


def test_confirm_applies_via_atomic_commit(tmp_path):
    service, session = _service(tmp_path, _config_with_relay())
    content = """
[[llm.providers]]
name = "fresh"
api = "https://fresh.example/v1"
api_key = "test-fresh-key-00001"

[[llm.providers]]
name = "relay"
api = "https://changed.example/v1"
"""
    revision = file_revision(session.config_path)

    response = service.route("POST", "/api/settings/llm/import", {
        "content": content, "confirm": True, "expected_revision": revision,
    })

    assert response["ok"] is True
    names = [p["name"] for p in response["providers"]]
    assert "fresh" in names
    reloaded = GDLAgentConfig.load(str(session.config_path))
    by_name = {p["name"]: p for p in reloaded.llm.providers}
    assert by_name["fresh"]["api"] == "https://fresh.example/v1"
    assert by_name["relay"]["api"] == "https://changed.example/v1"
    # 既有 key 保留（incoming 未提供 key）
    assert by_name["relay"]["api_key"] == "test-existing-key-0001"


def test_confirm_without_external_change_conflict_rejected(tmp_path):
    """提交前外部修改 → config_modified 拒绝，零写入。"""
    service, session = _service(tmp_path, _config_with_relay())
    stale_revision = file_revision(session.config_path)
    # 模拟外部修改
    import time

    time.sleep(0.01)
    session.config_path.write_text(
        session.config_path.read_text(encoding="utf-8") + "\n# external edit\n", encoding="utf-8"
    )

    response = service.route("POST", "/api/settings/llm/import", {
        "content": '[[llm.providers]]\nname = "x"\n',
        "confirm": True,
        "expected_revision": stale_revision,
    })

    assert response["ok"] is False
    assert response["code"] == "config_modified"
    reloaded = GDLAgentConfig.load(str(session.config_path))
    assert all(p["name"] != "x" for p in reloaded.llm.providers)


def test_confirm_missing_revision_rejected(tmp_path):
    service, _ = _service(tmp_path, _config_with_relay())

    response = service.route("POST", "/api/settings/llm/import", {
        "content": '[[llm.providers]]\nname = "x"\n', "confirm": True,
    })

    assert response["ok"] is False
    assert response["code"] == "invalid_request"


def test_key_preservation_two_branches(tmp_path):
    """key 保留规则：incoming 缺省/空 → 保留；显式非空 → 替换。"""
    config = _config_with_relay()
    apply_import(config, '[[llm.providers]]\nname = "relay"\napi = "https://relay.example/v1"\n')
    assert config.llm.providers[0]["api_key"] == "test-existing-key-0001"  # 缺省保留

    config2 = _config_with_relay()
    apply_import(config2, '[[llm.providers]]\nname = "relay"\napi_key = ""\n')
    assert config2.llm.providers[0]["api_key"] == "test-existing-key-0001"  # 显式空也保留

    config3 = _config_with_relay()
    apply_import(config3, '[[llm.providers]]\nname = "relay"\napi_key = "test-new-key-0000001"\n')
    assert config3.llm.providers[0]["api_key"] == "test-new-key-0000001"  # 显式非空替换


def test_legacy_keys_import_and_saved_as_canonical(tmp_path):
    """legacy [[llm.custom_providers]] 导入后保存为新规范键。"""
    service, session = _service(tmp_path, GDLAgentConfig())

    response = service.route("POST", "/api/settings/llm/import", {
        "content": """
[[llm.custom_providers]]
name = "legacy"
base_url = "https://legacy.example/v1"
protocol = "anthropic"
""",
        "confirm": True,
        "expected_revision": file_revision(session.config_path),
    })

    assert response["ok"] is True
    text = session.config_path.read_text(encoding="utf-8")
    assert "[[llm.providers]]" in text
    assert "custom_providers" not in text
    assert "base_url" not in text
    reloaded = GDLAgentConfig.load(str(session.config_path))
    entry = reloaded.llm.providers[0]
    assert entry["api"] == "https://legacy.example/v1"
    assert entry["api_mode"] == "anthropic_messages"


def test_error_text_redacted(tmp_path):
    config = _config_with_relay()
    # 构造含 secret 的解析错误：secret 出现在非法 TOML 行里
    bad_toml = '[[llm.providers]]\nname = "x"\napi_key = "test-leaky-key-000001"\nbroken line here =\n'
    plan = plan_import(config, bad_toml)

    assert plan["errors"]
    dumped = __import__("json").dumps(plan, ensure_ascii=False)
    assert "test-leaky-key-000001" not in dumped


def test_import_via_apply_direct_new_entry_round_trip(tmp_path):
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "config.toml"
        config = GDLAgentConfig()
        apply_import(config, """
[[llm.providers]]
name = "fresh"
api = "https://fresh.example/v1"
api_mode = "responses"
models = [{alias = "a1", model = "m1"}]
""")
        config.save(str(config_path))
        reloaded = GDLAgentConfig.load(str(config_path))
        entry = reloaded.llm.providers[0]
        assert entry["name"] == "fresh"
        assert entry["api_mode"] == "responses"
        assert entry["models"] == [{"alias": "a1", "model": "m1"}]


def test_import_note_for_incoming_default_model():
    plan = plan_import(_config_with_relay(), """
[llm]
model = "other/model"
[[llm.providers]]
name = "fresh"
""")
    assert any("不会应用" in note for note in plan["notes"])


if __name__ == "__main__":
    pytest.main([__file__])
