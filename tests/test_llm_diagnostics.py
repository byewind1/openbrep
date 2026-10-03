"""卡08：LLM 错误结构化分类测试（纯函数；网络测试一律 fake transport）。"""

import json
import socket

import pytest

from openbrep.workbench.llm_diagnostics import classify_llm_error, redact_secrets


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def test_status_auth():
    result = classify_llm_error(None, '{"error":{"code":"invalid_api_key"}}', status=401)
    assert result["category"] == "auth"
    assert result["fix_hint"]


def test_status_not_found_never_means_provider_down():
    result = classify_llm_error(None, '{"error":"Not Found"}', status=404)
    assert result["category"] == "not_found"
    # 评审 §5：404 不得判定服务商不可用
    assert "不代表服务商不可用" in result["fix_hint"]
    assert "/v1" in result["fix_hint"]


def test_status_rate_limited():
    assert classify_llm_error(None, None, status=429)["category"] == "rate_limited"


def test_status_client_error_is_protocol():
    assert classify_llm_error(None, None, status=400)["category"] == "protocol"


def test_status_server_error_is_unknown():
    assert classify_llm_error(None, None, status=500)["category"] == "unknown"


def test_exception_timeout():
    result = classify_llm_error(TimeoutError("request timed out"))
    assert result["category"] == "timeout"


def test_exception_socket_timeout():
    assert classify_llm_error(socket.timeout("timed out"))["category"] == "timeout"


def test_exception_network():
    assert classify_llm_error(ConnectionError("connection refused"))["category"] == "network"


def test_exception_auth_from_message():
    assert classify_llm_error(RuntimeError("401 Unauthorized"))["category"] == "auth"


def test_exception_response_status_wins():
    exc = RuntimeError("request failed")
    exc.response = _FakeResponse(401, '{"error":"invalid_api_key"}')
    assert classify_llm_error(exc)["category"] == "auth"


def test_exception_protocol_from_bad_json():
    with pytest.raises(json.JSONDecodeError) as exc_info:
        json.loads("<html>not json</html>")
    assert classify_llm_error(exc_info.value)["category"] == "protocol"


def test_seven_categories_have_hints():
    from openbrep.workbench.llm_diagnostics import CATEGORIES, _FIX_HINTS

    for category in CATEGORIES:
        assert _FIX_HINTS[category].strip()


def test_redact_secrets():
    text = 'Authorization: Bearer test-secret-key-0001 failed with test-secret-key-0001'
    redacted = redact_secrets(text, ["test-secret-key-0001"])
    assert "test-secret-key-0001" not in redacted
    assert "***" in redacted


def test_redact_ignores_short_values():
    text = "abc abc"
    assert redact_secrets(text, ["abc"]) == text  # <4 字符不替换（避免误伤）


def test_redact_none_and_empty():
    assert redact_secrets("", []) == ""
    assert redact_secrets("plain", []) == "plain"


if __name__ == "__main__":
    pytest.main([__file__])
