"""卡02：引用检查纯函数测试（与 find_custom_provider_match 解析器对齐）。"""

from types import SimpleNamespace

import pytest

from openbrep.config import GDLAgentConfig, find_custom_provider_match
from openbrep.workbench.provider_refs import (
    find_provider_refs,
    frontend_visibility_hint,
    model_targets_provider,
)


def _providers_relay_first() -> list[dict]:
    return [
        {
            "name": "relay",
            "api": "https://relay.example/v1",
            "api_key": "k",
            "models": ["relay-main", {"alias": "alias-one", "model": "real-one"}],
        },
        {
            "name": "alpha",
            "api": "https://alpha.example/v1",
            "models": [{"alias": "RELAY", "model": "alpha-model"}, "alpha-model"],
        },
    ]


def _config(providers: list[dict] | None = None) -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.providers = providers if providers is not None else _providers_relay_first()
    return config


# ── 五类引用各一例 ───────────────────────────────────────────────


def test_ref_llm_model_qualified_reference():
    config = _config()
    config.llm.model = "relay/real-one"

    refs = find_provider_refs(config, "relay")

    assert [r["location"] for r in refs] == ["llm.model"]
    assert refs[0]["context"] == "relay/real-one"
    assert refs[0]["blocking"] is True


def test_ref_session_model_override():
    config = _config()
    config.llm.model = "glm-4-flash"
    session = SimpleNamespace(session_llm_model="relay/real-one")

    refs = find_provider_refs(config, "relay", session=session)

    assert [r["location"] for r in refs] == ["session_model"]


def test_ref_retry_fallback_chain():
    config = _config()
    config.llm.retry = {"fallback_chains": {"modify": [{"model": "relay/real-one"}]}}

    refs = find_provider_refs(config, "relay")

    assert [r["location"] for r in refs] == ["llm.retry"]
    assert "modify" in refs[0]["detail"]


def test_ref_retry_ignores_invalid_role_via_router_normalization():
    config = _config()
    config.llm.retry = {"fallback_chains": {"bogus_role": [{"model": "relay/real-one"}]}}

    assert find_provider_refs(config, "relay") == []


def test_ref_enabled_models_scoped_and_global_patterns():
    config = _config()
    config.llm.enabled_models = [
        {"path": "/some/workspace", "models": ["relay/real-one"]},
        "relay/*",
    ]

    contexts = [r["context"] for r in find_provider_refs(config, "relay")]

    assert contexts == ["relay/real-one", "relay/*"]
    assert all(r["location"] == "enabled_models" for r in find_provider_refs(config, "relay"))


def test_ref_disabled_providers_case_insensitive():
    config = _config()
    config.llm.disabled_providers = [{"path": "/x", "providers": ["Relay"]}]

    refs = find_provider_refs(config, "relay")

    assert [r["location"] for r in refs] == ["disabled_providers"]


# ── 无引用空列表 ─────────────────────────────────────────────────


def test_no_refs_returns_empty_list():
    config = _config()
    config.llm.model = "glm-4-flash"

    assert find_provider_refs(config, "relay") == []


def test_empty_name_returns_empty_list():
    assert find_provider_refs(_config(), "") == []
    assert find_provider_refs(_config(), "  ") == []


def test_wildcard_allow_pattern_is_not_a_provider_reference():
    config = _config()
    config.llm.enabled_models = ["*", "all"]

    assert find_provider_refs(config, "relay") == []


def test_other_provider_references_are_not_reported():
    config = _config()
    config.llm.model = "alpha/alpha-model"
    config.llm.disabled_providers = ["alpha"]

    refs = find_provider_refs(config, "relay")

    assert refs == []


# ── 大小写与解析器对齐（对照测试：同一比较函数，不许两套规则）────


@pytest.mark.parametrize(
    "spelling",
    [
        "relay",                # 裸 provider 名
        "RELAY",                # 大小写不敏感
        "relay/real-one",       # 显式引用
        "RELAY/REAL-ONE",       # 显式引用大小写不敏感
        "relay-main",           # 裸 alias
        "RELAY-MAIN",
        "ALIAS-ONE",            # alias
    ],
)
def test_ref_detection_matches_parser_positive(spelling):
    config = _config()

    match = find_custom_provider_match(config.llm.providers, spelling)
    assert str(match["provider_name"]).lower() == "relay"

    assert model_targets_provider(config, spelling, "relay") is True
    assert model_targets_provider(config, spelling, "alpha") is False


@pytest.mark.parametrize(
    "spelling",
    [
        "alpha/alpha-model",
        "alpha-model",
        "glm-4-flash",          # 官方模型不指向自定义条目
        "unknown-thing",
        "",
    ],
)
def test_ref_detection_matches_parser_negative(spelling):
    config = _config()

    match = find_custom_provider_match(config.llm.providers, spelling)
    resolved = str(match["provider_name"]).lower() if match else None
    assert resolved != "relay"

    assert model_targets_provider(config, spelling, "relay") is False


def test_alias_collision_follows_parser_config_order():
    """裸名/alias 冲突时解析器按配置顺序先到先得——引用检查跟随同一结果。"""
    relay_first = _config()  # relay 在前：裸 "RELAY" 经名字分支命中 relay
    flipped = _config(list(reversed(_providers_relay_first())))  # alpha 在前：
    # 裸 "RELAY" 先扫到 alpha 的 alias "RELAY" → 解析到 alpha

    assert model_targets_provider(relay_first, "RELAY", "relay") is True
    assert model_targets_provider(relay_first, "RELAY", "alpha") is False
    assert model_targets_provider(flipped, "RELAY", "alpha") is True
    assert model_targets_provider(flipped, "RELAY", "relay") is False

    config_flipped = _config(list(reversed(_providers_relay_first())))
    config_flipped.llm.model = "RELAY"
    assert [r["location"] for r in find_provider_refs(config_flipped, "alpha")] == ["llm.model"]
    assert find_provider_refs(config_flipped, "relay") == []


def test_wildcard_qualified_pattern_via_parser_head_branch():
    """relay/* 经解析器 head 分支（explicit_ref）指向 relay。"""
    config = _config()

    assert model_targets_provider(config, "relay/*", "relay") is True
    assert model_targets_provider(config, "alpha/*", "relay") is False


# ── 前端可见性提示（非阻塞，不入 refs 列表）─────────────────────


def test_frontend_visibility_hint_is_non_blocking():
    hint = frontend_visibility_hint("relay")

    assert hint["location"] == "frontend_visibility"
    assert hint["blocking"] is False
    assert hint["context"] == "relay"


def test_refs_and_hint_are_disjoint_by_design():
    """find_provider_refs 只返回阻塞 refs；提示项由调用方按需并入报告。"""
    config = _config()
    config.llm.model = "relay/real-one"

    refs = find_provider_refs(config, "relay")

    assert all(r["blocking"] is True for r in refs)
    assert all(r["location"] != "frontend_visibility" for r in refs)
    assert frontend_visibility_hint("relay")["blocking"] is False


if __name__ == "__main__":
    pytest.main([__file__])
