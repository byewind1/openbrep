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
from tests.fake_codex_modify_transport import (
    _DelegatingClient,
    _ServerRequestTransport,
    _SteppingClock,
    driver_tool_call as _driver_tool_call,
    final_turn_frames as _final_turn_frames,
)
from tests.test_conversation_service import session_at

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


def test_followup_prepare_after_modify_finish_keeps_client_epoch(tmp_path):
    """跨调用回归（统一入口同步路径）：真实 generate 收尾赋值 session.project
    后，客户端仍持有任务开始时的代次，下一轮 prepare 不得返回
    PROJECT_CHANGED——同项目连续对话是产品契约（卡02 显式源刷新）。"""
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


def test_legacy_stream_finish_keeps_epoch(tmp_path):
    """legacy 流式路径：generate_with_assistant_stream 收尾赋值后，epoch 保持
    稳定；客户端以原代次发起下一轮不被拒绝（卡02 显式源刷新）。"""
    session = _real_modify_session(tmp_path)
    epoch_before = session.project_epoch
    events = list(session.assistant_service.generate_with_assistant_stream(
        {"message": "添加背板", "history": []}, cancel_event=None,
    ))
    kinds = [event["type"] for event in events]
    assert kinds[-1] == "done", kinds
    assert session.project_epoch == epoch_before


def test_xml_save_refresh_keeps_epoch(tmp_path):
    """paramlist.xml 保存后重载内存对象是同项目源刷新，不是项目激活：
    epoch 不变，且已打开的会话以原代次继续可用（卡02 显式源刷新）。"""
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


def test_three_rounds_modify_consult_modify_keep_conversation(tmp_path):
    """卡02 验收：连续三轮 修改→咨询→修改，同项目内会话代次稳定，任务状态
    不被收尾赋值打断。"""
    import json as _json

    from openbrep.llm import MockLLM

    session = _real_modify_session(tmp_path)
    epoch = session.project_epoch
    first = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "m1", "message": "添加背板",
        "project_epoch": epoch,
    })
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": first["turn_id"],
    })
    assert done["result_kind"] == "execution", done
    # 咨询轮：advisor 走 MockLLM
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[
        _json.dumps({"conclusion": "比例可以", "suggestions": [], "tradeoffs": []}, ensure_ascii=False),
    ])
    advice = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "q1", "message": "这个柜子比例不协调，有什么思路",
        "project_epoch": epoch,
    })
    assert advice["result_kind"] == "advice", advice
    # 第三轮修改：原代次直接可用
    third = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "m2", "message": "再加一块隔板",
        "project_epoch": epoch,
    })
    assert third["result_kind"] == "ready_to_execute", third
    assert session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": third["turn_id"],
    })["result_kind"] == "execution"
    assert session.project_epoch == epoch


def test_partial_modify_keeps_next_round_usable(tmp_path):
    """卡02 验收：部分修改（验证未过但有产出）交付后，下一轮 prepare/execute
    不被代次或指纹误拒。"""
    from openbrep.runtime.pipeline import TaskResult

    class _PartialPipeline(_SelfProjectPipeline):
        def execute(self, request):
            self.request = request
            return TaskResult(
                success=False,
                plain_text="超时前已完成部分修改",
                scripts={"scripts/3d.gdl": "BLOCK 1, 2, 3\nEND\n"},
                project=request.project,
            )

    session = _real_modify_session(tmp_path)
    session.pipeline_class = _PartialPipeline
    epoch = session.project_epoch
    first = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "p1", "message": "添加背板",
        "project_epoch": epoch,
    })
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": first["turn_id"],
    })
    assert done["result_kind"] == "execution", done
    followup = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "p2", "message": "继续完成剩下的部分",
        "project_epoch": epoch,
    })
    assert followup["result_kind"] == "ready_to_execute", followup


def test_activation_paths_still_invalidate_old_epoch(tmp_path):
    """卡02 验收：项目激活入口保持失效语义——关闭、同路径重开、恢复 revision
    都递增 epoch，旧代次的 prepare/execute 被拒绝。"""
    session = session_at(Path(tmp_path))
    epoch0 = session.project_epoch
    project_path = str(session.source_path)
    # 同路径重开（load 同一目录）
    session.route("POST", "/api/project/load", {"path": project_path})
    assert session.project_epoch == epoch0 + 1
    assert session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "stale", "message": "添加背板",
        "project_epoch": epoch0,
    })["code"] == "PROJECT_CHANGED"
    # 关闭项目
    session.route("POST", "/api/project/close")
    assert session.project_epoch == epoch0 + 2
    # 恢复 revision：旧任务失效（规格明确保留递增）
    session.route("POST", "/api/project/load", {"path": project_path})
    epoch_now = session.project_epoch
    saved = session.route("POST", "/api/project/revision/save", {"message": "savepoint"})
    assert saved["ok"], saved
    restored = session.route("POST", "/api/project/revision/restore", {
        "revision_id": saved["revision"]["revision_id"],
    })
    assert restored["ok"], restored
    assert session.project_epoch == epoch_now + 1
    assert session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "stale2", "message": "添加背板",
        "project_epoch": epoch_now,
    })["code"] == "PROJECT_CHANGED"


def test_response_carries_current_identity(tmp_path):
    """卡02 验收：执行响应带 session_id 与 current_project_epoch（服务端当前
    身份），旧任务结果不得盲接入新项目。"""
    session = _real_modify_session(tmp_path)
    ready = session.route("POST", "/api/assistant/turn", {
        "phase": "prepare", "client_turn_id": "id1", "message": "添加背板",
        "project_epoch": session.project_epoch,
    })
    assert ready["session_id"] == session.session_id
    assert ready["current_project_epoch"] == session.project_epoch
    done = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": ready["turn_id"],
    })
    assert done["session_id"] == session.session_id
    assert done["current_project_epoch"] == session.project_epoch


# ── 契约二：Agent 超时（卡03）────────────────────────────────


class _FakeTimeModule:
    """替换 modify_codex_bridge.time 的虚拟时钟模块（只暴露用到的 monotonic）。"""

    def __init__(self, clock: _SteppingClock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock()


def test_active_progress_survives_beyond_90s_window(tmp_path, monkeypatch):
    """超时虚拟时钟回归（卡03 转绿）：4 次真实执行并返回结果的工具调用 +
    已在途的 final 消息，虚拟时钟累计远超 90s 窗口，turn 以 stop 正常收尾。

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


def test_agent_timeout_contract_keys_and_validation(tmp_path):
    """超时配置键契约（卡03 转绿）：

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
