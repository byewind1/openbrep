"""卡00：配置原子提交合同测试。

覆盖：revision 冲突零写入；写盘/序列化失败磁盘与内存原样；key 三态；
高级字段 round-trip 不丢；api 缺省（允许顶层兜底）vs 显式空（绝不兜底）；
发布回调只拿已落盘副本；指纹与 settings_service.config_revision 同源。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.workbench.config_commit import (
    ConfigCommitError,
    apply_endpoint_update,
    commit_config_change,
    file_revision,
    resolve_api_key_update,
)
from openbrep.workbench.settings_service import WorkbenchSettingsService


def _make_config(tmp_path: Path) -> tuple[Path, GDLAgentConfig]:
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.save(str(config_path))
    return config_path, GDLAgentConfig.load(str(config_path))


def _provider_config(tmp_path: Path, entry_extra: dict | None = None) -> tuple[Path, GDLAgentConfig]:
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    entry = {
        "name": "relay",
        "api": "https://relay.example/v1",
        "api_key": "sk-old",
        "models": ["relay-model"],
    }
    if entry_extra:
        entry.update(entry_extra)
    config.llm.providers.append(entry)
    config.save(str(config_path))
    return config_path, GDLAgentConfig.load(str(config_path))


def _relay_entry(config: GDLAgentConfig) -> dict:
    return next(p for p in config.llm.providers if p["name"] == "relay")


def _update_relay(config_path: Path, config: GDLAgentConfig, request: dict, **kwargs):
    revision = file_revision(config_path)

    def mutate(working: GDLAgentConfig) -> None:
        entry = _relay_entry(working)
        entry["api_key"] = resolve_api_key_update(entry.get("api_key"), request)
        apply_endpoint_update(entry, request)

    return commit_config_change(config, config_path, expected_revision=revision, mutate=mutate, **kwargs)


# ── revision 冲突 ────────────────────────────────────────────────


def test_revision_conflict_returns_config_modified_without_any_write(tmp_path):
    config_path, config = _provider_config(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    mutate_calls: list = []
    published: list = []

    result = commit_config_change(
        config,
        config_path,
        expected_revision="stale-revision",
        mutate=mutate_calls.append,
        on_committed=published.append,
    )

    assert result["ok"] is False
    assert result["code"] == "config_modified"
    assert result["current_revision"] == file_revision(config_path)
    assert mutate_calls == []  # 冲突即短路：mutate 不执行
    assert published == []
    assert config_path.read_text(encoding="utf-8") == before_text
    assert _relay_entry(config)["api_key"] == "sk-old"  # 内存未变


def test_commit_on_missing_file_accepts_missing_revision(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    assert file_revision(config_path) == "missing"

    result = commit_config_change(
        config, config_path, expected_revision="missing", mutate=lambda c: None
    )

    assert result["ok"] is True
    assert config_path.is_file()


# ── 写盘失败：磁盘与内存都原样 ───────────────────────────────────


def test_write_failure_keeps_disk_and_memory_untouched(tmp_path, monkeypatch):
    config_path, config = _provider_config(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    published: list = []

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("openbrep.workbench.config_commit.os.replace", boom)

    def mutate(working: GDLAgentConfig) -> None:
        working.llm.model = "glm-4.6"

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=mutate,
        on_committed=published.append,
    )

    assert result["ok"] is False
    assert result["code"] == "config_write_failed"
    assert "disk full" in result["error"]
    assert config_path.read_text(encoding="utf-8") == before_text  # 磁盘仍旧内容
    assert config.llm.model == "glm-4-flash"  # 内存未先行生效
    assert published == []  # 未发布
    assert list(tmp_path.glob("*.tmp")) == []  # 临时文件已清理


def test_serialize_failure_keeps_disk_and_memory_untouched(tmp_path, monkeypatch):
    config_path, config = _provider_config(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    published: list = []

    def boom(self, path):
        raise RuntimeError("serialization exploded")

    monkeypatch.setattr(GDLAgentConfig, "save", boom)

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=lambda c: None,
        on_committed=published.append,
    )

    assert result["ok"] is False
    assert result["code"] == "config_serialize_failed"
    assert config_path.read_text(encoding="utf-8") == before_text
    assert published == []
    assert list(tmp_path.glob("*.tmp")) == []


# ── mutate 校验失败：零副作用 ────────────────────────────────────


def test_mutator_error_returns_structured_code_without_side_effects(tmp_path):
    config_path, config = _provider_config(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    published: list = []

    def mutate(working: GDLAgentConfig) -> None:
        raise ConfigCommitError("name_conflict", "名称已存在")

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=mutate,
        on_committed=published.append,
    )

    assert result == {"ok": False, "code": "name_conflict", "error": "名称已存在"}
    assert config_path.read_text(encoding="utf-8") == before_text
    assert published == []


def test_mutator_valueerror_maps_to_invalid_request(tmp_path):
    config_path, config = _provider_config(tmp_path)

    def mutate(working: GDLAgentConfig) -> None:
        raise ValueError("未知 api_mode 'grpc'")

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=mutate,
    )

    assert result["ok"] is False
    assert result["code"] == "invalid_request"
    assert "api_mode" in result["error"]


# ── 成功路径：发布已落盘副本，原配置对象不动 ─────────────────────


def test_success_publishes_committed_copy_and_keeps_original_object(tmp_path):
    config_path, config = _provider_config(tmp_path)
    published: list = []

    def mutate(working: GDLAgentConfig) -> None:
        working.llm.model = "glm-4.6"

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=mutate,
        on_committed=published.append,
    )

    assert result["ok"] is True
    assert result["revision"] == file_revision(config_path)
    assert config.llm.model == "glm-4-flash"  # 原 in-memory 对象未被 mutate 触碰
    assert len(published) == 1
    assert published[0].llm.model == "glm-4.6"  # 发布的是已落盘的规范化副本
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "glm-4.6"


# ── key 三态 ─────────────────────────────────────────────────────


def test_api_key_absent_in_request_keeps_existing(tmp_path):
    config_path, config = _provider_config(tmp_path)

    result = _update_relay(config_path, config, {"name": "relay"})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert _relay_entry(reloaded)["api_key"] == "sk-old"


def test_api_key_explicit_empty_clears(tmp_path):
    config_path, config = _provider_config(tmp_path)

    result = _update_relay(config_path, config, {"name": "relay", "api_key": ""})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert _relay_entry(reloaded)["api_key"] == ""


def test_api_key_nonempty_replaces(tmp_path):
    config_path, config = _provider_config(tmp_path)

    result = _update_relay(config_path, config, {"name": "relay", "api_key": " sk-new "})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert _relay_entry(reloaded)["api_key"] == "sk-new"


# ── 字段覆盖合同：未提交的高级字段原样保留 ───────────────────────


def test_unsubmitted_advanced_fields_survive_round_trip(tmp_path):
    config_path, config = _provider_config(
        tmp_path,
        entry_extra={
            "temperature": 0.6,
            "extra_body": {"thinking": {"type": "disabled"}},
            "credentials": [{"id": "c1", "value": "sk-pool"}],
        },
    )

    result = _update_relay(config_path, config, {"name": "relay", "api_key": "sk-new"})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    entry = _relay_entry(reloaded)
    assert entry["api_key"] == "sk-new"
    assert entry["temperature"] == 0.6
    assert entry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert entry["credentials"] == [{"id": "c1", "value": "sk-pool"}]


# ── 端点显式空 vs 缺省（_explicit_base 语义）────────────────────


def test_endpoint_absent_in_request_allows_top_level_fallback(tmp_path):
    config_path, config = _provider_config(tmp_path)
    config.llm.api_base = "https://fallback.example/v1"
    config.save(str(config_path))
    config = GDLAgentConfig.load(str(config_path))

    request = {"name": "fresh", "api_key": "sk-1", "models": ["m1"]}  # 无 api 键

    def mutate(working: GDLAgentConfig) -> None:
        entry = {"name": "fresh", "api_key": "sk-1", "models": ["m1"]}
        apply_endpoint_update(entry, request)
        working.llm.providers.append(entry)

    result = commit_config_change(
        config,
        config_path,
        expected_revision=file_revision(config_path),
        mutate=mutate,
    )

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.resolve_api_base("fresh/m1") == "https://fallback.example/v1"


def test_endpoint_explicit_empty_never_falls_back_to_top_level(tmp_path):
    config_path, config = _provider_config(tmp_path)
    config.llm.api_base = "https://fallback.example/v1"
    config.save(str(config_path))
    config = GDLAgentConfig.load(str(config_path))

    result = _update_relay(config_path, config, {"name": "relay", "api": ""})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.resolve_api_base("relay/relay-model") is None


def test_endpoint_absent_on_update_keeps_existing_explicit_base(tmp_path):
    """更新缺省 api：已有条目的 api 与显式空语义都原样保留。"""
    config_path, config = _provider_config(tmp_path, entry_extra={"api": ""})
    assert config.llm.resolve_api_base("relay/relay-model") is None

    result = _update_relay(config_path, config, {"name": "relay", "api_key": "sk-new"})

    assert result["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert _relay_entry(reloaded)["api"] == ""
    assert reloaded.llm.resolve_api_base("relay/relay-model") is None


# ── 指纹同源 ─────────────────────────────────────────────────────


def test_file_revision_matches_settings_service_fingerprint(tmp_path):
    config_path, _ = _make_config(tmp_path)
    session = SimpleNamespace(config=None, config_path=config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None)

    assert service.config_revision()["revision"] == file_revision(config_path)
    assert service.config_revision()["revision"] != "missing"


def test_file_revision_missing_file(tmp_path):
    assert file_revision(tmp_path / "nope.toml") == "missing"


# ── resolve_api_key_update 纯函数直测 ────────────────────────────


def test_resolve_api_key_update_three_states():
    assert resolve_api_key_update("sk-old", {"name": "x"}) == "sk-old"  # 缺省 = 保持
    assert resolve_api_key_update("sk-old", {"api_key": ""}) == ""  # 显式空 = 清除
    assert resolve_api_key_update("sk-old", {"api_key": "sk-new"}) == "sk-new"  # 非空 = 替换
    assert resolve_api_key_update(None, {"api_key": "sk-new"}) == "sk-new"
    assert resolve_api_key_update(None, {}) == ""  # 无现有、缺省 = 空


if __name__ == "__main__":
    pytest.main([__file__])
