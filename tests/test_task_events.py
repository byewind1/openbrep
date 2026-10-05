"""卡04：实时事件与后端持久化——服务/存储/路由/并发契约测试。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from openbrep.runtime.pipeline import TaskResult
from openbrep.workbench.request_gate import is_lock_free_route
from openbrep.workbench.task_event_store import TaskEventStore
from tests.test_conversation_service import session_at
from tests.test_task_feedback_regression import (
    _SelfProjectPipeline,
    _real_modify_session,
)


def _events(session, turn_id: str) -> list[dict]:
    result = session.route("GET", f"/api/assistant/turn/events/{turn_id}", {})
    assert result["ok"], result
    return result["events"]


def _kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


def _prepare_and_execute(session, message="添加背板", client_id="c1"):
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": client_id, "message": message,
        "project_epoch": session.project_epoch,
    })
    assert ready["result_kind"] == "ready_to_execute", ready
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"],
    })
    return ready, done


def test_unified_turn_records_accepted_ready_and_completed(tmp_path):
    """正常执行 turn：accepted → preparing(ready) → completed 终止，seq 单调，
    session_id/project_epoch/turn_id 绑定。"""
    session = _real_modify_session(tmp_path)
    ready, done = _prepare_and_execute(session)
    assert done["result_kind"] == "execution"
    events = _events(session, ready["turn_id"])
    assert _kinds(events)[0] == "accepted"
    assert events[0]["seq"] == 1
    assert events[0]["session_id"] == session.session_id
    assert "preparing" in _kinds(events)
    assert _kinds(events)[-1] == "completed"
    assert events[-1]["seq"] == len(events)
    # 落盘位置契约：每 turn 独立 JSONL
    path = Path(session.source_path) / ".openbrep" / "memory" / "chats" / "tasks" / f"{ready['turn_id']}.jsonl"
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == len(events)


def test_pipeline_events_recorded_before_broadcast_and_tool_states(tmp_path):
    """卡04 核心：工具开始/结束/验证事件由真实 on_event 流适配落盘；
    广播回调触发时事件已落盘（append-before-broadcast）；未返回工具是
    running，失败工具是 failed。"""
    session = _real_modify_session(tmp_path)

    class _EventEmittingPipeline(_SelfProjectPipeline):
        def execute(self, request):
            self.request = request
            emit = getattr(request, "on_event", None)
            assert callable(emit)
            emit("tool_started", {"tool": "update_script", "tool_call_id": "c-1", "stage": "think"})
            emit("tool_call", {"tool": "update_script", "ok": True, "summary": "已更新 scripts/3d.gdl"})
            emit("tool_started", {"tool": "compile_script", "tool_call_id": "c-2", "stage": "think"})
            emit("compile_result", {"success": True, "message": "编译通过"})
            emit("tool_call", {"tool": "compile_script", "ok": False, "summary": "静态检查 1 个问题"})
            return TaskResult(success=True, plain_text="完成", project=request.project)

    session.pipeline_class = _EventEmittingPipeline
    _SelfProjectPipeline.captured = []
    broadcast_saw_persisted: list[bool] = []
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-ev", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    _turn_id = ready["turn_id"]
    # 广播回调时刻：tool_finished 必须已落盘可读（先追加后广播）
    def worker():
        for event in session.route("POST", "/api/assistant/turn", {
            "phase": "execute", "turn_id": _turn_id, "stream": True,
        }):
            if isinstance(event, dict) and event.get("type") == "tool_finished":
                store = session.task_event_service._store_for(str(session.source_path))
                kinds = [e["kind"] for e in store.read_turn(_turn_id)]
                broadcast_saw_persisted.append("tool_finished" in kinds)

    consume = threading.Thread(target=worker)
    consume.start()
    consume.join(5)
    assert not consume.is_alive()
    assert broadcast_saw_persisted and all(broadcast_saw_persisted), broadcast_saw_persisted

    events = _events(session, _turn_id)
    tool_events = [e for e in events if e["kind"].startswith("tool_")]
    by_id = [(e["kind"], e.get("tool_name"), e.get("state")) for e in tool_events]
    # update_script 开始→结束（succeeded）；compile_script 开始→结束（failed）
    assert ("tool_started", "update_script", "running") in by_id
    assert ("tool_finished", "update_script", "succeeded") in by_id
    assert ("tool_started", "compile_script", "running") in by_id
    assert ("tool_finished", "compile_script", "failed") in by_id
    # 验证事件
    assert any(e["kind"] == "verification" and e.get("state") == "succeeded" for e in events)
    # 终止：完成
    assert _kinds(events)[-1] == "completed"


def test_terminal_events_are_idempotent(tmp_path):
    """重复 execute（幂等回放）不产生重复终止事件。"""
    session = _real_modify_session(tmp_path)
    ready, done = _prepare_and_execute(session, client_id="c-idem")
    again = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"],
    })
    assert again == done
    events = _events(session, ready["turn_id"])
    assert _kinds(events).count("completed") == 1


def test_cancelled_turn_records_cancelled_event(tmp_path):
    session = session_at(Path(tmp_path))
    session.conversation_service.semantic_decision = lambda payload: json.dumps(
        {"mode": "execute", "task_intent": "MODIFY", "constraints": []}
    )
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-cancel", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"], "approve": False,
    })
    events = _events(session, ready["turn_id"])
    assert _kinds(events)[-1] == "cancelled"


def test_failed_turn_records_failed_event_with_code(tmp_path):
    session = session_at(Path(tmp_path))
    session.conversation_service.semantic_decision = lambda payload: json.dumps(
        {"mode": "execute", "task_intent": "MODIFY", "constraints": []}
    )
    session.assistant_service.generate_with_assistant = lambda body: (_ for _ in ()).throw(
        RuntimeError("boom"))
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-fail", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"],
    })
    assert done["result_kind"] == "failed"
    events = _events(session, ready["turn_id"])
    assert _kinds(events)[-1] == "failed"
    assert events[-1].get("error_code") == "EXECUTION_FAILED"


def test_consult_without_project_creates_no_directories(tmp_path):
    """project=null 的咨询：只留会话内存，不创建项目/output/任务目录。"""
    session = session_at(Path(tmp_path))
    session.project = None
    session.source_path = None
    from openbrep.llm import MockLLM

    session.conversation_service.semantic_decision = lambda payload: json.dumps(
        {"mode": "consult", "task_intent": "CHAT", "constraints": []}
    )
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[
        json.dumps({"conclusion": "建议", "suggestions": [], "tradeoffs": []}, ensure_ascii=False),
    ])
    result = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-chat", "message": "什么是GDL",
        "project_epoch": session.project_epoch,
    })
    assert result["result_kind"] == "advice", result
    assert not (tmp_path / ".openbrep").exists()
    assert not (tmp_path / "output").exists()


def test_partial_modify_records_partial_state(tmp_path):
    """部分修改：完成状态区分——delivery partial_change → completed/partial。"""
    from openbrep.runtime.pipeline import TaskResult

    class _PartialPipeline(_SelfProjectPipeline):
        def execute(self, request):
            self.request = request
            return TaskResult(
                success=False,
                plain_text="中断前进度",
                scripts={"scripts/3d.gdl": "BLOCK\n"},
                project=request.project,
                metadata={"delivery_source": {"state": "partial_change", "status": "incomplete"},
                          "acceptance": {}, "delivery": {
                              "presentation": {"status": "incomplete", "state": "partial_change",
                                               "headline": "未完成，存在部分修改",
                                               "changed_files": ["scripts/3d.gdl"]}},
                          "changed_files": ["scripts/3d.gdl"]},
            )

    session = _real_modify_session(tmp_path)
    session.pipeline_class = _PartialPipeline
    _SelfProjectPipeline.captured = []
    ready, done = _prepare_and_execute(session, client_id="c-partial")
    events = _events(session, ready["turn_id"])
    last = events[-1]
    assert last["kind"] == "completed"
    assert last["state"] == "partial", last
    assert "scripts/3d.gdl" in (last.get("affected_files") or [])


def test_query_route_is_lock_free_and_readable_during_execution(tmp_path):
    """查询不拿长执行 _op_lock：执行中被阻塞的锁不堵 GET 事件读取。"""
    session = _real_modify_session(tmp_path)
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-live", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    turn_id = ready["turn_id"]
    entered, release = threading.Event(), threading.Event()

    class _BlockingPipeline(_SelfProjectPipeline):
        def execute(self, request):
            self.request = request
            entered.set()
            assert release.wait(3)
            return TaskResult(success=True, plain_text="done", project=request.project)

    session.pipeline_class = _BlockingPipeline
    _SelfProjectPipeline.captured = []
    stream = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": turn_id, "stream": True,
    })
    consume = threading.Thread(target=lambda: list(stream))
    consume.start()
    assert entered.wait(3)
    # 执行持锁期间：GET 事件查询可用且能读到 accepted
    assert is_lock_free_route("GET", f"/api/assistant/turn/events/{turn_id}")
    events = _events(session, turn_id)
    assert _kinds(events)[0] == "accepted"
    release.set()
    consume.join(5)
    assert not consume.is_alive()


def test_history_save_does_not_touch_task_events(tmp_path):
    """聊天 history rewrite 与任务事件文件互不干扰（并发契约）。"""
    session = _real_modify_session(tmp_path)
    ready, _ = _prepare_and_execute(session, client_id="c-save")
    before = _events(session, ready["turn_id"])
    # 并发保存历史（改写聊天正文/meta）×N
    for _ in range(3):
        save = session.route("POST", "/api/assistant/history",
                             {"messages": [{"role": "user", "content": "添加背板"},
                                           {"role": "assistant", "content": "已完成"}]})
        assert save["ok"], save
    after = _events(session, ready["turn_id"])
    assert after == before


def test_source_fingerprint_and_managed_files_exclude_tasks(tmp_path):
    """任务事件记录不进入源指纹/revision 快照（间接保证不进 prompt）。"""
    from openbrep.source_fingerprint import collect_managed_source_files, compute_source_fingerprint

    session = _real_modify_session(tmp_path)
    root = Path(session.source_path)
    before_files = collect_managed_source_files(root)
    before_fp = compute_source_fingerprint(root)
    _prepare_and_execute(session, client_id="c-fp")
    after_files = collect_managed_source_files(root)
    after_fp = compute_source_fingerprint(root)
    assert after_files == before_files
    assert after_fp == before_fp
    assert not any(p.startswith(".openbrep") for p in after_files)


def test_store_redacts_secrets_and_tolerates_half_line(tmp_path):
    """脱敏：凭据/长 base64 不入日志；读取容忍最后半行。"""
    root = Path(tmp_path)
    store = TaskEventStore(root)
    store.append("t1", {"kind": "preparing", "message": "key=sk-abcdef1234567890 泄漏测试"})
    store.append("t1", {"kind": "preparing", "message": "img=A" * 600 + "Z"})
    store.append_terminal("t1", {"kind": "completed", "state": "delivered"})
    events = store.read_turn("t1")
    messages = [e.get("message") for e in events]
    assert "sk-abcdef1234567890" not in messages[0]
    assert "[REDACTED]" in messages[0]
    assert "A" * 600 not in messages[1]
    # 半行
    with open(store.turn_path("t1"), "a", encoding="utf-8") as fh:
        fh.write('{"kind": "comp')
    assert len(store.read_turn("t1")) == 3


def test_service_commentary_buffer_merges_and_flushes(tmp_path):
    """public_commentary 按窗口合并为规范事件；终止时 flush，不丢。

    （RF02 更正：assistant_delta 是 final 文本流，不再误记为公开思考。）
    """
    from openbrep.workbench.task_event_service import WorkbenchTaskEventService

    session = session_at(Path(tmp_path))
    service = WorkbenchTaskEventService(session)
    service.begin_turn("t-comment", project_epoch=1, message="改回纹")
    for chunk in ("回", "纹", "修改", "完成"):
        service.handle_pipeline_event("t-comment", "public_commentary", {"content": chunk})
    service.finish_turn("t-comment", kind="completed", state="delivered", message="done")
    store = service._store_for(str(session.source_path))
    events = store.read_turn("t-comment")
    commentary = [e for e in events if e["kind"] == "public_commentary"]
    assert commentary, events
    assert "回纹修改完成" in commentary[-1]["message"]
    assert _last_kind(events) == "completed"


def _last_kind(events):
    return events[-1]["kind"]
