"""卡01（任务反馈与连续对话修复）：契约冻结与故障回归。

冻结四份契约，配套实施计划《OpenBrep-任务反馈与连续对话修复-实施计划-2026-10-05》：

1. 项目身份契约（卡02）：真实 generate 收尾赋值 → 响应 → 下一轮 prepare 的
   跨调用回归；同步与 legacy 流式都覆盖；XML 保存刷新不递增 epoch。
2. Agent 超时契约（卡03）：虚拟时钟下有有效活动超过 llm.timeout（90s）窗口
   不终止；[agent] 超时配置键与正数校验。
3. 任务事件契约（卡04）：事件 schema 常量与任务事件存储读写契约。
4. 前端复盘契约（卡05）：见 frontend thinkingSteps 往返 / 时间线展开回归。

本卡允许新增用例红灯：统一以 ``xfail(strict=True)`` 记录预期红灯；对应卡实施
转绿时移除标记（strict 保证转绿后遗忘移除会显式失败）。既有基线必须全绿。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbrep.hsf_project import ScriptType
from openbrep.runtime.pipeline import TaskResult
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from tests.test_conversation_service import prepare, session_at

# ── 契约一：项目身份（卡02）────────────────────────────────────


class _SelfProjectPipeline:
    """替身 pipeline：返回携带 request.project 的真实 TaskResult。

    真实 pipeline 的 MODIFY 收尾会把修改后的项目对象放进 TaskResult.project，
    assistant_service 收尾随后把它赋回 session.project——本替身保留该赋值链，
    不 mock 整个 generate（否则掩盖收尾赋值，见卡01派单）。
    """

    captured: list = []

    def __init__(self, **kwargs):
        _SelfProjectPipeline.captured.append(self)
        self.kwargs = kwargs
        self.request = None

    def execute(self, request):
        self.request = request
        return TaskResult(
            success=True,
            plain_text="已加一层板，编译通过。",
            scripts={"scripts/3d.gdl": "BLOCK 1, 2, 3\nEND\n"},
            project=request.project,
        )


def _real_modify_session(tmp_path):
    """真实 generate_with_assistant + 替身 pipeline 的会话（贯穿收尾赋值）。"""
    session = session_at(Path(tmp_path))
    session.assistant_service.generate_with_assistant = (
        WorkbenchAssistantService.generate_with_assistant.__get__(
            session.assistant_service, WorkbenchAssistantService
        )
    )
    _SelfProjectPipeline.captured = []
    session.pipeline_class = _SelfProjectPipeline
    session.conversation_service.semantic_decision = lambda payload: json.dumps(
        {"mode": "execute", "task_intent": "MODIFY", "constraints": []}
    )
    return session


@pytest.mark.xfail(strict=True, reason="卡02：同项目修改收尾走显式源刷新，不再递增 epoch")
def test_followup_prepare_after_modify_finish_keeps_client_epoch(tmp_path):
    """跨调用回归（统一入口同步路径）：真实 generate 收尾赋值 session.project
    （今天 epoch 1→2）后，客户端仍持有任务开始时的代次 1，下一轮 prepare
    不得返回 PROJECT_CHANGED——同项目连续对话是产品契约。"""
    session = _real_modify_session(tmp_path)
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c1", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    assert ready["result_kind"] == "ready_to_execute"
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"],
    })
    assert done["result_kind"] == "execution", done
    # 收尾赋值后客户端 epoch 未变（前端只有快照刷新，没有代次推送）
    followup = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c2", "message": "再加一块隔板",
        "project_epoch": ready["project_epoch"],
    })
    assert followup["result_kind"] == "ready_to_execute", followup


@pytest.mark.xfail(strict=True, reason="卡02：legacy 流式收尾同样走显式源刷新")
def test_legacy_stream_finish_keeps_epoch(tmp_path):
    """legacy 流式路径：generate_with_assistant_stream 收尾赋值后，epoch 保持
    稳定；客户端以原代次发起下一轮不被拒绝。"""
    session = _real_modify_session(tmp_path)
    epoch_before = session.project_epoch
    events = list(session.assistant_service.generate_with_assistant_stream(
        {"message": "添加背板", "history": []}, cancel_event=None,
    ))
    kinds = [event["type"] for event in events]
    assert kinds[-1] == "done", kinds
    assert session.project_epoch == epoch_before


@pytest.mark.xfail(strict=True, reason="卡02：XML 保存后内存刷新走显式源刷新，不递增 epoch")
def test_xml_save_refresh_keeps_epoch(tmp_path):
    """paramlist.xml 保存后重载内存对象是同项目源刷新，不是项目激活：
    epoch 不变，且已打开的会话以原代次继续可用。"""
    session = session_at(Path(tmp_path))
    from openbrep.paramlist_builder import build_paramlist_xml

    content = build_paramlist_xml(session.project.parameters)
    epoch_before = session.project_epoch
    result = session.route("POST", "/api/project/script/paramlist.xml", {"content": content})
    assert result["ok"], result
    assert session.project_epoch == epoch_before
    followup = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "c-xml", "message": "添加背板",
        "project_epoch": epoch_before,
    })
    assert followup["result_kind"] == "ready_to_execute", followup


# ── 契约二：Agent 超时（卡03）────────────────────────────────


class _SteppingClock:
    """虚拟时钟：每次 monotonic() 前进固定步长（秒）。"""

    def __init__(self, step: float) -> None:
        self._step = step
        self._now = 0.0

    def __call__(self) -> float:
        self._now += self._step
        return self._now


class _FakeTimeModule:
    """替换 modify_codex_bridge.time 的虚拟时钟模块（只暴露用到的 monotonic）。"""

    def __init__(self, clock: _SteppingClock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock()


class _ServerRequestTransport:
    """进程内 transport：脚本化通知流 + item/tool/call 服务器请求。

    与 tests.test_codex_turn._RecordingTransport 同思路，但支持 modify 驱动
    需要的 subscribe_server_request / respond 面。
    """

    def __init__(self, notifications: list[dict], tool_calls: list[dict]) -> None:
        self.notifications = notifications
        self.tool_calls = tool_calls
        self.subscribers: list = []
        self.server_subscribers: list = []
        self.responded: list[tuple[int, dict]] = []
        self.calls: list[tuple[str, dict]] = []

    def subscribe(self, handler) -> None:
        self.subscribers.append(handler)

    def unsubscribe(self, handler) -> None:
        if handler in self.subscribers:
            self.subscribers.remove(handler)

    def subscribe_server_request(self, handler) -> None:
        self.server_subscribers.append(handler)

    def unsubscribe_server_request(self, handler) -> None:
        if handler in self.server_subscribers:
            self.server_subscribers.remove(handler)

    def respond(self, req_id: int, result: dict) -> None:
        self.responded.append((req_id, result))

    def _deliver(self, frame: dict) -> None:
        for handler in list(self.subscribers):
            handler(frame)

    def _deliver_tool_call(self, req: dict) -> None:
        for handler in list(self.server_subscribers):
            handler(req["req_id"], "item/tool/call", req["params"])

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "th-1"}}
        if method == "turn/start":
            # 先投递服务器工具请求，再投递 final/完成通知——与真实时序一致
            # （工具调用先于 final 消息；驱动按事件消费顺序处理）。
            for req in self.tool_calls:
                self._deliver_tool_call(req)
            for frame in self.notifications:
                self._deliver(frame)
            return {"turn": {"id": "tn-1"}}
        if method in ("turn/interrupt", "thread/delete"):
            return {}
        raise AssertionError(f"unexpected method: {method}")


class _DelegatingClient:
    """thread/turn 方法委托 transport 的最小客户端替身（对齐 _RecordingClient）。"""

    def __init__(self, transport: _ServerRequestTransport) -> None:
        self.transport = transport

    def thread_start(self, params):
        return self.transport.call("thread/start", params)

    def turn_start(self, params):
        return self.transport.call("turn/start", params)

    def turn_interrupt(self, params):
        return self.transport.call("turn/interrupt", params)

    def thread_delete(self, params):
        return self.transport.call("thread/delete", params)


def _driver_tool_call(req_id: int, call_id: str) -> dict:
    return {"req_id": req_id, "params": {
        "callId": call_id, "tool": "read_parameters", "arguments": {},
        "threadId": "th-1", "turnId": "tn-1",
    }}


def _final_turn_frames() -> list[dict]:
    thread = {"threadId": "th-1", "turnId": "tn-1"}
    return [
        {"method": "item/started", "params": {**thread, "item": {"id": "m1", "phase": "final_answer"}}},
        {"method": "item/agentMessage/delta", "params": {**thread, "itemId": "m1", "delta": "OK"}},
        {"method": "item/completed", "params": {**thread, "item": {
            "id": "m1", "type": "agentMessage", "text": "OK", "phase": "final_answer"}}},
        {"method": "turn/completed", "params": {**thread, "turn": {"id": "tn-1", "status": "completed"}}},
    ]


@pytest.mark.xfail(strict=True, reason="卡03：活动续期；今天 90s 固定截止会误杀有活动的回合")
def test_active_progress_survives_beyond_90s_window(tmp_path, monkeypatch):
    """超时虚拟时钟回归：4 次真实执行并返回结果的工具调用 + 已在途的 final
    消息，虚拟时钟累计远超 llm.timeout=90s 窗口，turn 不得以 timeout 终止。

    原始事故（r_20261005_142143_0c1f07）：9 次工具调用、117.48 秒、timeout、
    部分修改已落盘——有有效活动的回合被固定截止时间中断。
    """
    from openbrep.runtime import modify_codex_bridge

    transport = _ServerRequestTransport(
        notifications=_final_turn_frames(),
        tool_calls=[_driver_tool_call(i, f"call-{i}") for i in range(4)],
    )
    executed: list[str] = []
    driver = modify_codex_bridge.CodexModifyTurnDriver(
        client=_DelegatingClient(transport),
        model="gpt-5.6-luna",
        cwd=str(tmp_path),
        system_text="sys",
        dynamic_tools=[],
        executor=lambda _call_id, _ns, tool, _args: (executed.append(tool) or "ok", True),
        timeout=90.0,
        should_cancel=None,
        on_delta=None,
    )
    monkeypatch.setattr(modify_codex_bridge, "time", _FakeTimeModule(_SteppingClock(step=20.0)))
    outcome = driver.run("hi")
    assert outcome.finish_reason == "stop", (outcome.finish_reason, outcome.error)
    assert outcome.content == "OK"
    assert executed == ["read_parameters"] * 4
    assert len(transport.responded) == 4


@pytest.mark.xfail(strict=True, reason="卡03：[agent] 超时配置键冻结（缺省兼容 + 正数校验）")
def test_agent_timeout_contract_keys_and_validation(tmp_path):
    """卡01 冻结超时键名与语义（实施计划 §二）：

    - ``[agent] agent_idle_timeout``=180：无有效活动上限；普通 llm.timeout
      不再限制整个工具回合。
    - ``[agent] agent_task_timeout``=1800：Agent 总执行上限，整个任务跨轮共享。
    - ``[agent] agent_tool_timeout``=600：单工具执行上限；编译/预览沿用
      compiler.timeout 独立预算。
    - 正数校验：0/负数/非法值回退默认值并记 warning，0 不得隐式表示无限。
    """
    from openbrep.config import GDLAgentConfig

    cfg = GDLAgentConfig()
    assert cfg.agent.agent_idle_timeout == 180
    assert cfg.agent.agent_task_timeout == 1800
    assert cfg.agent.agent_tool_timeout == 600

    config_path = Path(tmp_path) / "agent.toml"
    good = GDLAgentConfig()
    good.agent.agent_idle_timeout = 240
    good.agent.agent_task_timeout = 3600
    good.agent.agent_tool_timeout = 120
    good.save(str(config_path))
    loaded = GDLAgentConfig.load(str(config_path))
    assert loaded.agent.agent_idle_timeout == 240
    assert loaded.agent.agent_task_timeout == 3600
    assert loaded.agent.agent_tool_timeout == 120

    bad_path = Path(tmp_path) / "bad.toml"
    bad = GDLAgentConfig()
    bad.agent.agent_idle_timeout = 0
    bad.agent.agent_task_timeout = -5
    bad.agent.agent_tool_timeout = "abc"  # type: ignore[assignment]
    bad.save(str(bad_path))
    reloaded = GDLAgentConfig.load(str(bad_path))
    assert reloaded.agent.agent_idle_timeout == 180
    assert reloaded.agent.agent_task_timeout == 1800
    assert reloaded.agent.agent_tool_timeout == 600


# ── 契约三：任务事件与存储（卡04）────────────────────────────


@pytest.mark.xfail(strict=True, reason="卡04：统一执行事件 schema 常量冻结")
def test_task_event_schema_contract():
    """事件契约（实施计划 §三）：字段全集 + kind 覆盖面 + seq 单调。"""
    from openbrep.workbench.task_events import (
        TASK_EVENT_FIELDS,
        TASK_EVENT_KINDS,
        TASK_EVENT_SCHEMA_VERSION,
    )

    assert TASK_EVENT_SCHEMA_VERSION == 1
    assert set(TASK_EVENT_FIELDS) >= {
        "schema_version", "event_id", "seq", "timestamp", "elapsed_ms",
        "session_id", "project_epoch", "turn_id", "run_id", "kind", "stage",
        "state", "message", "tool_call_id", "tool_name", "affected_files",
        "duration_ms", "summary", "error_code",
    }
    assert set(TASK_EVENT_KINDS) >= {
        "accepted", "preparing", "waiting_model", "public_commentary",
        "tool_started", "tool_finished", "verification", "source_changed",
        "delivery", "cancelled", "failed", "completed",
    }


@pytest.mark.xfail(strict=True, reason="卡04：任务事件存储读写契约冻结")
def test_task_event_store_contract(tmp_path):
    """存储契约（实施计划 §四）：每 turn 独立 JSONL、seq 按 turn 单调、
    终止幂等去重、读取容忍最后半行、目录按需创建。"""
    from openbrep.workbench.task_event_store import TaskEventStore

    root = Path(tmp_path)
    store = TaskEventStore(root)
    assert store.tasks_dir == root / ".openbrep" / "memory" / "chats" / "tasks"

    first = store.append("turn-a", {"kind": "accepted", "session_id": "s1", "project_epoch": 1})
    assert first["seq"] == 1
    assert first["turn_id"] == "turn-a"
    assert first["schema_version"] == 1
    second = store.append("turn-a", {"kind": "tool_finished", "session_id": "s1", "project_epoch": 1})
    assert second["seq"] == 2
    other = store.append("turn-b", {"kind": "accepted", "session_id": "s1", "project_epoch": 1})
    assert other["seq"] == 1

    events = store.read_turn("turn-a")
    assert [e["seq"] for e in events] == [1, 2]

    # 终止幂等：同 turn 重复 completed 只落一条（seq 去重语义由 store 保证）
    store.append_terminal("turn-a", {"kind": "completed", "session_id": "s1", "project_epoch": 1})
    store.append_terminal("turn-a", {"kind": "completed", "session_id": "s1", "project_epoch": 1})
    assert [e["kind"] for e in store.read_turn("turn-a")].count("completed") == 1

    # 读取容忍最后半行：手工追加截断行不破坏读取
    path = store.turn_path("turn-a")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "trunc')
    assert len(store.read_turn("turn-a")) == 3

    # 无项目的咨询不创建目录（project=null 只留会话内存）
    fresh = TaskEventStore(root / "fresh")
    assert not fresh.tasks_dir.exists()
