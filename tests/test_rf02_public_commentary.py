"""RF02（返修派单 P1）：Codex 公开 commentary 接通——与 final 答复分离。

原始缺陷：bridge 构造 driver 传 on_delta=None；driver delta 分支排除
commentary、_record_item 遇 commentary 直接返回——真实 Codex 的公开说明
从未到达任务事件服务（public_commentary 事件形同虚设）。

修复契约：
- driver 新增 on_commentary 回调：仅转发协议 agentMessage 的公开 commentary；
  final 答复仍走 candidates/_record_item，二者不混流；
- 支持 delta 与完整 item 两种到达模式：同 item id 已有 delta 的 completed
  不再重复转发（去重）；隐藏 reasoning 不采集；
- bridge 把 commentary 转为 public_commentary 事件（先记录再广播由服务层负责）；
- 服务层合并段落（窗口/段落收束），final 不吞进度。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_codex_modify_bridge import (
    _codex_config,
    _make_project,
    _pipeline,
    _request,
    _sem_pass,
    _write_script,
    _FakeServerHarness,
)
from tests.fake_codex_modify_transport import (
    _DelegatingClient,
    _ServerRequestTransport,
    final_turn_frames,
)

# ── driver 层：commentary 与 final 分流 + 去重 ────────────────


def _commentary_frames() -> list[dict]:
    thread = {"threadId": "th-1", "turnId": "tn-1"}
    return [
        # 公开说明段落一：delta 到达 + completed 完整 item（同 id → 去重）
        {"method": "item/started", "params": {**thread, "item": {"id": "c1", "phase": "commentary"}}},
        {"method": "item/agentMessage/delta", "params": {**thread, "itemId": "c1", "delta": "我先检查"}},
        {"method": "item/agentMessage/delta", "params": {**thread, "itemId": "c1", "delta": "参数结构。"}},
        {"method": "item/completed", "params": {**thread, "item": {
            "id": "c1", "type": "agentMessage", "text": "我先检查参数结构。", "phase": "commentary"}}},
        # 段落二：只有完整 item（无 delta → 不被去重屏蔽）
        {"method": "item/completed", "params": {**thread, "item": {
            "id": "c2", "type": "agentMessage", "text": "计划用 patch_script。", "phase": "commentary"}}},
        # final 答复（与 commentary 分离）
        {"method": "item/started", "params": {**thread, "item": {"id": "m1", "phase": "final_answer"}}},
        {"method": "item/agentMessage/delta", "params": {**thread, "itemId": "m1", "delta": "已修改完成。"}},
        {"method": "item/completed", "params": {**thread, "item": {
            "id": "m1", "type": "agentMessage", "text": "已修改完成。", "phase": "final_answer"}}},
        {"method": "turn/completed", "params": {**thread, "turn": {"id": "tn-1", "status": "completed"}}},
    ]


def test_driver_forwards_commentary_separately_with_dedup(tmp_path):
    from openbrep.runtime.modify_codex_bridge import CodexModifyTurnDriver

    commentary: list[str] = []
    transport = _ServerRequestTransport(
        notifications=_commentary_frames(),
        tool_calls=[],
    )
    driver = CodexModifyTurnDriver(
        client=_DelegatingClient(transport),
        model="gpt-5.6-luna",
        cwd=str(tmp_path),
        system_text="sys",
        dynamic_tools=[],
        executor=lambda *_: ("", True),
        timeout=30.0,
        should_cancel=None,
        on_delta=None,       # on_delta=None 不得遮蔽 commentary 链路
        on_commentary=commentary.append,
    )
    outcome = driver.run("hi")
    assert outcome.finish_reason == "stop"
    assert outcome.content == "已修改完成。", "final 答复必须与 commentary 分离"
    assert commentary == ["我先检查", "参数结构。", "计划用 patch_script。"], commentary


def test_bridge_emits_public_commentary_events(tmp_path):
    """真实桥：fake server commentary → on_event('public_commentary') 先于 final
    完成；final 文本不包含 commentary 内容。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [[
        {"op": "commentary", "text": "我先检查参数结构。"},
        {"op": "final", "text": "已按计划完成修改，编译通过。"},
    ]])
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    events: list[tuple[str, dict]] = []
    try:
        with patch_semantics_pass():
            request = _request(tmp_path, project)
            request.on_event = lambda kind, data: events.append((kind, dict(data)))
            from openbrep.runtime.modify_codex_bridge import run_codex_modify_agent_loop

            result = run_codex_modify_agent_loop(pipeline, request)
    finally:
        provider.close()
        harness.cleanup()
    kinds = [kind for kind, _ in events]
    assert "public_commentary" in kinds, kinds
    idx = kinds.index("public_commentary")
    content = events[idx][1].get("content") or ""
    assert "我先检查参数结构" in content
    # commentary 在 final 之前到达
    assert "已按计划完成修改，编译通过。" in result.plain_text
    assert "我先检查参数结构" not in result.plain_text.split("**Agent loop")[0], \
        "commentary 不得混入 final 答复正文"


def patch_semantics_pass():
    from unittest.mock import patch

    return patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass())


# ── 服务层：commentary 合并、先记录再广播、终局 flush ─────────


def test_service_merges_commentary_and_flushes_on_paragraph_end(tmp_path):
    """commentary 合并窗口 + 段落收束：后续非 commentary 事件先 flush 上一段；
    落盘与广播使用同一规范事件。"""
    from openbrep.workbench.task_event_service import WorkbenchTaskEventService

    session = _session_with_project(tmp_path)
    service = WorkbenchTaskEventService(session)
    service.begin_turn("turn-c", project_epoch=1, message="改回纹")

    broadcast: list[dict] = []
    out = service.handle_pipeline_event("turn-c", "public_commentary", {"content": "先检查参数"})
    assert out == []  # 未收束：等窗口/段落
    out += service.handle_pipeline_event("turn-c", "public_commentary", {"content": "结构，再改。"})
    out += service.handle_pipeline_event("turn-c", "preparing", {"stage": "think", "message": "下一步"})
    # 段落收束：第三条事件处理前 flush 出规范 commentary 事件
    kinds = [e["kind"] for e in out]
    assert "public_commentary" in kinds, out
    merged = next(e for e in out if e["kind"] == "public_commentary")
    assert merged["message"] == "先检查参数结构，再改。"
    assert merged["event_id"] and merged["seq"] > 0 and merged["timestamp"]
    broadcast.extend(out)
    service.finish_turn("turn-c", kind="completed", state="delivered", message="done")
    store = service._store_for(str(session.source_path))
    persisted = store.read_turn("turn-c")
    persisted_commentary = [e for e in persisted if e["kind"] == "public_commentary"]
    assert persisted_commentary and persisted_commentary[0]["message"] == "先检查参数结构，再改。"


def _session_with_project(tmp_path):
    from openbrep.hsf_project import HSFProject

    from tests.test_conversation_service import session_at

    return session_at(Path(tmp_path))


def test_run_id_binds_to_process_events_once_established(tmp_path):
    """run_id 在 pipeline 建立后绑定过程事件（on_event 注入）。"""
    class _RunIdProbePipeline:
        def __init__(self, **kwargs):
            self._current_run_id = None

        def execute(self, request):
            self._current_run_id = "r_20261005_probe_abc"
            request.on_event("status", {"stage": "understand", "message": "🤔 理解中"})
            from openbrep.runtime.pipeline import TaskResult

            return TaskResult(success=True, plain_text="完成", project=request.project)

    from tests.test_task_feedback_regression import _real_modify_session

    session = _real_modify_session(Path(tmp_path))
    session.pipeline_class = _RunIdProbePipeline
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-runid", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    session.route("POST", "/api/assistant/turn", {"phase": "execute", "turn_id": ready["turn_id"]})
    events = session.route("GET", f"/api/assistant/turn/events/{ready['turn_id']}", {})["events"]
    preparing = [e for e in events if e["kind"] == "preparing" and e.get("stage") == "understand"]
    assert preparing, events
    assert preparing[0].get("run_id") == "r_20261005_probe_abc"
