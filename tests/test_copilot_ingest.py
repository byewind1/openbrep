"""E1: Copilot 错误自动沉淀（剪贴板监听旁路 + 手动 ingest-error 入口）。

验证命令：``python -m pytest tests/test_copilot_ingest.py tests/test_copilot_service.py -q``

覆盖：
- 新错误写入全局错题本（tmp path 隔离），字段齐全
- 同指纹重复沉淀 → count 递增、不产生重复条目、last_seen 更新
- 行号/路径/数字归一化后同指纹（line 12 与 line 42 合并）
- ingest_error 带 code_context → example 字段留存
- 写盘失败（patch 模拟）不影响 chat / 剪贴板 buffer 主路径
- 路由可达 + 在 LOCK_FREE_POST_ROUTES 中
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import openbrep.workbench.copilot_service as copilot_service
from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse
from openbrep.workbench.copilot_service import WorkbenchCopilotService
from openbrep.workbench.request_gate import LOCK_FREE_POST_ROUTES, is_lock_free_route
from openbrep.workbench_api import WorkbenchSession


def _load_lessons(path):
    """读 jsonl 错题本 → list[dict]（与 error_harvest 的读取方式同构）。"""
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


@pytest.fixture
def global_lessons_path(tmp_path, monkeypatch):
    """全局错题本路径隔离：~/.openbrep/error_lessons.jsonl → tmp_path。"""
    path = tmp_path / "error_lessons.jsonl"
    monkeypatch.setattr(copilot_service, "GLOBAL_ERROR_LESSONS_PATH", path)
    return path


# ── 手动沉淀：新错误写入 + 字段齐全 ───────────────────────────────


def test_ingest_error_writes_new_lesson_with_complete_fields(service, global_lessons_path):
    result = service.ingest_error({
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })

    assert result["ok"] is True
    assert result["created"] is True
    assert result["count"] == 1
    assert result["fingerprint"].startswith("variable_mapping:")

    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1
    lesson = lessons[0]
    for field in (
        "fingerprint",
        "category",
        "summary",
        "guidance",
        "example",
        "count",
        "first_seen",
        "last_seen",
        "source",
        "raw_excerpt",
    ):
        assert field in lesson, f"missing field: {field}"
    assert lesson["source"] == "copilot_manual"
    assert lesson["category"] == "variable_mapping"
    assert lesson["count"] == 1
    assert lesson["summary"]
    assert lesson["guidance"]
    assert lesson["raw_excerpt"] == "Error in 3D script, line 12: Undefined variable width"
    assert lesson["first_seen"] == lesson["last_seen"]


def test_ingest_error_requires_error_text(service):
    assert service.ingest_error({}) == {
        "ok": False,
        "error": "error_text is required.",
        "status": 400,
    }
    assert service.ingest_error({"error_text": "   "})["status"] == 400


def test_ingest_error_truncates_raw_excerpt_to_500_chars(service, global_lessons_path):
    long_error = "Error in 3D script, line 12: Undefined variable width " + "x" * 600
    result = service.ingest_error({"error_text": long_error})

    assert result["ok"] is True
    lesson = _load_lessons(global_lessons_path)[0]
    assert len(lesson["raw_excerpt"]) == 500
    assert lesson["raw_excerpt"].startswith("Error in 3D script, line 12")


# ── 同指纹去重：count 递增 / 无重复条目 / last_seen 更新 ──────────


class _FakeDatetime:
    """可拨动的 datetime 替身（copilot_service.datetime.now().isoformat(...)）。"""

    current = "2026-08-13T10:00:00"

    @classmethod
    def now(cls):
        return cls

    @classmethod
    def isoformat(cls, timespec="seconds"):
        return cls.current


def test_repeat_ingest_increments_count_without_duplicate_and_updates_last_seen(
    service, global_lessons_path, monkeypatch
):
    monkeypatch.setattr(copilot_service, "datetime", _FakeDatetime)

    first = service.ingest_error({
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })
    _FakeDatetime.current = "2026-08-13T11:30:45"
    second = service.ingest_error({
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })

    assert first["ok"] is True and second["ok"] is True
    assert first["fingerprint"] == second["fingerprint"]
    assert first["created"] is True
    assert second["created"] is False
    assert second["count"] == 2

    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1  # 不产生重复条目
    lesson = lessons[0]
    assert lesson["count"] == 2
    assert lesson["first_seen"] == "2026-08-13T10:00:00"
    assert lesson["last_seen"] == "2026-08-13T11:30:45"  # last_seen 已更新


# ── 指纹归一化：行号/路径/数字不同 → 同指纹合并 ───────────────────


def test_line_number_normalization_merges_same_error(service, global_lessons_path):
    first = service.ingest_error({
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })
    second = service.ingest_error({
        "error_text": "Error in 3D script, line 42: Undefined variable width",
    })

    assert first["fingerprint"] == second["fingerprint"]
    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1
    assert lessons[0]["count"] == 2


def test_path_and_number_normalization_merges_same_error(service, global_lessons_path):
    first = service.ingest_error({
        "error_text": (
            "Error in /Users/alice/proj/chair.gdl, line 12: "
            "Not enough parameters (3 vs 5)"
        ),
    })
    second = service.ingest_error({
        "error_text": (
            "Error in /Users/bob/other/table.gdl, line 99: "
            "Not enough parameters (1 vs 7)"
        ),
    })

    assert first["ok"] is True and second["ok"] is True
    assert first["fingerprint"] == second["fingerprint"]
    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1
    assert lessons[0]["count"] == 2


# ── code_context → example 字段 ────────────────────────────────────


def test_ingest_error_stores_code_context_as_example(service, global_lessons_path):
    context = "PRISM_ 4, 1+2, 0, 1, 0, -1, 0, 0"
    result = service.ingest_error({
        "error_text": "Error in 3D script, line 7: Not enough parameters",
        "code_context": context,
    })

    assert result["ok"] is True
    lesson = _load_lessons(global_lessons_path)[0]
    assert lesson["example"] == context


# ── 剪贴板监听自动沉淀（旁路） ────────────────────────────────────


def test_clipboard_watch_auto_precipitates_new_error(service, global_lessons_path, monkeypatch):
    error_text = "Error in 3D script, line 12: Undefined variable width"
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: (error_text, "pbpaste"),
    )

    service._clipboard_watch_iteration()

    # buffer 主路径照常
    assert service.clipboard_buffer()["items"] == [error_text]
    # 自动沉淀已落盘
    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1
    assert lessons[0]["source"] == "copilot_clipboard"
    assert lessons[0]["raw_excerpt"] == error_text
    assert lessons[0]["count"] == 1


def test_clipboard_watch_same_signature_not_reprecipitated(
    service, global_lessons_path, monkeypatch
):
    error_text = "Error in 3D script, line 12: Undefined variable width"
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: (error_text, "pbpaste"),
    )

    service._clipboard_watch_iteration()
    service._clipboard_watch_iteration()  # 相同签名 → 去重，不重复沉淀

    lessons = _load_lessons(global_lessons_path)
    assert len(lessons) == 1
    assert lessons[0]["count"] == 1


def test_clipboard_watch_filters_non_error_text_without_precipitation(
    service, global_lessons_path, monkeypatch
):
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: ("建筑师随便复制的一段普通文本", "pbpaste"),
    )

    service._clipboard_watch_iteration()

    assert service.clipboard_buffer()["items"] == []
    assert _load_lessons(global_lessons_path) == []


# ── 写盘失败不影响主路径 ──────────────────────────────────────────


def test_write_failure_does_not_break_chat_and_buffer(
    session, service, global_lessons_path, monkeypatch
):
    def boom_write(self, lessons):
        raise OSError("disk full")

    monkeypatch.setattr(copilot_service.ErrorLearningStore, "_write_lessons", boom_write)

    # 手动沉淀：失败返回 500，不抛异常
    result = service.ingest_error({
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })
    assert result["ok"] is False
    assert result["status"] == 500
    assert "错题本写入失败" in result["error"]

    # chat 主路径不受影响
    class FakeLLM:
        def __init__(self, config):
            self.config = config

        def generate(self, messages, **kwargs):
            return LLMResponse(content="```gdl\nGOSUB 100\n```", model="test-model")

    monkeypatch.setattr(copilot_service, "LLMAdapter", FakeLLM)
    chat_result = service.chat({"message": "hi"})
    assert chat_result["ok"] is True
    assert chat_result["code_blocks"] == ["GOSUB 100"]

    # 剪贴板 buffer 主路径不受影响（自动沉淀失败只记日志）
    monkeypatch.setattr(
        copilot_service,
        "_read_clipboard_snapshot",
        lambda: ("line 3: error near GOSUB", "pbpaste"),
    )
    service._clipboard_watch_iteration()  # 不应抛异常
    assert service.clipboard_buffer()["items"] == ["line 3: error near GOSUB"]


# ── 路由可达 + lock-free 登记 ─────────────────────────────────────


def test_ingest_error_route_reachable_via_session_without_project(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_service.platform, "system", lambda: "Linux")
    global_path = tmp_path / "error_lessons.jsonl"
    monkeypatch.setattr(copilot_service, "GLOBAL_ERROR_LESSONS_PATH", global_path)
    # 全新 WorkbenchSession：无项目打开
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    assert session.project is None

    result = session.route("POST", "/api/copilot/ingest-error", {
        "error_text": "Error in 3D script, line 12: Undefined variable width",
        "code_context": "GOSUB 100",
    })

    assert result["ok"] is True
    lessons = _load_lessons(global_path)
    assert len(lessons) == 1
    assert lessons[0]["source"] == "copilot_manual"
    assert lessons[0]["example"] == "GOSUB 100"


def test_ingest_error_route_reachable_via_copilot_service_route(service, global_lessons_path):
    result = service.route("POST", "/api/copilot/ingest-error", {
        "error_text": "Error in 3D script, line 12: Undefined variable width",
    })
    assert result["ok"] is True
    assert len(_load_lessons(global_lessons_path)) == 1


def test_ingest_error_route_registered_lock_free():
    assert "/api/copilot/ingest-error" in LOCK_FREE_POST_ROUTES
    assert is_lock_free_route("POST", "/api/copilot/ingest-error") is True
