"""RF04（返修派单 P1）：保存所有聊天结果与任务关联，异常可复盘。

后端契约：
- GET /api/assistant/turn/events 列出任务索引（不依赖前端末次 save）：
  turn_id、started_at、last_kind/state、terminal、message 摘要、run_id；
- 进程退出后未终止的任务可被前端发现并展示"已开始未结束"，不伪造最终答复；
- task_ref 只用于展示与复盘，不进入 LLM history。
"""

from __future__ import annotations

import json
from pathlib import Path


def test_turn_index_lists_turns_with_terminal_flags(tmp_path):
    session = _session(tmp_path)
    ready, done = _prepare_and_execute(session, client_id="c1")
    listing = session.route("GET", "/api/assistant/turn/events", {})
    assert listing["ok"]
    turns = {t["turn_id"]: t for t in listing["turns"]}
    assert ready["turn_id"] in turns
    entry = turns[ready["turn_id"]]
    assert entry["terminal"] is True
    assert entry["last_kind"] in {"completed", "failed", "cancelled"}
    assert entry["started_at"]
    assert "添加背板" in (entry.get("message") or "")


def test_unterminated_turn_is_discoverable_after_restart(tmp_path):
    """进程退出场景：turn 有事件但未终止——索引标 terminal=False，
    last_kind 为最后已发生事件；读取端不得推测 completed。"""
    session = _session(tmp_path)
    from openbrep.workbench.task_event_service import WorkbenchTaskEventService

    service = session.task_event_service
    service.begin_turn("t-crash", project_epoch=1, message="把A改成2")
    service.handle_pipeline_event("t-crash", "tool_started",
                                  {"tool": "update_script", "tool_call_id": "c1"})
    # 模拟进程退出：直接重建服务（内存丢失，仅存盘记录）
    fresh = WorkbenchTaskEventService(session)
    listing = fresh.list_turns()
    entry = next(t for t in listing if t["turn_id"] == "t-crash")
    assert entry["terminal"] is False
    assert entry["last_kind"] == "tool_started"
    assert "把A改成2" in (entry.get("message") or "")


def test_turn_list_does_not_include_empty_directory_noise(tmp_path):
    session = _session(tmp_path)
    listing = session.route("GET", "/api/assistant/turn/events", {})
    assert listing["ok"] and listing["turns"] == []


def _session(tmp_path):
    from tests.test_conversation_service import session_at

    return session_at(Path(tmp_path))


def _prepare_and_execute(session, message="添加背板", client_id="c1"):
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": client_id, "message": message,
        "project_epoch": session.project_epoch,
    })
    assert ready["result_kind"] == "ready_to_execute"
    done = session.route("POST", "/api/assistant/turn", {"phase": "execute", "turn_id": ready["turn_id"]})
    assert done["result_kind"] == "execution"
    return ready, done
