"""RF01（返修派单 P0）：超时/取消后禁止迟到写入，恢复有界可终止执行。

原始缺陷（隔离探针已复现）：_execute_with_timeout 启动 daemon 线程执行工具，
join(tool_timeout) 超时后仅返回失败文本，线程未被终止；join_pending_tools
再次等待后仍可存活，桥只发警告；释放阻塞点后后台线程仍执行写入——迟到写入
可污染共享 project/源码/revision/编译结果。

本文件在真实生产接缝上复现并锁定修复：
- 阻塞点 = ``ModifyToolRegistry`` 写路径真实函数 ``sanitize_llm_script_output``
  （提交前的真实生产阶段，update_script/patch_script 共用）；
- 提交点 = ``GDLAgent._apply_changes``（唯一的内存态源码变更入口）；
- 终止快照对比受管源码 hash / 参数 / revision / last_compile_result。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import ScriptType
from openbrep.source_fingerprint import compute_source_fingerprint
from tests.test_codex_modify_bridge import (
    _FakeServerHarness,
    _codex_config,
    _make_project,
    _pipeline,
    _request,
    _sem_pass,
    _upd,
    _write_script,
)

FAKE_SERVER = str(Path(__file__).resolve().parent / "fake_codex_app_server.py")


def _blocking_sanitize(monkeypatch, entered, release, finished):
    """在真实写路径的提交前阶段注入受控阻塞点。

    sanitize_llm_script_output 是 update_script/patch_script 提交前的真实
    生产阶段；阻塞释放后的提交授权检查由修复实现负责。
    """
    import openbrep.runtime.modify_agent_tools as mat

    original = mat.sanitize_llm_script_output

    def blocking(content, file_path):
        entered.set()
        assert release.wait(20), "测试阻塞点未被释放"
        try:
            return original(content, file_path)
        finally:
            finished.set()

    monkeypatch.setattr(mat, "sanitize_llm_script_output", blocking)


def _snapshot(project):
    from openbrep.revisions import list_revisions

    return {
        "fingerprint": compute_source_fingerprint(project.root),
        "scripts": dict(project.scripts),
        "params": [(p.name, p.value) for p in project.parameters],
        "revisions": [r.revision_id for r in list_revisions(project.root)],
    }


def _run_blocked_update(monkeypatch, tmp_path, *, cancel_hook=None, epoch_hook=None):
    """真实桥 + 受控阻塞点：update_script 提交前阻塞 → 工具超时 → turn 终止。

    返回 (result, entered, release, finished, bridge_probe)。
    """
    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [[
        _upd("BLOCK 9, 9, 9\nADDZ 9\nBLOCK 9, 9, 0.018\nDEL 1\nEND\n"),
        {"op": "hang"},
    ]])
    config = _codex_config()
    config.agent.agent_tool_timeout = 0.5
    config.agent.agent_idle_timeout = 3
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    _blocking_sanitize(monkeypatch, entered, release, finished)

    events: list[tuple[str, dict]] = []

    def should_cancel():
        return bool(cancel_hook and cancel_hook())

    request = _request(tmp_path, project, should_cancel=should_cancel if cancel_hook else None)
    if epoch_hook is not None:
        request.epoch_guard = epoch_hook
    request.on_event = lambda kind, data: events.append((kind, data))

    from openbrep.runtime.modify_codex_bridge import run_codex_modify_agent_loop

    try:
        result = run_codex_modify_agent_loop(pipeline, request)
    finally:
        provider.close()
        harness.cleanup()
    return result, entered, release, finished, events, project


def test_timeout_late_write_is_rejected_and_isolated(tmp_path, monkeypatch):
    """工具超时 → turn 终止 → 释放阻塞点：迟到提交必须被拒，
    受管源码/参数/revision/编译结果保持终止快照。"""
    result, entered, release, finished, events, project = _run_blocked_update(monkeypatch, tmp_path)
    assert entered.wait(10), "阻塞点未到达"
    # 终止态已达成：执行返回（timeout 终止，无假成功）
    assert result.success is False, result.plain_text
    snapshot = _snapshot(project)
    assert result.metadata["codex_modify"]["tool_timeouts"] == 1
    assert result.metadata["execution"]["abandoned_write_workers"] == 1, result.metadata["execution"]

    # 释放阻塞点：迟到 worker 恢复并尝试提交
    release.set()
    assert finished.wait(15), "迟到 worker 未结束"
    after = _snapshot(project)
    assert after["fingerprint"] == snapshot["fingerprint"], "迟到写入污染了受管源码"
    assert after["scripts"] == snapshot["scripts"]
    assert after["params"] == snapshot["params"]
    assert after["revisions"] == snapshot["revisions"]
    # 迟到 worker 不得发出成功事件
    assert not any(kind == "tool_call" and data.get("ok") is True for kind, data in events), events


def test_cancel_late_write_is_rejected(tmp_path, monkeypatch):
    """取消信号（worker 阻塞期间触发）→ 终止 → 迟到提交被拒。"""
    entered_gate = threading.Event()

    def cancel_after_entry():
        return entered_gate.is_set()

    result, entered, release, finished, events, project = _run_blocked_update(
        monkeypatch, tmp_path, cancel_hook=cancel_after_entry,
    )
    assert entered.wait(10)
    entered_gate.set()  # worker 阻塞期间用户取消
    assert result.success is False
    snapshot = _snapshot(project)
    release.set()
    assert finished.wait(15)
    after = _snapshot(project)
    assert after["fingerprint"] == snapshot["fingerprint"]
    assert after["scripts"] == snapshot["scripts"], "迟到 worker 修改了共享 project"


def test_epoch_invalidation_blocks_late_write(tmp_path, monkeypatch):
    """项目代次失效 → 终止 → 迟到提交被拒。"""
    epoch_state = {"valid": True}

    def epoch_guard():
        return epoch_state["valid"]

    result, entered, release, finished, events, project = _run_blocked_update(
        monkeypatch, tmp_path, epoch_hook=epoch_guard,
    )
    assert entered.wait(10)
    epoch_state["valid"] = False
    assert result.success is False
    snapshot = _snapshot(project)
    release.set()
    assert finished.wait(15)
    after = _snapshot(project)
    assert after["fingerprint"] == snapshot["fingerprint"]
    assert after["scripts"] == snapshot["scripts"], "迟到 worker 修改了共享 project"


def test_next_task_unaffected_by_late_worker(tmp_path, monkeypatch):
    """迟到 worker 被隔离后，下一个任务在同一项目上的写入不受影响。"""
    result, entered, release, finished, events, project = _run_blocked_update(monkeypatch, tmp_path)
    assert entered.wait(10)
    assert result.success is False
    release.set()
    assert finished.wait(15)
    snapshot = _snapshot(project)

    # 下一个任务：真实桥、无阻塞，正常写入必须成功
    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [[
        _upd("BLOCK 7, 7, 7\nEND\n"),
        {"op": "final", "text": "第二次修改完成。"},
    ]])
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            second = pipeline.execute(_request(tmp_path, project, user_input="再加一层"))
        assert second.success, second.plain_text
        assert "BLOCK 7, 7, 7" in project.get_script(ScriptType.SCRIPT_3D)
        assert _snapshot(project)["fingerprint"] != snapshot["fingerprint"]
    finally:
        provider.close()
        harness.cleanup()


def test_read_tools_run_inline_and_write_tools_threaded(tmp_path):
    """读/编译工具不堆线程（内联执行），写入工具走有界线程。"""
    import threading as _threading

    from openbrep.runtime.modify_codex_bridge import CodexModifyTurnDriver
    from tests.fake_codex_modify_transport import (
        _DelegatingClient,
        _ServerRequestTransport,
        driver_tool_call,
        final_turn_frames,
    )

    main_thread = _threading.current_thread().ident
    seen: dict[str, int] = {}

    def executor(_call_id, _ns, tool, _args):
        seen[tool] = _threading.current_thread().ident
        return "ok", True

    transport = _ServerRequestTransport(
        notifications=final_turn_frames(),
        tool_calls=[driver_tool_call(1, "c-r", tool="read_parameters"),
                    driver_tool_call(2, "c-w", tool="update_script")],
    )
    driver = CodexModifyTurnDriver(
        client=_DelegatingClient(transport),
        model="gpt-5.6-luna",
        cwd=str(tmp_path),
        system_text="sys",
        dynamic_tools=[],
        executor=executor,
        timeout=30.0,
        should_cancel=None,
        on_delta=None,
        tool_timeout=5.0,
        threaded_tools=frozenset({"update_script", "patch_script", "edit_parameters"}),
    )
    outcome = driver.run("hi")
    assert outcome.finish_reason == "stop"
    assert seen["read_parameters"] == main_thread, "读工具必须内联执行"
    assert seen["update_script"] != main_thread, "写工具必须在有界线程中执行"


def test_task_deadline_shorter_than_tool_timeout_bounds_wait():
    """task 截止先到：工具等待按任务剩余预算退出，不按 tool_timeout 干等。"""
    import time as _time

    from openbrep.runtime.modify_codex_bridge import CodexModifyTurnDriver, TOOL_TIMEOUT_TEXT
    from tests.fake_codex_modify_transport import (
        _DelegatingClient,
        _ServerRequestTransport,
        _SteppingClock,
        driver_tool_call,
    )

    transport = _ServerRequestTransport(notifications=[], tool_calls=[
        driver_tool_call(1, "c-1", tool="update_script")])
    release = threading.Event()

    def blocked_executor(_cid, _ns, _tool, _args):
        release.wait(10)
        return "ok", True

    driver = CodexModifyTurnDriver(
        client=_DelegatingClient(transport),
        model="gpt-5.6-luna",
        cwd="/tmp/rf01-deadline",
        system_text="sys",
        dynamic_tools=[],
        executor=blocked_executor,
        timeout=90.0,
        should_cancel=None,
        on_delta=None,
        tool_timeout=600.0,
        task_deadline=2.0,  # 虚拟时钟下很快到期
        clock=_SteppingClock(step=0.5),
        threaded_tools=frozenset({"update_script"}),
    )
    start = _time.monotonic()
    outcome = driver.run("hi")
    wall = _time.monotonic() - start
    release.set()
    # 虚拟时钟下 task 截止（2s）远小于 tool 预算（600s）：按任务上限退出
    assert outcome.finish_reason == "timeout", (outcome.finish_reason, outcome.error)
    assert outcome.timeout_reason == "task_deadline"
    assert wall < 5, f"等待必须被任务剩余预算约束（实际 {wall:.1f}s）"
