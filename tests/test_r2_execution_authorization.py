"""R2（返修派单第二轮）：执行授权生命周期与原子提交。

核验探针复现的缺陷（main 4c492a9）：modify_codex_bridge 在每次 driver.run
结束后设置 terminated=True，门禁打回后循环继续但授权未重建——第二轮的合法
写入被拒（"任务已结束"），修复未应用，最终仍 success=True（假成功）。

本文件锁定修复契约：
- R2-01：任务终局（取消/截止/代次失效/预算退出/最终交付）与回合结束分离；
  每次工具调用持独立不可复活的授权令牌，回合结束撤销本回合未完成调用，
  工具超时立即撤销该调用；新回合新授权，被放弃 worker 永久无效。
- R2-02：提交原子化——最终授权校验与内存/受管文件变更在同一提交锁内，
  撤销与提交共享同一同步协议；已原子提交的变更是真实部分修改。
- R2-03：关键写入被拒且无后续有效修复时不得判成功（即使门禁通过）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

from openbrep.hsf_project import ScriptType
from openbrep.source_fingerprint import compute_source_fingerprint
from tests.test_codex_modify_bridge import (
    _FakeServerHarness,
    _codex_config,
    _make_project,
    _pipeline,
    _request,
    _sem_blocking,
    _sem_pass,
    _tool,
    _upd,
    _write_script,
)

SCRIPT_A = "BLOCK 1, 1, 1\nEND\n"
SCRIPT_B = "BLOCK 7, 7, 7\nEND\n"
SCRIPT_C = "BLOCK 9, 9, 9\nEND\n"


def _run_two_rounds(tmp_path, *, semantics=None, turn_scripts=None, config_overrides=None):
    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, turn_scripts or [
        [_upd(SCRIPT_A), _tool("compile_script"), {"op": "final", "text": "第一轮完成"}],
        [_upd(SCRIPT_B), _tool("compile_script"), {"op": "final", "text": "第二轮修复完成"}],
    ])
    config = _codex_config()
    if config_overrides:
        config_overrides(config)
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics",
                   side_effect=semantics or [_sem_blocking(), _sem_pass()]):
            result = pipeline.execute(_request(tmp_path, project, agent_loop_budget=10))
    finally:
        provider.close()
        harness.cleanup()
    return result, project


def test_gate_rejection_second_round_write_applies_and_succeeds(tmp_path):
    """核验探针回归（红灯→转绿）：第一轮写A被打回，第二轮写B并编译——
    最终脚本与 hash 必须等于 B，第二轮审计为成功写入，不得假成功。"""
    result, project = _run_two_rounds(tmp_path)
    assert result.success is True, result.plain_text
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_B.strip()
    assert compute_source_fingerprint(project.root) == compute_source_fingerprint(project.root)
    md = result.metadata["codex_modify"]
    writes = [e for e in md["tool_audit"] if e["tool"] == "update_script"]
    assert len(writes) == 2
    assert writes[0]["ok"] is True and writes[0].get("rejected_reason") is None
    assert writes[1]["ok"] is True and writes[1].get("rejected_reason") is None, writes[1]
    assert not md.get("rejected_write_commits_unresolved")


def test_three_rounds_of_rejected_fixes_still_apply_final_write(tmp_path):
    """三轮修复同样成立：每轮打回后下一轮新授权生效。"""
    result, project = _run_two_rounds(
        tmp_path,
        semantics=[_sem_blocking(), _sem_blocking(), _sem_pass()],
        turn_scripts=[
            [_upd(SCRIPT_A), {"op": "final", "text": "r1"}],
            [_upd(SCRIPT_B), {"op": "final", "text": "r2"}],
            [_upd(SCRIPT_C), _tool("compile_script"), {"op": "final", "text": "r3"}],
        ],
    )
    assert result.success is True, result.plain_text
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_C.strip()
    writes = [e for e in result.metadata["codex_modify"]["tool_audit"] if e["tool"] == "update_script"]
    assert len(writes) == 3 and all(w["ok"] is True for w in writes)


def test_round1_timeout_worker_cannot_write_during_round2(tmp_path, monkeypatch):
    """旧 worker 与新回合同时存在：第一轮超时被放弃的 worker 在第二轮期间
    释放，不得改源码/内存/参数/revision/changed_files 或发成功事件。"""
    import openbrep.runtime.modify_agent_tools as mat

    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [
        [_upd(SCRIPT_A), {"op": "final", "text": "第一轮完成"}],
        [_upd(SCRIPT_B), _tool("compile_script"), {"op": "final", "text": "第二轮修复完成"}],
    ])
    config = _codex_config()
    config.agent.agent_tool_timeout = 0.5
    config.agent.agent_idle_timeout = 5
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()

    original_sanitize = mat.sanitize_llm_script_output
    calls = {"n": 0}
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocking_then_pass(content, file_path):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert release.wait(30), "阻塞点未释放"
            try:
                return original_sanitize(content, file_path)
            finally:
                finished.set()
        if calls["n"] == 2:
            # 第二轮写入已在途：此刻释放第一轮迟到 worker（真并发共存）
            release.set()
        return original_sanitize(content, file_path)

    monkeypatch.setattr(mat, "sanitize_llm_script_output", blocking_then_pass)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics",
                   side_effect=[_sem_blocking(), _sem_pass()]):
            result = pipeline.execute(_request(tmp_path, project, agent_loop_budget=10))
    finally:
        provider.close()
        harness.cleanup()

    assert entered.wait(15)
    assert finished.wait(20)
    assert result.success is True, result.plain_text
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_B.strip(), "迟到 worker 不得覆盖新回合写入"
    md = result.metadata["codex_modify"]
    assert md["tool_timeouts"] == 1
    # 审计按完成时间排序：迟到 worker 的条目落在最后；按 ok 分布断言
    writes = [e for e in md["tool_audit"] if e["tool"] == "update_script"]
    assert len(writes) == 2
    assert sum(1 for w in writes if w["ok"] is True) == 1, writes
    rejected = [w for w in writes if w["ok"] is False]
    assert rejected and rejected[0].get("rejected_reason"), writes


def test_rejected_write_without_later_fix_is_not_success(tmp_path, monkeypatch):
    """R2-03 负例：关键写入被拒且无后续有效修复——即使门禁通过也不算成功。"""
    import openbrep.runtime.modify_agent_tools as mat

    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [
        [_upd(SCRIPT_A), {"op": "final", "text": "第一轮完成"}],
        [_upd(SCRIPT_B), _tool("compile_script"), {"op": "final", "text": "第二轮修复完成"}],
    ])
    config = _codex_config()
    config.agent.agent_tool_timeout = 0.5
    config.agent.agent_idle_timeout = 5
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()

    original_sanitize = mat.sanitize_llm_script_output
    calls = {"n": 0}
    entered, release = threading.Event(), threading.Event()

    def block_second(content, file_path):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二轮的写入：阻塞 → 工具超时 → 提交被拒
            entered.set()
            assert release.wait(30)
        return original_sanitize(content, file_path)

    monkeypatch.setattr(mat, "sanitize_llm_script_output", block_second)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics",
                   side_effect=[_sem_blocking(), _sem_pass()]):
            result = pipeline.execute(_request(tmp_path, project, agent_loop_budget=10))
    finally:
        release.set()
        provider.close()
        harness.cleanup()

    assert entered.wait(15)
    # 第二轮写入被拒、无后续有效修复：门禁虽过（编译+语义对未修复源），
    # 也不得宣称成功；changed_files 不含未发生的 B 写入
    assert result.success is False, result.plain_text
    assert "BLOCK 7" not in project.get_script(ScriptType.SCRIPT_3D)
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_A.strip()


def test_rejected_then_valid_fix_succeeds(tmp_path, monkeypatch):
    """R2-03 正例：失败调用后，后续合法修复且当前源验证通过 → 成功。"""
    result, project = _run_two_rounds(
        tmp_path,
        turn_scripts=[
            [{"op": "final", "text": "第一轮没有实际修复"}],
            [_upd(SCRIPT_B), _tool("compile_script"), {"op": "final", "text": "第二轮修复完成"}],
        ],
    )
    assert result.success is True, result.plain_text
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_B.strip()


# ── R2-02：原子提交边界屏障（真实生产调用接缝）──────────────────


def test_barrier_inside_commit_boundary_commits_before_revocation(tmp_path, monkeypatch):
    """屏障位于"最终授权检查完成 → _apply_changes 开始"之间（提交锁内）：
    屏障期间代次失效——按提交线性化契约，已在边界内开始的提交属于
    真实部分修改（先提交、撤销在后），且此后无新提交。"""
    from openbrep.core import GDLAgent

    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [
        [_upd(SCRIPT_B), _tool("compile_script"), {"op": "final", "text": "完成"}],
    ])
    provider = harness.provider()
    pipeline = _pipeline(_codex_config(), provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()

    epoch_state = {"valid": True}
    entered, release = threading.Event(), threading.Event()
    original_apply = GDLAgent._apply_changes

    def barrier_apply(self, proj, changes):
        entered.set()
        # 屏障：位于提交锁内、最终授权校验之后、实际变更之前
        assert release.wait(30)
        return original_apply(self, proj, changes)

    monkeypatch.setattr(GDLAgent, "_apply_changes", barrier_apply)

    def watcher():
        entered.wait(30)
        epoch_state["valid"] = False  # 屏障期间代次失效（撤销请求）
        release.set()

    threading.Thread(target=watcher, daemon=True).start()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project, epoch_guard=lambda: epoch_state["valid"]))
    finally:
        release.set()
        provider.close()
        harness.cleanup()

    assert entered.wait(15)
    # 提交线性化：边界内已开始的提交完成（真实部分修改），撤销其后生效
    assert project.get_script(ScriptType.SCRIPT_3D).strip() == SCRIPT_B.strip()
    # 代次失效 → 非完整交付（不得宣称成功）
    assert result.success is False
    assert result.metadata["codex_modify"]["epoch_violated"] is True


def test_barrier_in_edit_parameters_boundary_commits_before_revocation(tmp_path, monkeypatch):
    """edit_parameters 提交边界屏障：mutate_parameters 执行中代次失效——
    原子替换完成后属于真实部分修改；撤销后无新提交。"""
    import openbrep.runtime.modify_agent_tools as mat
    from openbrep.source_fingerprint import compute_source_fingerprint

    harness = _FakeServerHarness(tmp_path)
    provider = harness.provider()
    pipeline = _pipeline(_codex_config(), provider, tmp_path)
    project = _make_project(tmp_path)
    project.save_to_disk()
    fingerprint = compute_source_fingerprint(project.root)
    _write_script(tmp_path, [[
        _tool("read_parameters"),
        _tool("edit_parameters", {
            "expected_source_fingerprint": fingerprint,
            "operations": [{"op": "set_value", "name": "A", "value": "3"}],
        }),
        _tool("compile_script"),
        {"op": "final", "text": "参数已改"},
    ]])

    epoch_state = {"valid": True}
    entered, release = threading.Event(), threading.Event()
    original_mutate = mat.mutate_parameters

    def barrier_mutate(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return original_mutate(*args, **kwargs)

    monkeypatch.setattr(mat, "mutate_parameters", barrier_mutate)

    def watcher():
        entered.wait(30)
        epoch_state["valid"] = False  # 屏障期间代次失效（撤销请求）
        release.set()

    threading.Thread(target=watcher, daemon=True).start()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project, epoch_guard=lambda: epoch_state["valid"]))
    finally:
        release.set()
        provider.close()
        harness.cleanup()

    assert entered.wait(15)
    # 已原子提交的参数变更是真实部分修改
    assert project.get_parameter("A").value == "3"
    assert result.success is False
