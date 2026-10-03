"""卡08：模型发现服务测试（fake transport，CI 离线）。"""

import json
import threading

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.workbench.model_discovery import (
    MAX_ITEMS,
    build_models_url,
    discover,
    discover_provider_models,
)


# ── 端点拼接规则（表驱动）────────────────────────────────────────


@pytest.mark.parametrize(
    ("api", "api_mode", "expected"),
    [
        # OpenAI 兼容：{api}/models，防重复
        ("https://api.example.com", "chat_completions", "https://api.example.com/models"),
        ("https://api.example.com/", "chat_completions", "https://api.example.com/models"),
        ("https://api.example.com/v1", "chat_completions", "https://api.example.com/v1/models"),
        ("https://api.example.com/v1/", "responses", "https://api.example.com/v1/models"),
        ("https://api.example.com/v1/models", "chat_completions", "https://api.example.com/v1/models"),
        # 代理前缀原样保留
        ("https://gw.example/openai", "chat_completions", "https://gw.example/openai/models"),
        ("https://gw.example/proxy/openai/v1", "responses", "https://gw.example/proxy/openai/v1/models"),
        # Anthropic：{api}/v1/models；带 /v1 只补 /models；防重复 /v1/v1
        ("https://api.anthropic.com", "anthropic_messages", "https://api.anthropic.com/v1/models"),
        ("https://api.anthropic.com/v1", "anthropic_messages", "https://api.anthropic.com/v1/models"),
        ("https://api.anthropic.com/v1/models", "anthropic_messages", "https://api.anthropic.com/v1/models"),
        ("https://api.anthropic.com/", "anthropic_messages", "https://api.anthropic.com/v1/models"),
        # 空 api → 空 url（由调用方报 protocol）
        ("", "chat_completions", ""),
    ],
)
def test_build_models_url_table(api, api_mode, expected):
    assert build_models_url(api, api_mode) == expected


# ── 成功路径 ─────────────────────────────────────────────────────


def test_discover_openai_compatible_one_shot():
    def transport(method, url, headers, timeout):
        assert url == "https://api.example.com/v1/models"
        assert headers["Authorization"] == "Bearer test-key-0000000"
        assert method == "GET"
        return 200, json.dumps({"object": "list", "data": [{"id": "m-1"}, {"id": "m-2"}, {"id": "m-1"}]})

    result = discover("https://api.example.com/v1/models", {"Authorization": "Bearer test-key-0000000"}, transport=transport)

    assert result["ok"] is True
    assert result["models"] == ["m-1", "m-2"]  # 去重保序
    assert result["raw_count"] == 3
    assert result["truncated"] is False
    assert result["page_count"] == 1


def test_discover_anthropic_pagination():
    pages = {
        0: {"data": [{"id": "a"}, {"id": "b"}], "has_more": True, "last_id": "b"},
        1: {"data": [{"id": "c"}], "has_more": True, "last_id": "c"},
        2: {"data": [{"id": "d"}], "has_more": False, "last_id": "d"},
    }

    def transport(method, url, headers, timeout):
        assert headers.get("x-api-key") == "test-key-0000000"
        assert headers.get("anthropic-version")
        page = pages[len(_calls)]
        _calls.append(url)
        return 200, json.dumps(page)

    _calls: list[str] = []
    result = discover(
        "https://api.anthropic.com/v1/models",
        {"x-api-key": "test-key-0000000", "anthropic-version": "2023-06-01"},
        transport=transport,
    )

    assert result["ok"] is True
    assert result["models"] == ["a", "b", "c", "d"]
    assert result["page_count"] == 3
    # 第 2/3 页带 after_id 游标
    assert "after_id=b" in _calls[1]
    assert "after_id=c" in _calls[2]


def test_discover_truncates_at_cap():
    big = {"data": [{"id": f"m-{i}"} for i in range(MAX_ITEMS + 50)]}

    result = discover(
        "https://api.example.com/models",
        {},
        transport=lambda *_args: (200, json.dumps(big)),
    )

    assert result["ok"] is True
    assert len(result["models"]) == MAX_ITEMS
    assert result["raw_count"] == MAX_ITEMS + 1  # 到达上限即停（差 1 计数到达）
    assert result["truncated"] is True


def test_discover_max_pages_cap():
    _calls: list[int] = []

    def counting_transport(method, url, headers, timeout):
        _calls.append(1)
        return 200, json.dumps({"data": [{"id": f"p{len(_calls)}"}], "has_more": True, "last_id": "x"})

    result = discover("https://x.example/models", {}, transport=counting_transport)

    assert result["page_count"] == 5
    assert result["truncated"] is False  # 分页自然结束于 max_pages


# ── 失败路径（七类）──────────────────────────────────────────────


def test_discover_auth_error():
    result = discover(
        "https://x.example/models",
        {"Authorization": "Bearer test-key-0000000"},
        transport=lambda *_a: (401, '{"error":{"message":"bad key test-key-0000000"}}'),
    )

    assert result["ok"] is False
    assert result["category"] == "auth"
    # 脱敏回归：响应体回显的 key 不出现在 message/fix_hint
    assert "test-key-0000000" not in result["message"]
    assert "test-key-0000000" not in result["fix_hint"]


def test_discover_not_found_hint_mentions_v1():
    result = discover("https://x.example/models", {}, transport=lambda *_a: (404, "nope"))

    assert result["ok"] is False
    assert result["category"] == "not_found"
    assert "/v1" in result["fix_hint"]
    assert "不代表服务商不可用" in result["fix_hint"]


def test_discover_timeout_error():
    def transport(method, url, headers, timeout):
        raise TimeoutError("request timed out")

    result = discover("https://x.example/models", {}, transport=transport)

    assert result["category"] == "timeout"


def test_discover_network_error():
    def transport(method, url, headers, timeout):
        raise ConnectionError("getaddrinfo failed")

    result = discover("https://x.example/models", {}, transport=transport)

    assert result["category"] == "network"


def test_discover_protocol_error_bad_json():
    result = discover("https://x.example/models", {}, transport=lambda *_a: (200, "<html>not json</html>"))

    assert result["category"] == "protocol"


def test_discover_protocol_error_wrong_shape():
    result = discover("https://x.example/models", {}, transport=lambda *_a: (200, '{"unexpected": true}'))

    assert result["category"] == "protocol"


def test_discover_rate_limited():
    result = discover("https://x.example/models", {}, transport=lambda *_a: (429, "slow down"))

    assert result["category"] == "rate_limited"


def test_discover_server_error_unknown():
    result = discover("https://x.example/models", {}, transport=lambda *_a: (500, "boom"))

    assert result["category"] == "unknown"


# ── 路由入口：{name} 与草稿两条路径 ──────────────────────────────


def _config() -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.providers.append({
        "name": "relay",
        "api": "https://relay.example/v1",
        "api_mode": "chat_completions",
        "api_key": "${RELAY_DISCOVER_KEY}",
        "models": ["relay-main"],
    })
    config.llm.providers.append({
        "name": "pooled",
        "api": "https://pooled.example/v1",
        "api_mode": "chat_completions",
        "credentials": [{"id": "c1", "value": "test-pool-key-001"}],
        "models": [],
    })
    return config


def test_route_by_name_reads_entry_with_env_interpolation(monkeypatch):
    monkeypatch.setenv("RELAY_DISCOVER_KEY", "test-env-key-00001")
    seen: dict[str, str] = {}

    def transport(method, url, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization", "")
        return 200, json.dumps({"data": [{"id": "m"}]})

    import openbrep.workbench.model_discovery as md

    original = md._default_transport
    md._default_transport = transport
    try:
        result = discover_provider_models(_config(), {"name": "relay"})
    finally:
        md._default_transport = original

    assert result["ok"] is True
    assert result["provider"] == "relay"
    assert seen["url"] == "https://relay.example/v1/models"
    assert seen["auth"] == "Bearer test-env-key-00001"


def test_route_by_name_pool_entry_never_selects(monkeypatch):
    from openbrep.credential_pool import CredentialPool

    select_calls: list = []
    monkeypatch.setattr(
        CredentialPool, "select", lambda self, scope="default", **kwargs: select_calls.append(scope)
    )

    def transport(method, url, headers, timeout):
        return 200, json.dumps({"data": []})

    import openbrep.workbench.model_discovery as md

    original = md._default_transport
    md._default_transport = transport
    try:
        result = discover_provider_models(_config(), {"name": "pooled"})
    finally:
        md._default_transport = original

    assert result["ok"] is True
    assert select_calls == []


def test_route_by_name_missing_entry():
    result = discover_provider_models(_config(), {"name": "ghost"})

    assert result["ok"] is False
    assert result["category"] == "not_found"


def test_route_draft_key_discarded_and_config_untouched():
    config = _config()
    seen: dict[str, str] = {}

    def transport(method, url, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization", "")
        return 200, json.dumps({"data": [{"id": "m"}]})

    import openbrep.workbench.model_discovery as md

    original = md._default_transport
    md._default_transport = transport
    try:
        result = discover_provider_models(config, {
            "api": "https://draft.example/v1",
            "api_mode": "chat_completions",
            "api_key": "test-draft-key-0001",
        })
    finally:
        md._default_transport = original

    assert result["ok"] is True
    assert seen["url"] == "https://draft.example/v1/models"
    assert seen["auth"] == "Bearer test-draft-key-0001"
    # 配置零写入：key 用完即弃
    assert all(p.get("api_key") != "test-draft-key-0001" for p in config.llm.providers)
    assert len(config.llm.providers) == 2


def test_route_rejects_codex_and_unknown_api_mode():
    config = _config()

    result = discover_provider_models(config, {"api": "https://x", "api_mode": "grpc"})
    assert result["ok"] is False
    assert result["category"] == "protocol"

    result = discover_provider_models(config, {"api": "https://x", "api_mode": "codex_app_server"})
    assert result["ok"] is False
    assert result["category"] == "protocol"


def test_route_no_lock_concurrent_discovery():
    """无锁合同下的并发不互扰：两请求并行各拿各的 fake transport 结果。"""
    def make_transport(tag: str):
        def transport(method, url, headers, timeout):
            return 200, json.dumps({"data": [{"id": tag}]})
        return transport

    import openbrep.workbench.model_discovery as md

    results: dict[str, dict] = {}

    def run(tag: str):
        results[tag] = discover("https://x.example/models", {}, transport=make_transport(tag))

    original = md._default_transport
    try:
        threads = [threading.Thread(target=run, args=(f"t{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        md._default_transport = original

    assert [results[f"t{i}"]["models"] for i in range(2)] == [["t0"], ["t1"]]


if __name__ == "__main__":
    pytest.main([__file__])
