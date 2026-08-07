"""计划确认门（V3）后端测试：TaskRequest.confirm_plan / modify_agent_loop 计划调用 /
assistant_service pending_plan 生命周期 / /api/modify/confirm 语义。

覆盖（任务 V3 测试要求）：
- 计划调用 JSON 解析与失败回落（awaiting 不执行 / 坏 JSON 直接执行并注明）
- pending_plan 生命周期：生成存 session、approve 清除并带计划执行、
  reject 清除、无 pending 明确错误码、跨项目失效
- confirm_plan=False 零变化（agent loop 旧行为不变）
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.modify_agent_loop import _parse_confirm_plan
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, TaskResult
from openbrep.workbench.assistant_service import WorkbenchAssistantService


def _make_project(tmp_path: Path) -> HSFProject:
    proj = HSFProject.create_new("Shelf", work_dir=str(tmp_path))
    proj.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="2"))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.save_to_disk()
    return proj


def _make_pipeline(mock_llm, tmp_path: Path) -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _plan_json(**overrides) -> str:
    plan = {
        "intent_summary": "给书架加一层层板",
        "user_visible_changes": ["3D 几何会多出一层层板"],
        "affected_files": ["scripts/3d.gdl"],
        "risk": "几何形状变化",
    }
    plan.update(overrides)
    return json.dumps(plan, ensure_ascii=False)


def _modify_request(tmp_path: Path, **overrides) -> TaskRequest:
    kwargs = dict(
        user_input="给书架加一层层板",
        intent="MODIFY",
        project=_make_project(tmp_path),
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        agent_loop=True,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


class TestParseConfirmPlan(unittest.TestCase):
    def test_valid_plan_parsed(self):
        plan = _parse_confirm_plan(_plan_json())
        self.assertEqual(plan["intent_summary"], "给书架加一层层板")
        self.assertEqual(plan["user_visible_changes"], ["3D 几何会多出一层层板"])
        self.assertEqual(plan["affected_files"], ["scripts/3d.gdl"])
        self.assertEqual(plan["risk"], "几何形状变化")

    def test_bad_json_returns_none(self):
        for content in ("", "not json", "[1,2]", '{"foo": 1}', "``` not json ```"):
            self.assertIsNone(_parse_confirm_plan(content))

    def test_invalid_schema_returns_none(self):
        self.assertIsNone(_parse_confirm_plan(_plan_json(intent_summary="")))
        self.assertIsNone(_parse_confirm_plan(_plan_json(user_visible_changes=[])))
        self.assertIsNone(_parse_confirm_plan(_plan_json(user_visible_changes=["ok", 42])))
        self.assertIsNone(_parse_confirm_plan(_plan_json(affected_files="scripts/3d.gdl")))
        self.assertIsNone(_parse_confirm_plan(_plan_json(risk=123)))

    def test_code_fences_stripped(self):
        plan = _parse_confirm_plan("```json\n" + _plan_json() + "\n```")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["intent_summary"], "给书架加一层层板")


class TestPipelinePlanConfirmation(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_confirm_plan_true_returns_awaiting_without_modification(self):
        mock_llm = MockLLM(responses=[_plan_json()])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_modify_request(self.tmp, project=project, confirm_plan=True))

        self.assertTrue(result.metadata.get("awaiting_confirmation"))
        plan = result.metadata["pending_plan"]
        self.assertEqual(plan["intent_summary"], "给书架加一层层板")
        # 计划阶段不执行任何修改
        self.assertEqual(project.get_script(ScriptType.SCRIPT_3D), "BLOCK A, B, ZZYZX\nEND\n")
        self.assertEqual(mock_llm.call_count, 1)

    def test_confirm_plan_true_bad_json_falls_back_and_notes(self):
        mock_llm = MockLLM(responses=[
            "not json at all",  # 计划调用失败
            {"tool_calls": [{"name": "patch_script", "arguments": {"file_path": "scripts/3d.gdl", "patches": [{"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1"}]}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已加一层。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_modify_request(self.tmp, project=project, confirm_plan=True))

        self.assertFalse(result.metadata.get("awaiting_confirmation"))
        self.assertIn("计划生成失败", result.plain_text)
        self.assertIn("ADDZ ZZYZX", project.get_script(ScriptType.SCRIPT_3D))
        self.assertEqual(mock_llm.call_count, 4)

    def test_confirmed_plan_injected_without_replan_call(self):
        confirmed = json.loads(_plan_json())
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "patch_script", "arguments": {"file_path": "scripts/3d.gdl", "patches": [{"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1"}]}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已按确认的计划修改。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_modify_request(self.tmp, project=project, confirmed_plan=confirmed))

        self.assertFalse(result.metadata.get("awaiting_confirmation"))
        self.assertIn("ADDZ ZZYZX", project.get_script(ScriptType.SCRIPT_3D))
        # 3 次调用 = patch + compile + 最终答复；没有额外的重新规划调用
        self.assertEqual(mock_llm.call_count, 3)
        convo = str(mock_llm.call_history)
        self.assertIn("已确认的修改计划", convo)
        self.assertIn("给书架加一层层板", convo)

    def test_confirm_plan_false_unchanged(self):
        # confirm_plan=False：旧行为——无计划确认门，直接 agent loop
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "patch_script", "arguments": {"file_path": "scripts/3d.gdl", "patches": [{"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1"}]}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已加一层。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_modify_request(self.tmp, project=project))

        self.assertFalse(result.metadata.get("awaiting_confirmation"))
        self.assertEqual(mock_llm.call_count, 3)
        self.assertNotIn("计划生成失败", result.plain_text)

    def test_debug_intent_not_gated(self):
        # DEBUG 不走确认门：即使 confirm_plan=True 也不出 awaiting
        mock_llm = MockLLM(responses=["分析完毕，没有问题。"])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_modify_request(self.tmp, project=project, intent="DEBUG", confirm_plan=True))
        self.assertFalse(result.metadata.get("awaiting_confirmation"))


class TestPendingPlanLifecycle(unittest.TestCase):
    """service 层 pending_plan 生命周期 + /api/modify/confirm 语义。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def _session(self, pipeline_class=None, pending_plan=None):
        class _FakePipeline:
            def __init__(self, *a, **k):
                pass

        return SimpleNamespace(
            source_path=self.project.root,
            project=self.project,
            project_epoch=1,
            pipeline_class=pipeline_class or _FakePipeline,
            llm_model="mock",
            llm_api_key="",
            llm_api_base="",
            assistant_settings="",
            max_retries=5,
            pending_plan=pending_plan,
        )

    def _awaiting_pipeline(self):
        """fake pipeline：首请求返回 awaiting，执行请求返回正常结果，记录 request。"""
        project = self.project
        calls: list[TaskRequest] = []

        class FakePipeline:
            def __init__(self, *a, **k):
                pass

            def execute(self, request):
                calls.append(request)
                if request.confirm_plan and not request.confirmed_plan:
                    return TaskResult(
                        success=True, intent="MODIFY", project=project,
                        metadata={
                            "awaiting_confirmation": True,
                            "pending_plan": json.loads(_plan_json()),
                        },
                    )
                return TaskResult(
                    success=True, intent="MODIFY", project=project,
                    plain_text="✅ 已按确认的计划修改完成。",
                    metadata={},
                )

        FakePipeline.calls = calls
        return FakePipeline

    def test_plan_request_stores_pending_and_returns_awaiting(self):
        pipeline_class = self._awaiting_pipeline()
        session = self._session(pipeline_class=pipeline_class)
        service = WorkbenchAssistantService(session)
        response = service._generate_with_confirmation({
            "message": "给书架加一层层板", "intent": "MODIFY", "confirm_plan": True,
        })
        self.assertTrue(response["awaiting_confirmation"])
        self.assertEqual(response["pending_plan"]["intent_summary"], "给书架加一层层板")
        self.assertIsNotNone(session.pending_plan)
        self.assertEqual(session.pending_plan["plan"], response["pending_plan"])
        self.assertEqual(session.pending_plan["project_epoch"], 1)

    def test_confirm_approve_clears_pending_and_executes_with_plan(self):
        pipeline_class = self._awaiting_pipeline()
        session = self._session(
            pipeline_class=pipeline_class,
            pending_plan={"plan": json.loads(_plan_json()), "body": {"message": "给书架加一层层板", "intent": "MODIFY"}, "project_epoch": 1},
        )
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": True})
        self.assertTrue(response["ok"])
        self.assertIsNone(session.pending_plan)
        executed = pipeline_class.calls[-1]
        self.assertTrue(executed.confirmed_plan)
        self.assertFalse(executed.confirm_plan)

    def test_confirm_reject_clears_pending(self):
        session = self._session(
            pipeline_class=self._awaiting_pipeline(),
            pending_plan={"plan": {}, "body": {}, "project_epoch": 1},
        )
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": False})
        self.assertTrue(response["ok"])
        self.assertTrue(response["cancelled"])
        self.assertIsNone(session.pending_plan)

    def test_confirm_without_pending_returns_error_code(self):
        session = self._session(pipeline_class=self._awaiting_pipeline())
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": True})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "NO_PENDING_PLAN")

    def test_confirm_stale_epoch_invalidates_pending(self):
        session = self._session(
            pipeline_class=self._awaiting_pipeline(),
            pending_plan={"plan": {}, "body": {}, "project_epoch": 0},
        )
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": True})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "NO_PENDING_PLAN")
        self.assertIsNone(session.pending_plan)

    def test_confirm_approve_stream_returns_generator(self):
        pipeline_class = self._awaiting_pipeline()
        session = self._session(
            pipeline_class=pipeline_class,
            pending_plan={"plan": json.loads(_plan_json()), "body": {"message": "给书架加一层层板", "intent": "MODIFY"}, "project_epoch": 1},
        )
        service = WorkbenchAssistantService(session)
        stream = service.confirm_modify({"approve": True, "stream": True})
        self.assertTrue(hasattr(stream, "__next__"))  # generator
        self.assertIsNone(session.pending_plan)


if __name__ == "__main__":
    unittest.main()
