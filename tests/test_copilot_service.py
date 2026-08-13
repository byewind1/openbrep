"""T1: WorkbenchCopilotService（移植自 ADDON copilot/server.py）行为测试。

验证命令：``python -m pytest tests/test_copilot_service.py -q``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openbrep.workbench.copilot_service as copilot_service
from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse
from openbrep.workbench.copilot_service import (
    WorkbenchCopilotService,
    _extract_gdl_code_blocks,
    _is_error_clipboard_text,
)


@pytest.fixture
def session(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    return SimpleNamespace(config=config, config_path=str(tmp_path / "config.toml"))


@pytest.fixture
def service(session):
    return WorkbenchCopilotService(session)


@pytest.fixture(autouse=True)
def _no_real_clipboard_thread(monkeypatch):
    """测试默认视为非 macOS：不启动真实剪贴板监听线程，buffer 由测试注入。"""
    monkeypatch.setattr(copilot_service.platform, "system", lambda: "Linux")


# ── status ──────────────────────────────────────────────────────────


def test_status_reports_version_and_min_addon(service):
    assert service.status() == {"ok": True, "version": "0.2.0", "min_addon_version": "0.4.0"}


# ── chat 正常路径 ───────────────────────────────────────────────────


def test_chat_ok_returns_reply_and_code_blocks(session, service, monkeypatch):
    captured: list[list[dict]] = []

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            captured.append(messages)
            return LLMResponse(
                content="问题原因一句话。\n\n```gdl\nGOSUB 100\n```\n\n```gdl\nEND\n```",
                model="test-model",
            )

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.chat({
        "message": "修复这个脚本",
        "history": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请贴代码"},
        ],
        "images": [{"b64": "AAAA", "mime": "image/png"}],
    })

    assert result["ok"] is True
    assert "问题原因一句话" in result["reply"]
    assert result["code_blocks"] == ["GOSUB 100", "END"]
    # system + 2 条历史 + 1 条带图 user
    assert len(captured[0]) == 4
    user_content = captured[0][-1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,AAAA")
    assert user_content[-1] == {"type": "text", "text": "修复这个脚本"}


def test_chat_empty_message_returns_validation_error(service):
    result = service.chat({"message": "   "})
    assert result["ok"] is False
    assert result["status"] == 400
    assert "message is required" in result["error"]


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"message": "hi", "history": [1]}, "history items must be objects"),
        ({"message": "hi", "images": [1]}, "image items must be objects"),
    ],
)
def test_chat_rejects_non_object_history_and_image_items(service, body, error):
    result = service.chat(body)
    assert result == {"ok": False, "error": f"{error}.", "status": 400}


# ── chat LLM 错误映射 ──────────────────────────────────────────────


def test_chat_auth_error_maps_to_status_400_with_config_error(service, monkeypatch):
    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            raise RuntimeError("LLM 配置错误：当前模型 `x` 未找到可用 API Key。")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.chat({"message": "hi"})
    assert result["ok"] is False
    assert result["status"] == 400
    assert "配置错误" in result["error"]


def test_chat_runtime_error_maps_to_status_503(service, monkeypatch):
    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.chat({"message": "hi"})
    assert result["ok"] is False
    assert result["status"] == 503
    assert result["error"] == "provider exploded"


def test_chat_route_unavailable_falls_back_to_fallback_models(session, service, monkeypatch):
    used_models: list[str | None] = []

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            used_models.append(kwargs.get("model"))
            if kwargs.get("model"):
                return LLMResponse(content="备用模型回复", model="moonshotai/kimi-k2.5")
            raise RuntimeError("model_not_found")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.chat({"message": "hi"})
    assert result["ok"] is True
    assert result["reply"] == "备用模型回复"
    assert used_models == [None, "moonshotai/kimi-k2.5"]


def test_chat_transient_upstream_error_maps_to_status_503(service, monkeypatch):
    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            raise ValueError("upstream timeout")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.chat({"message": "hi"})
    assert result["ok"] is False
    assert result["status"] == 503
    assert "当前模型暂不可用" in result["error"]


# ── 剪贴板 buffer：增 / 清 / 错误正则过滤 ──────────────────────────


def test_clipboard_buffer_ingests_error_text_and_dedupes(service, monkeypatch):
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: ("line 3: error near GOSUB", "pbpaste"),
    )
    service._clipboard_watch_iteration()
    service._clipboard_watch_iteration()  # 相同签名 → 去重
    assert service.clipboard_buffer()["items"] == ["line 3: error near GOSUB"]


def test_clipboard_buffer_filters_non_error_text(service, monkeypatch):
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: ("建筑师随便复制的一段普通文本", "pbpaste"),
    )
    service._clipboard_watch_iteration()
    assert service.clipboard_buffer()["items"] == []


def test_error_clipboard_pattern_matches_chinese_keywords():
    assert _is_error_clipboard_text("第2行 错误：参数未定义")
    assert _is_error_clipboard_text("警告：对象丢失")
    assert _is_error_clipboard_text("a.gsm / b.gdl / line 1 / warning / error")
    assert not _is_error_clipboard_text("普通文本")


def test_clipboard_buffer_clear_filters_by_error_pattern(service):
    result = service.clipboard_buffer_clear({
        "items": ["line 1: warning x.gdl", "普通文本不匹配", "错误：xxx", "  "],
    })
    assert result["ok"] is True
    assert result["items"] == ["line 1: warning x.gdl", "错误：xxx"]


def test_clipboard_buffer_clear_without_items_empties_buffer(service):
    service._clipboard_buffer.append("line 9: error")
    result = service.clipboard_buffer_clear({})
    assert result == {"ok": True, "items": []}
    assert service.clipboard_buffer()["items"] == []


# ── summarize-errors ────────────────────────────────────────────────


def test_summarize_errors_empty_buffer_returns_empty_summary(service):
    assert service.summarize_errors() == {"ok": True, "summary": ""}


def test_summarize_errors_uses_llm_and_clears_buffer(session, service, monkeypatch):
    service._clipboard_buffer.append("line 1: error A")
    service._clipboard_buffer.append("line 2: error B")

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            return LLMResponse(content="第1、2行存在错误", model="test-model")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.summarize_errors()
    assert result["ok"] is True
    assert result["summary"] == "第1、2行存在错误"
    assert service.clipboard_buffer()["items"] == []  # 消费后清空


def test_summarize_errors_fallback_when_llm_fails(session, service, monkeypatch):
    service._clipboard_buffer.append("line 5: error X")
    service._clipboard_buffer.append("line 9: warning Y")

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.summarize_errors()
    assert result["ok"] is True
    assert result["summary"] == "line 5: error X；line 9: warning Y"


def test_summarize_errors_fallback_truncates_to_100_chars(session, service, monkeypatch):
    service._clipboard_buffer.append("x" * 80)
    service._clipboard_buffer.append("y" * 80)

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)

    result = service.summarize_errors()
    assert result["ok"] is True
    assert len(result["summary"]) == 100


# ── 平台相关 ───────────────────────────────────────────────────────


def test_non_macos_buffer_stays_empty_without_exception(session, monkeypatch):
    monkeypatch.setattr(copilot_service.platform, "system", lambda: "Linux")
    service = WorkbenchCopilotService(session)

    assert service.status()["ok"] is True
    assert service.clipboard_buffer() == {"ok": True, "items": []}
    assert service.summarize_errors() == {"ok": True, "summary": ""}
    assert service._clipboard_thread is None


def test_macos_lazy_starts_daemon_thread_and_keeps_buffer_empty_when_unreadable(
    session, monkeypatch
):
    monkeypatch.setattr(copilot_service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(copilot_service, "_read_clipboard_snapshot", lambda: ("", "pbpaste"))
    service = WorkbenchCopilotService(session)

    assert service._clipboard_thread is None
    service.status()  # 首次任意方法调用时懒启动
    assert service._clipboard_thread is not None
    assert service._clipboard_thread.is_alive()
    assert service._clipboard_thread.daemon is True
    # 两者都不可用 → buffer 恒空
    assert service.clipboard_buffer()["items"] == []
    assert service.summarize_errors()["summary"] == ""


# ── 代码块提取工具 ─────────────────────────────────────────────────


def test_extract_gdl_code_blocks_handles_case_and_whitespace():
    text = "A\n```GDL\n   GOSUB 100   \n```\nB"
    assert _extract_gdl_code_blocks(text) == ["GOSUB 100"]
    assert _extract_gdl_code_blocks("no fences") == []
