"""RF03（返修派单 P1）：持久化失败可见，内存记录与落盘一致。

原始缺陷：
- task_event_service._persist 捕获 Exception 后 pass——注入 OSError 后
  begin_turn 正常返回、无任何提示；
- root=None 直接 return 且服务不保存事件列表，"只保留会话内存"与实现不符；
- append_terminal 的"检查已有终止"与"追加"分两次加锁——并发重复终止可
  双写；不同终止 kind 的冲突语义未定义；
- 读取容忍最后半行，但继续追加会把新事件接到残行上造成记录丢失。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from openbrep.workbench.task_event_service import WorkbenchTaskEventService
from openbrep.workbench.task_event_store import TaskEventStore


def _service(tmp_path, with_project=True):
    from tests.test_conversation_service import session_at

    session = session_at(Path(tmp_path))
    if not with_project:
        session.project = None
        session.source_path = None
    return session, WorkbenchTaskEventService(session)


def test_persist_failure_is_visible_and_memory_stays_queryable(tmp_path, monkeypatch):
    """磁盘满：任务继续、内存进度可查、查询返回 degraded + 原因；源不回滚。"""
    session, service = _service(tmp_path)
    service.begin_turn("t-disk", project_epoch=1, message="改回纹")

    store = service._store_for(str(session.source_path))
    real_append = store.append

    def failing_append(turn_id, event):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append", failing_append)
    service.handle_pipeline_event("t-disk", "tool_started",
                                  {"tool": "update_script", "tool_call_id": "c1"})
    monkeypatch.setattr(store, "append", real_append)

    result = service.read_turn("t-disk")
    assert result["recording"]["status"] == "degraded", result["recording"]
    assert "disk full" in result["recording"]["error"]
    kinds = [e["kind"] for e in result["events"]]
    assert "accepted" in kinds and "tool_started" in kinds, result["events"]
    # 存储恢复后可继续正常落盘（不因历史失败永久降级）
    service.handle_pipeline_event("t-disk", "preparing", {"stage": "think", "message": "继续"})
    assert service.read_turn("t-disk")["recording"]["status"] == "degraded"


def test_no_project_turn_keeps_memory_events_without_directories(tmp_path):
    """无项目咨询：有会话内存事件（status in_memory），零目录创建。"""
    session, service = _service(tmp_path, with_project=False)
    service.begin_turn("t-chat", project_epoch=0, message="什么是GDL")
    service.handle_pipeline_event("t-chat", "preparing", {"stage": "route", "message": "路由中"})
    service.finish_turn("t-chat", kind="completed", state="advice", message="答")

    result = service.read_turn("t-chat")
    assert result["recording"]["status"] == "in_memory"
    assert [e["kind"] for e in result["events"]] == ["accepted", "preparing", "completed"]
    assert not (Path(tmp_path) / ".openbrep").exists()


def test_persisted_and_broadcast_share_identity(tmp_path):
    """落盘事件与广播事件同一 event_id/seq/timestamp；seq 按 turn 单调。"""
    session, service = _service(tmp_path)
    service.begin_turn("t-id", project_epoch=1, message="改")
    broadcast: list[dict] = []
    for kind, data in (
        ("tool_started", {"tool": "update_script", "tool_call_id": "c1"}),
        ("tool_call", {"tool": "update_script", "ok": True, "summary": "ok", "tool_call_id": "c1"}),
    ):
        for canonical in service.handle_pipeline_event("t-id", kind, data):
            broadcast.append(canonical)
    store = service._store_for(str(session.source_path))
    persisted = store.read_turn("t-id")
    by_seq = {e["seq"]: e for e in persisted}
    for event in broadcast:
        same = by_seq.get(event["seq"])
        assert same is not None
        assert same["event_id"] == event["event_id"]
        assert same["timestamp"] == event["timestamp"]
        assert same["kind"] == event["kind"]
    seqs = [e["seq"] for e in persisted]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_tool_finished_links_call_id_with_duration(tmp_path):
    """tool_finished 保留 tool_call_id 关联 start，并记录 duration_ms/elapsed_ms。"""
    session, service = _service(tmp_path)
    service.begin_turn("t-dur", project_epoch=1, message="改")
    service.handle_pipeline_event("t-dur", "tool_started",
                                  {"tool": "update_script", "tool_call_id": "call-9"})
    service.handle_pipeline_event("t-dur", "tool_call",
                                  {"tool": "update_script", "ok": True, "summary": "ok",
                                   "tool_call_id": "call-9", "duration_ms": 123.4})
    events = service.read_turn("t-dur")["events"]
    finish = next(e for e in events if e["kind"] == "tool_finished")
    assert finish["tool_call_id"] == "call-9"
    assert finish["duration_ms"] == 123.4
    assert finish["elapsed_ms"] >= 0
    start = next(e for e in events if e["kind"] == "tool_started")
    assert start["tool_call_id"] == "call-9"


def test_terminal_idempotency_is_atomic_across_kinds(tmp_path):
    """并发重复终止（含不同 kind）只落一条最终状态；幂等检查与追加同一临界区。"""
    root = Path(tmp_path)
    store = TaskEventStore(root)
    store.append("t-term", {"kind": "accepted"})
    barrier = threading.Barrier(6)
    results: list = []

    def worker(kind):
        barrier.wait()
        results.append(store.append_terminal("t-term", {"kind": kind, "state": kind}))

    threads = [threading.Thread(target=worker, args=(k,))
               for k in ("completed", "failed", "cancelled", "completed", "failed", "completed")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    events = store.read_turn("t-term")
    terminals = [e["kind"] for e in events if e["kind"] in {"completed", "failed", "cancelled"}]
    assert len(terminals) == 1, terminals
    assert [e["seq"] for e in events] == sorted({e["seq"] for e in events})


def test_append_after_half_line_does_not_lose_events(tmp_path):
    """崩溃残行之后继续追加：新事件完整可读（不接在残行上）。"""
    root = Path(tmp_path)
    store = TaskEventStore(root)
    store.append("t-half", {"kind": "accepted"})
    with open(store.turn_path("t-half"), "a", encoding="utf-8") as fh:
        fh.write('{"kind": "comp')  # 模拟崩溃残行（无换行）
    store.append("t-half", {"kind": "preparing", "message": "恢复后继续"})
    store.append_terminal("t-half", {"kind": "completed", "state": "delivered", "message": "恢复后完成"})
    events = store.read_turn("t-half")
    kinds = [e["kind"] for e in events]
    assert kinds.count("preparing") == 1 and kinds.count("completed") == 1
    assert events[-1]["kind"] == "completed" and events[-1]["message"] == "恢复后完成"


def test_recording_status_surfaced_in_turn_response(tmp_path):
    """持久化失败上报到执行响应：任务继续，但明确"执行记录保存失败"。"""
    from openbrep.runtime.pipeline import TaskResult
    from tests.test_task_feedback_regression import _real_modify_session

    session = _real_modify_session(Path(tmp_path))
    service = session.task_event_service

    def _orig_prepare_all(turn_id, **kwargs):
        return None

    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-rec", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    # 注入持久化失败：在执行前替换 store.append
    store = service._store_for(str(session.source_path))

    def failing_append(turn_id, event):
        raise OSError("disk full")

    from unittest.mock import patch

    with patch.object(store, "append", failing_append):
        done = session.route("POST", "/api/assistant/turn", {"phase": "execute", "turn_id": ready["turn_id"]})
    assert done["result_kind"] == "execution"
    assert done.get("events_recording", {}).get("status") == "degraded"
    assert "disk full" in done["events_recording"].get("error", "")
