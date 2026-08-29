"""MODIFY 预算制 agent loop（实验新路径）与 ToolRegistry 的合同测试。

全部使用 MockLLM 脚本化响应 + MockHSFCompiler，离线可跑，不打真实 API。
覆盖：
- 工具调用循环的正常终止（改脚本 → 编译 → 纯文本完成）
- 工具执行结果正确回填对话历史（role=tool / assistant tool_calls）
- 预算上限触发强制退出并如实报告
- 最终答复夹带 [FILE:] 块的兜底解析
- 开关默认关闭时行为与旧路径一致
- ToolRegistry 各工具的参数校验与薄封装语义
"""

from __future__ import annotations

import unittest

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry, normalize_script_path
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


# ── 公共构造 ──────────────────────────────────────────────

def _make_project(tmp_path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return proj


def _make_pipeline(mock_llm: MockLLM, tmp_path) -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _make_request(project: HSFProject, tmp_path, **overrides) -> TaskRequest:
    kwargs = dict(
        user_input="给书架加一层层板",
        intent="MODIFY",
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
        agent_loop=True,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


def _make_registry(project: HSFProject, tmp_path) -> ModifyToolRegistry:
    agent = GDLAgent(llm=MockLLM(), compiler=MockHSFCompiler())
    return ModifyToolRegistry(
        project=project,
        compiler=MockHSFCompiler(),
        output_gsm=str(tmp_path / "out" / f"{project.name}.gsm"),
        apply_changes=agent._apply_changes,
    )


# ── Agent loop 主流程 ─────────────────────────────────────

class TestAgentLoopFlow(unittest.TestCase):
    def test_normal_termination_applies_scripts_and_compiles(self):
        """改脚本 → 编译 → 纯文本完成：正常退出，变更落进工程与 TaskResult。"""
        new_3d = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "update_script", "arguments": {"file_path": "scripts/3d.gdl", "content": new_3d}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已加一层层板，编译通过。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(_make_project(self.tmp), self.tmp)

        result = pipeline.execute(request)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.compile_result)
        self.assertTrue(result.compile_result.success)
        self.assertIn("scripts/3d.gdl", result.scripts)
        self.assertIn("ADDZ ZZYZX", result.project.get_script(ScriptType.SCRIPT_3D))
        self.assertEqual(mock_llm.call_count, 3)
        self.assertIn("工具调用 2/10 次", result.plain_text)
        self.assertIn("LLM 调用 3 次", result.plain_text)
        self.assertIn("✅ 编译通过", result.plain_text)

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_tool_results_backfilled_into_conversation(self):
        """工具执行后，对话历史应包含 assistant tool_calls 消息与 role=tool 结果消息。"""
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "编译通过，无需改动。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        pipeline.execute(_make_request(_make_project(self.tmp), self.tmp))

        # 第二次调用时，历史里应有第一轮的 assistant tool_calls + tool 结果
        second_call_messages = mock_llm.call_history[1]
        assistant_msgs = [m for m in second_call_messages if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")]
        tool_msgs = [m for m in second_call_messages if isinstance(m, dict) and m.get("role") == "tool"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0]["tool_calls"][0]["function"]["name"], "compile_script")
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], assistant_msgs[0]["tool_calls"][0]["id"])
        self.assertIn("编译通过", tool_msgs[0]["content"])

    def test_budget_exhaustion_forces_exit_and_reports(self):
        """预算耗尽：强制退出，plain_text 如实报告"预算耗尽"。"""
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(_make_project(self.tmp), self.tmp, agent_loop_budget=2)

        result = pipeline.execute(request)

        self.assertEqual(mock_llm.call_count, 3)  # 第 3 轮发现预算用尽后不再执行工具
        self.assertIn("工具调用 2/2 次", result.plain_text)
        self.assertIn("预算耗尽", result.plain_text)
        self.assertIsNotNone(result.compile_result)

    def test_final_answer_file_blocks_applied_as_fallback(self):
        """AI 直接以 [FILE:] 块作答（不用工具）时，兜底解析应用并补跑最终编译。"""
        mock_llm = MockLLM(responses=[
            "已修复。\n[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        result = pipeline.execute(_make_request(_make_project(self.tmp), self.tmp))

        self.assertIn("scripts/3d.gdl", result.scripts)
        self.assertIn("BLOCK A, B, 0.5", result.project.get_script(ScriptType.SCRIPT_3D))
        # AI 从未调用 compile_script，loop 仍为报告补跑一次最终编译
        self.assertIsNotNone(result.compile_result)
        self.assertTrue(result.compile_result.success)

    def test_repair_intent_also_routes_to_agent_loop(self):
        """REPAIR intent 打开开关后同样走新路径。"""
        mock_llm = MockLLM(responses=["已分析，无需改动。"])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(_make_project(self.tmp), self.tmp, intent="REPAIR", error_log="IF/ENDIF mismatch")

        result = pipeline.execute(request)
        self.assertIn("Agent loop（实验路径）", result.plain_text)

    def test_planning_stage_emits_plan_event_and_guided_execution(self):
        """agent_loop_plan=True 时，LLM 先输出计划并流式发出 plan 事件，再执行修改。"""
        plan_json = (
            '{"intent_summary": "给书架加一层层板",'
            ' "affected_files": ["scripts/3d.gdl"],'
            ' "parameter_changes": [],'
            ' "strategy": "在 3D 脚本中追加一层层板几何"}'
        )
        new_3d = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        events: list[dict] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        mock_llm = MockLLM(responses=[
            plan_json,
            {"tool_calls": [{"name": "update_script", "arguments": {"file_path": "scripts/3d.gdl", "content": new_3d}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已按 plans 加了一层。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(
            _make_project(self.tmp), self.tmp, agent_loop_plan=True, on_event=on_event
        )

        result = pipeline.execute(request)

        self.assertTrue(result.success)
        self.assertIn("scripts/3d.gdl", result.scripts)
        plan_events = [e for e in events if e["type"] == "plan"]
        self.assertEqual(len(plan_events), 1)
        self.assertEqual(plan_events[0]["data"].get("intent_summary"), "给书架加一层层板")
        # planning + 3 轮主循环 = 4 次 LLM 调用
        self.assertEqual(mock_llm.call_count, 4)
        # plan 被注入对话历史，最后一次调用应包含计划文本
        last_call_messages = mock_llm.call_history[-1]
        self.assertTrue(any(plan_json in (m.get("content") or "") for m in last_call_messages))


class TestAgentLoopBudgetConfig(unittest.TestCase):
    """D12：[agent] agent_loop_budget 驱动非 codex agent loop 的预算（同旋钮）。

    断言矩阵：3 → 恰好 3 次；0/负数/字符串 → 各路径默认值（10）；999 →
    既有上限 clamp（20）；显式 request 预算优先于 config。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, config) -> tuple[TaskResult, MockLLM, TaskRequest]:
        """1 个 compile 工具响应（耗尽后 MockLLM 重复末条）：跑完整个预算。"""
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
        ])
        pipeline = TaskPipeline(config=config, trace_dir=str(self.tmp / "traces"))
        pipeline._make_llm = lambda _req: mock_llm
        pipeline._make_compiler = lambda: MockHSFCompiler()
        request = _make_request(_make_project(self.tmp), self.tmp, agent_loop_budget=0)
        result = pipeline.execute(request)
        return result, mock_llm, request

    def test_budget_three_from_config_exactly_three_tools(self):
        """agent_loop_budget = 3 → 非 codex loop 恰好 3 次工具预算后预算耗尽。"""
        config = GDLAgentConfig()
        config.agent.agent_loop_budget = 3
        result, mock_llm, request = self._run(config)
        self.assertEqual(request.agent_loop_budget, 3)  # 注入成功
        self.assertIn("工具调用 3/3 次", result.plain_text)
        self.assertIn("预算耗尽", result.plain_text)
        self.assertEqual(mock_llm.call_count, 4)  # 第 4 轮发现预算用尽不再执行工具

    def test_budget_zero_from_config_keeps_default_ten(self):
        """默认 0 → 不注入，走各路径既有默认 10。"""
        config = GDLAgentConfig()
        result, _mock_llm, request = self._run(config)
        self.assertEqual(request.agent_loop_budget, 0)
        self.assertIn("工具调用 10/10 次", result.plain_text)
        self.assertIn("预算耗尽", result.plain_text)

    def test_budget_negative_from_config_keeps_default_ten(self):
        """负数（直接塞入 dataclass）→ 不注入，默认 10。"""
        config = GDLAgentConfig()
        config.agent.agent_loop_budget = -5
        result, _mock_llm, request = self._run(config)
        self.assertEqual(request.agent_loop_budget, 0)
        self.assertIn("工具调用 10/10 次", result.plain_text)

    def test_budget_bool_from_config_keeps_default_ten(self):
        """bool（int 子类）→ 不注入，默认 10（防 True 被当作 1 误放大）。"""
        config = GDLAgentConfig()
        config.agent.agent_loop_budget = True  # type: ignore[assignment]
        result, _mock_llm, request = self._run(config)
        self.assertEqual(request.agent_loop_budget, 0)
        self.assertIn("工具调用 10/10 次", result.plain_text)

    def test_budget_string_from_toml_config_keeps_default_ten(self):
        """字符串从 toml 加载 → load 规范化 0 → 默认 10（全链路：load → pipeline）。"""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.toml"
            config_path.write_text('[agent]\nagent_loop_budget = "abc"\n', encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            self.assertEqual(config.agent.agent_loop_budget, 0)
            result, _mock_llm, request = self._run(config)
            self.assertEqual(request.agent_loop_budget, 0)
            self.assertIn("工具调用 10/10 次", result.plain_text)

    def test_budget_over_cap_from_config_clamped_to_twenty(self):
        """999 → 非 codex 路径既有上限 clamp 到 20。"""
        config = GDLAgentConfig()
        config.agent.agent_loop_budget = 999
        result, _mock_llm, request = self._run(config)
        self.assertEqual(request.agent_loop_budget, 999)
        self.assertIn("工具调用 20/20 次", result.plain_text)
        self.assertIn("预算耗尽", result.plain_text)

    def test_explicit_request_budget_wins_over_config(self):
        """调用方显式设置 request.agent_loop_budget > 0 时，config 不覆盖。"""
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        pipeline.config.agent.agent_loop_budget = 3
        request = _make_request(_make_project(self.tmp), self.tmp, agent_loop_budget=7)
        result = pipeline.execute(request)
        self.assertEqual(request.agent_loop_budget, 7)
        self.assertIn("工具调用 7/7 次", result.plain_text)
        self.assertIn("预算耗尽", result.plain_text)


class TestAgentLoopToggle(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_agent_loop_defaults_to_none_and_modify_enables_it(self):
        """TaskRequest 默认 agent_loop=None；pipeline 对 MODIFY 默认启用 agent loop。"""
        request = TaskRequest(user_input="x")
        self.assertIsNone(request.agent_loop)
        self.assertEqual(request.agent_loop_budget, 0)

        mock_llm = MockLLM(responses=["已分析，无需改动。"])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(_make_project(self.tmp), self.tmp)
        request.agent_loop = None  # 显式用默认值

        result = pipeline.execute(request)

        self.assertIn("Agent loop（实验路径）", result.plain_text)

    def test_default_path_unchanged_when_explicitly_off(self):
        """显式 agent_loop=False：走 _handle_script_update 旧路径，不出现 agent loop 标记。"""
        mock_llm = MockLLM(responses=["[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n"])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(_make_project(self.tmp), self.tmp, agent_loop=False)

        result = pipeline.execute(request)

        self.assertNotIn("Agent loop（实验路径）", result.plain_text)
        self.assertIn("BLOCK A, B, 0.5", result.project.get_script(ScriptType.SCRIPT_3D))


# ── ToolRegistry ──────────────────────────────────────────

class TestModifyToolRegistry(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_normalize_script_path(self):
        self.assertEqual(normalize_script_path("3d.gdl"), "scripts/3d.gdl")
        self.assertEqual(normalize_script_path("scripts/3d.gdl"), "scripts/3d.gdl")
        self.assertEqual(normalize_script_path("./scripts/2d.gdl"), "scripts/2d.gdl")
        self.assertEqual(normalize_script_path("paramlist.xml"), "paramlist.xml")

    def test_update_script_rejects_illegal_path_and_empty_content(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        bad_path = registry.execute(_call("update_script", {"file_path": "readme.md", "content": "x"}))
        self.assertFalse(bad_path.ok)
        self.assertIn("非法 file_path", bad_path.summary)
        empty = registry.execute(_call("update_script", {"file_path": "scripts/3d.gdl", "content": "  "}))
        self.assertFalse(empty.ok)

    def test_update_script_applies_paramlist_changes(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "paramlist.xml",
            "content": 'Length shelf_thk = 0.018 ! 层板厚度',
        }))
        self.assertTrue(result.ok)
        names = {p.name for p in project.parameters}
        self.assertIn("shelf_thk", names)
        self.assertIn("paramlist.xml", registry.changed_files)

    def test_compile_script_success_and_failure_recorded(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        ok_result = registry.execute(_call("compile_script", {}))
        self.assertTrue(ok_result.ok)
        self.assertTrue(registry.last_compile_result.success)

        # 人为制造 IF/ENDIF 失配 → mock 编译失败，错误信息回填
        project.scripts[ScriptType.SCRIPT_3D] = "IF A > 0 THEN\nBLOCK A, B, ZZYZX\nEND\n"
        fail_result = registry.execute(_call("compile_script", {}))
        self.assertFalse(fail_result.ok)
        self.assertIn("编译失败", fail_result.summary)
        self.assertIn("IF/ENDIF", fail_result.summary)

    def test_run_static_check_reports_pass_for_clean_project(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        result = registry.execute(_call("run_static_check", {}))
        self.assertTrue(result.ok)
        self.assertTrue(result.data["passed"])

    def test_query_knowledge_modes(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        bad_mode = registry.execute(_call("query_knowledge", {"query": "BLOCK", "mode": "nope"}))
        self.assertFalse(bad_mode.ok)
        empty_query = registry.execute(_call("query_knowledge", {"query": " ", "mode": "api"}))
        self.assertFalse(empty_query.ok)
        api_hit = registry.execute(_call("query_knowledge", {"query": "BLOCK", "mode": "api"}))
        self.assertTrue(api_hit.ok)
        self.assertIn("BLOCK", api_hit.summary)

    def test_preview_geometry_returns_mesh_summary(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        result = registry.execute(_call("preview_geometry", {}))
        self.assertTrue(result.ok)
        self.assertGreaterEqual(result.data["mesh_count"], 1)
        self.assertIn("包围盒", result.summary)

    def test_unknown_tool_degrades_gracefully(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        result = registry.execute(_call("fly_to_moon", {}))
        self.assertFalse(result.ok)
        self.assertIn("未知工具", result.summary)

    def test_tool_log_accumulates(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        registry.execute(_call("compile_script", {}))
        registry.execute(_call("run_static_check", {}))
        self.assertEqual([e["name"] for e in registry.tool_log], ["compile_script", "run_static_check"])


def _call(name: str, arguments: dict):
    from openbrep.llm import ToolCall
    return ToolCall(id=f"test_{name}", name=name, arguments=arguments)


if __name__ == "__main__":
    unittest.main()


# ── S3：完成门禁 ──────────────────────────────────────────

from unittest.mock import patch  # noqa: E402

from openbrep.semantic_verifier import (  # noqa: E402
    SemanticIssue,
    SemanticVerificationResult,
)


def _blocking_semantic():
    return SemanticVerificationResult(
        passed=False,
        issues=[SemanticIssue(check_type="mesh_empty", detail="几何为空", blocking=True)],
    )


class TestCompletionGate(unittest.TestCase):
    """AI 宣称完成时的结构化核验：编译 + 语义证据，未过打回（有界）。"""

    def test_gate_rejects_false_done_claim_then_accepts(self):
        """谎报完成 → 门禁打回（证据注入对话）→ 修复后再确认 → 放行。"""
        mock_llm = MockLLM(responses=[
            {"content": "改完了，没有问题。", "tool_calls": []},
            {"content": "这次真的修好了。", "tool_calls": []},
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_blocking_semantic(), SemanticVerificationResult(passed=True)],
        ):
            with __import__("tempfile").TemporaryDirectory() as tmp:
                from pathlib import Path
                tmp_path = Path(tmp)
                pipeline = _make_pipeline(mock_llm, tmp_path)
                result = pipeline.execute(_make_request(_make_project(tmp_path), tmp_path))

        self.assertEqual(mock_llm.call_count, 2)  # 第一次被打回，没有直接交付
        convo = str(mock_llm.call_history)
        self.assertIn("完成门禁未通过", convo)      # 证据打回了对话
        self.assertIn("mesh_empty", convo)
        self.assertIn("打回 1 次", result.plain_text)
        self.assertTrue(result.success)

    def test_gate_rejections_bounded(self):
        """连续谎报：打回 MAX_GATE_REJECTIONS 次后强制交付并如实标注。"""
        mock_llm = MockLLM(responses=[
            {"content": "完成了1", "tool_calls": []},
            {"content": "完成了2", "tool_calls": []},
            {"content": "完成了3", "tool_calls": []},
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_blocking_semantic(),
        ):
            with __import__("tempfile").TemporaryDirectory() as tmp:
                from pathlib import Path
                tmp_path = Path(tmp)
                pipeline = _make_pipeline(mock_llm, tmp_path)
                result = pipeline.execute(_make_request(_make_project(tmp_path), tmp_path))

        self.assertEqual(mock_llm.call_count, 3)
        self.assertIn("打回 2 次", result.plain_text)
        self.assertFalse(result.success)  # 语义失败如实进报告

    def test_gate_passes_clean_claim_without_rejection(self):
        mock_llm = MockLLM(responses=[{"content": "完成了", "tool_calls": []}])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=SemanticVerificationResult(passed=True),
        ):
            with __import__("tempfile").TemporaryDirectory() as tmp:
                from pathlib import Path
                tmp_path = Path(tmp)
                pipeline = _make_pipeline(mock_llm, tmp_path)
                result = pipeline.execute(_make_request(_make_project(tmp_path), tmp_path))

        self.assertEqual(mock_llm.call_count, 1)
        self.assertNotIn("打回", result.plain_text)
        self.assertTrue(result.success)

    def test_no_rejection_when_budget_cannot_fix(self):
        """预算已用尽时宣称完成：不再打回（没有预算修复），如实交付失败状态。"""
        mock_llm = MockLLM(responses=[
            {"content": "", "tool_calls": [{"name": "compile_script", "arguments": {}}]},
            {"content": "完成了", "tool_calls": []},
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_blocking_semantic(),
        ):
            with __import__("tempfile").TemporaryDirectory() as tmp:
                from pathlib import Path
                tmp_path = Path(tmp)
                pipeline = _make_pipeline(mock_llm, tmp_path)
                result = pipeline.execute(
                    _make_request(_make_project(tmp_path), tmp_path, agent_loop_budget=1)
                )

        self.assertNotIn("打回", result.plain_text)
        self.assertIn("完成门禁未通过", result.plain_text)
        self.assertFalse(result.success)


# ── T1：纯文本逃逸舱修复（门禁时序 A + 温和强制 B + 提示词消歧 C） ─────────

def _passing_semantics():
    from openbrep.semantic_verifier import SemanticVerificationResult
    return SemanticVerificationResult(passed=True)


class TestTextDeliveryEscapeHatch(unittest.TestCase):
    """纯文本夹带 [FILE:] 的逃逸舱修复：门禁评估改动后项目 + 零工具未验证打回。

    A：先应用 [FILE:] 再跑完成门禁（门禁评估改动后的项目）；
    B：零工具 + 文本夹带变更 = 未验证交付，复用有界打回并指引工具链；
    M18 类纯问答（无变更）不触发 B，照常交付。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, mock_llm, **overrides):
        pipeline = _make_pipeline(mock_llm, self.tmp)
        return pipeline.execute(_make_request(_make_project(self.tmp), self.tmp, **overrides))

    def test_text_file_changes_applied_before_gate_compile_break_rejected(self):
        """A：破坏编译的 [FILE:] 文本交付先落盘、再被门禁打回（含编译证据）。"""
        mock_llm = MockLLM(responses=[
            # 第一轮：纯文本夹带 [FILE:]，内容 IF/ENDIF 失配 → 落盘后编译必失败
            "修好了。\n[FILE: scripts/3d.gdl]\nIF A > 0.4 THEN\nBLOCK A, B, ZZYZX\nEND\n",
            # 第二轮：被迫回工具链，用 update_script + compile_script 修复
            {"tool_calls": [
                {"name": "update_script", "arguments": {
                    "file_path": "scripts/3d.gdl",
                    "content": "IF A > 0.4 THEN\nBLOCK A, B, ZZYZX\nENDIF\nEND\n",
                }},
                {"name": "compile_script", "arguments": {}},
            ]},
            "修复完成，编译通过。",
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_passing_semantics(),
        ):
            result = self._run(mock_llm)

        # 门禁评估的是改动后的项目：编译错误证据（IF/ENDIF mismatch）被打回对话
        convo = str(mock_llm.call_history)
        self.assertIn("完成门禁未通过", convo)
        self.assertIn("IF/ENDIF mismatch", convo)
        # 未验证交付提示与门禁证据合并打回
        self.assertIn("未经工具链验证", convo)
        self.assertEqual(mock_llm.call_count, 3)  # 没在第一轮放行
        self.assertTrue(result.success)
        self.assertIn("ENDIF", result.project.get_script(ScriptType.SCRIPT_3D))
        self.assertIn("打回 1 次", result.plain_text)

    def test_zero_tools_with_file_blocks_rejected_with_toolchain_guidance(self):
        """B：零工具 + [FILE:] 变更 → 打回一次并指引工具链；第二轮纯问答放行。"""
        mock_llm = MockLLM(responses=[
            # 第一轮：纯文本夹带 [FILE:]，改动本身能编译过——B 依然打回（未验证交付）
            "改好了。\n[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n",
            # 第二轮：纯文本无变更（问答式）→ 门禁过且非未验证交付 → 放行
            "好的，已确认。",
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_passing_semantics(),
        ):
            result = self._run(mock_llm)

        convo = str(mock_llm.call_history)
        self.assertIn("未经工具链验证", convo)
        self.assertIn("compile_script", convo)
        self.assertIn("patch_script", convo)
        # 门禁本身通过了，打回纯粹因为"未验证交付"（温和强制，不含门禁失败证据）
        self.assertNotIn("完成门禁未通过", convo)
        self.assertEqual(mock_llm.call_count, 2)
        self.assertIn("打回 1 次", result.plain_text)
        self.assertTrue(result.success)
        self.assertIn("BLOCK A, B, 0.5", result.project.get_script(ScriptType.SCRIPT_3D))

    def test_unverified_delivery_rejection_bounded_releases(self):
        """B：未验证交付打回有界（MAX_GATE_REJECTIONS），耗尽后照常放行。"""
        mock_llm = MockLLM(responses=[
            "改好了。\n[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n",
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_passing_semantics(),
        ):
            result = self._run(mock_llm)

        self.assertEqual(mock_llm.call_count, 3)  # 打回 2 次 + 第 3 轮放行
        self.assertIn("打回 2 次", result.plain_text)
        self.assertTrue(result.success)
        self.assertIn("BLOCK A, B, 0.5", result.project.get_script(ScriptType.SCRIPT_3D))

    def test_plain_qa_answer_without_changes_not_rejected(self):
        """M18 类：纯文本问答（无 [FILE:] 无变更）不触发 B，第一轮直接交付。"""
        mock_llm = MockLLM(responses=[
            "书架的高度由参数 ZZYZX 决定，默认值为 1.8 米，几何按该参数拉伸。",
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_passing_semantics(),
        ):
            result = self._run(mock_llm)

        self.assertEqual(mock_llm.call_count, 1)
        self.assertNotIn("打回", result.plain_text)
        self.assertTrue(result.success)
        self.assertEqual(result.scripts, {})  # 零变更

    def test_file_changes_applied_exactly_once_no_reapply_at_release(self):
        """改动只应用一次：每轮 [FILE:] 恰好应用一次，放行轮不重复应用。"""
        from openbrep.runtime import modify_agent_loop as mal

        real_apply = mal.GDLAgent._apply_changes
        counter = {"n": 0}

        def counting_apply(self, project, changes):
            counter["n"] += 1
            return real_apply(self, project, changes)

        mock_llm = MockLLM(responses=[
            "改好了。\n[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n",
        ])
        with unittest.mock.patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_passing_semantics(),
        ), unittest.mock.patch.object(mal.GDLAgent, "_apply_changes", counting_apply):
            result = self._run(mock_llm)

        # 三轮文本答复（打回 2 次 + 放行），每轮恰好应用一次，放行轮不重复应用
        self.assertEqual(mock_llm.call_count, 3)
        self.assertEqual(counter["n"], 3)
        self.assertTrue(result.success)


# ── P0：agent loop 修改前 revision 快照 ────────────────────

class TestAgentLoopBeforeRevision(unittest.TestCase):
    """建筑基础_v1 事故修复：agent loop 此前零快照，AI 全文重写直接覆盖打开的
    项目且无法回滚。现在首次实际改动前必须落 before-revision；零改动任务
    不产生空 revision。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _saved_project(self) -> HSFProject:
        proj = _make_project(self.tmp)
        proj.save_to_disk()
        return proj

    def test_first_mutation_creates_before_revision(self):
        """update_script 首次执行前：快照改动前内容；项目内容照常更新。"""
        from pathlib import Path
        new_3d = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "update_script", "arguments": {"file_path": "scripts/3d.gdl", "content": new_3d}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已加一层层板，编译通过。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        proj = self._saved_project()
        result = pipeline.execute(_make_request(proj, self.tmp))

        self.assertTrue(result.success)
        rev_dir = Path(proj.root) / ".openbrep" / "revisions" / "r0001"
        self.assertTrue(rev_dir.is_dir(), "首次写工具执行前应创建 before-revision")
        snap_3d = (rev_dir / "scripts" / "3d.gdl").read_text(encoding="utf-8")
        self.assertIn("BLOCK A, B, ZZYZX", snap_3d)
        self.assertNotIn("ADDZ ZZYZX", snap_3d)  # 快照必须是改动前的内容
        self.assertIn("ADDZ ZZYZX", proj.get_script(ScriptType.SCRIPT_3D))

    def test_text_fallback_changes_also_snapshot(self):
        """文本夹带 [FILE:] 的兜底应用路径同样先快照再应用。"""
        from pathlib import Path
        mock_llm = MockLLM(responses=[
            "改好了。\n[FILE: scripts/3d.gdl]\nBLOCK A, B, 0.5\nEND\n",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        proj = self._saved_project()
        pipeline.execute(_make_request(proj, self.tmp))

        rev_dir = Path(proj.root) / ".openbrep" / "revisions" / "r0001"
        self.assertTrue(rev_dir.is_dir(), "文本兜底改动也应先创建 before-revision")
        snap_3d = (rev_dir / "scripts" / "3d.gdl").read_text(encoding="utf-8")
        self.assertNotIn("BLOCK A, B, 0.5", snap_3d)

    def test_no_mutation_no_revision(self):
        """全程只读工具（compile）：不产生空 revision。"""
        from pathlib import Path
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "编译通过，无需改动。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        proj = self._saved_project()
        pipeline.execute(_make_request(proj, self.tmp))

        self.assertFalse((Path(proj.root) / ".openbrep" / "revisions").exists())
