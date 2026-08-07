"""项目级 LLM 反馈信号采集（openbrep/feedback.py + 各写入点）测试。

覆盖（任务要求）：
- append_feedback：建目录/追加/异常静默/未落盘跳过/未知 kind/截断纪律
- 每个写入点一个测试（触发后读 <project_root>/.openbrep/feedback.jsonl 验证）：
  compile_failure / semantic_blocking（agent loop 完成门禁）
  semantic_repair_outcome（CREATE 语义修复闭环）
  dsl_fallback（参数级修改 DSL 回落，含 reason 透出）
  patch_failure（patch_script 匹配 0 次 / 多次）
  plan_rejected（confirm_modify approve=False）
- parse_param_modify 回落 reason 各分支 + 返回 None 语义不变回归
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.feedback import append_feedback
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse, MockLLM, ToolCall
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry
from openbrep.runtime.param_modify import parse_param_modify
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticIssue, SemanticVerificationResult
from openbrep.workbench.assistant_service import WorkbenchAssistantService

# ── 公共构造 ──────────────────────────────────────────────

def _make_project(tmp_path: Path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.save_to_disk()
    return proj


def _make_pipeline(mock_llm: MockLLM, tmp_path: Path) -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _make_request(project: HSFProject, tmp_path: Path, **overrides) -> TaskRequest:
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


def _make_registry(project: HSFProject, tmp_path: Path) -> ModifyToolRegistry:
    agent = GDLAgent(llm=MockLLM(), compiler=MockHSFCompiler())
    return ModifyToolRegistry(
        project=project,
        compiler=MockHSFCompiler(),
        output_gsm=str(tmp_path / "out" / f"{project.name}.gsm"),
        apply_changes=agent._apply_changes,
    )


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=f"test_{name}", name=name, arguments=arguments)


def _read_feedback(project: HSFProject) -> list[dict]:
    path = project.root / ".openbrep" / "feedback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _blocking_semantic() -> SemanticVerificationResult:
    return SemanticVerificationResult(
        passed=False,
        issues=[SemanticIssue(check_type="mesh_empty", detail="几何为空", blocking=True)],
    )


def _clean_semantic() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


# ── 1. append_feedback 模块契约 ───────────────────────────

class TestAppendFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_creates_dir_and_appends_jsonl(self):
        project = _make_project(self.tmp)
        fb_path = project.root / ".openbrep" / "feedback.jsonl"
        self.assertFalse(fb_path.exists())

        ok1 = append_feedback(project.root, {"kind": "compile_failure", "summary": "编译失败：A", "detail": {"error": "bad"}})
        ok2 = append_feedback(project.root, {"kind": "semantic_blocking", "summary": "几何为空", "detail": {"checks": ["mesh_empty"]}})

        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertTrue(fb_path.exists())
        lines = [json.loads(l) for l in fb_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["kind"], "compile_failure")
        self.assertEqual(lines[0]["project"], project.name)
        self.assertIn("ts", lines[0])
        self.assertEqual(lines[1]["kind"], "semantic_blocking")

    def test_unknown_kind_skipped(self):
        project = _make_project(self.tmp)
        ok = append_feedback(project.root, {"kind": "not_a_kind", "summary": "x"})
        self.assertFalse(ok)
        self.assertFalse((project.root / ".openbrep" / "feedback.jsonl").exists())

    def test_project_not_on_disk_skipped(self):
        project = HSFProject.create_new("NotSaved", work_dir=str(self.tmp))
        ok = append_feedback(project.root, {"kind": "compile_failure", "summary": "x"})
        self.assertFalse(ok)
        self.assertFalse((project.root / ".openbrep" / "feedback.jsonl").exists())
        # 完全不存在目录
        ok2 = append_feedback(self.tmp / "nope", {"kind": "compile_failure", "summary": "x"})
        self.assertFalse(ok2)

    def test_exception_silent_never_raises(self):
        project = _make_project(self.tmp)
        # 事件不是 dict → 跳过不抛
        self.assertFalse(append_feedback(project.root, "not a dict"))
        # summary 含不可序列化对象 → 兜底截断写入，不抛
        ok = append_feedback(project.root, {"kind": "compile_failure", "summary": 42, "detail": {"bad": object()}})
        self.assertTrue(ok)
        # 不可写路径（root 是文件）→ warning 不抛
        f = self.tmp / "afile"
        f.write_text("x")
        self.assertFalse(append_feedback(f, {"kind": "compile_failure", "summary": "x"}))

    def test_truncation_discipline(self):
        project = _make_project(self.tmp)
        append_feedback(project.root, {
            "kind": "compile_failure",
            "summary": "长" * 300,
            "detail": {"instruction": "指" * 300, "error": "e" * 900},
        })
        event = _read_feedback(project)[0]
        self.assertLessEqual(len(event["summary"]), 200)
        self.assertLessEqual(len(event["detail"]["instruction"]), 100)
        self.assertLessEqual(len(event["detail"]["error"]), 500)


# ── 2. agent loop：compile_failure / semantic_blocking ────

class TestAgentLoopFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_compile_failure_written_when_gate_finally_fails(self):
        """门禁最终未过且编译失败 → compile_failure（语义干净，不写 semantic_blocking）。"""
        project = _make_project(self.tmp)
        project.scripts[ScriptType.SCRIPT_3D] = "IF A > 0 THEN\nBLOCK A, B, ZZYZX\n"  # IF/ENDIF 失配
        mock_llm = MockLLM(responses=[
            {"content": "完成了1", "tool_calls": []},
            {"content": "完成了2", "tool_calls": []},
            {"content": "完成了3", "tool_calls": []},
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_clean_semantic(),
        ):
            result = pipeline.execute(_make_request(project, self.tmp))

        self.assertFalse(result.success)
        events = _read_feedback(project)
        kinds = [e["kind"] for e in events]
        self.assertIn("compile_failure", kinds)
        self.assertNotIn("semantic_blocking", kinds)
        cf = next(e for e in events if e["kind"] == "compile_failure")
        self.assertIn("IF/ENDIF", cf["summary"])
        self.assertLessEqual(len(cf["summary"]), 200)

    def test_semantic_blocking_written_when_gate_finally_fails(self):
        """门禁最终未过且语义 blocking → semantic_blocking（编译干净，不写 compile_failure）。"""
        project = _make_project(self.tmp)
        mock_llm = MockLLM(responses=[
            {"content": "完成了1", "tool_calls": []},
            {"content": "完成了2", "tool_calls": []},
            {"content": "完成了3", "tool_calls": []},
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_blocking_semantic(),
        ):
            result = pipeline.execute(_make_request(project, self.tmp))

        self.assertFalse(result.success)
        events = _read_feedback(project)
        kinds = [e["kind"] for e in events]
        self.assertIn("semantic_blocking", kinds)
        self.assertNotIn("compile_failure", kinds)
        sb = next(e for e in events if e["kind"] == "semantic_blocking")
        self.assertEqual(sb["detail"]["checks"], ["mesh_empty"])


# ── 3. pipeline：semantic_repair_outcome ──────────────────

class TestSemanticRepairOutcomeFeedback(unittest.TestCase):
    def _run_create(self, tmp_path: Path) -> HSFProject:
        from openbrep.compiler import CompileResult

        project = HSFProject.create_new("test_shelf", work_dir=str(tmp_path))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        cfg = GDLAgentConfig()
        cfg.compiler.path = "/fake/LP_XMLConverter"
        pipeline = TaskPipeline(config=cfg, trace_dir=str(tmp_path / "traces"))
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content=(
                "[FILE: scripts/3d.gdl]\nADDX 0.1\nBLOCK A, B, ZZYZX\nDEL 1\nEND\n"
                "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
            ),
            model="mock", usage={}, finish_reason="stop",
        )
        pipeline._make_llm = lambda _req: mock_llm
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = CompileResult(
            success=True, stdout="", stderr="", mode="lp", output_path="/tmp/t.gsm", exit_code=0,
        )
        pipeline._make_compiler = lambda: compiler_mock
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_blocking_semantic(), _clean_semantic()],
        ):
            result = pipeline.execute(TaskRequest(
                user_input="生成一个货架", intent="CREATE",
                project=project, work_dir=str(tmp_path), output_dir=str(tmp_path),
            ))
        self.assertTrue(result.success)
        return project

    def test_semantic_repair_outcome_written_when_attempted(self):
        project = self._run_create(self.tmp)
        events = _read_feedback(project)
        kinds = [e["kind"] for e in events]
        self.assertIn("semantic_repair_outcome", kinds)
        ev = next(e for e in events if e["kind"] == "semantic_repair_outcome")
        self.assertGreaterEqual(ev["detail"]["attempted"], 1)
        self.assertEqual(ev["detail"]["intent"], "CREATE")
        self.assertIn("语义修复跑了", ev["summary"])

    def test_no_event_when_repair_not_attempted(self):
        project = HSFProject.create_new("clean_shelf", work_dir=str(self.tmp))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        cfg = GDLAgentConfig()
        cfg.compiler.path = "/fake/LP_XMLConverter"
        pipeline = TaskPipeline(config=cfg, trace_dir=str(self.tmp / "traces"))
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content=(
                "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
                "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
            ),
            model="mock", usage={}, finish_reason="stop",
        )
        pipeline._make_llm = lambda _req: mock_llm
        compiler_mock = MagicMock()
        from openbrep.compiler import CompileResult
        compiler_mock.hsf2libpart.return_value = CompileResult(
            success=True, stdout="", stderr="", mode="lp", output_path="/tmp/t.gsm", exit_code=0,
        )
        pipeline._make_compiler = lambda: compiler_mock
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_clean_semantic(),
        ):
            result = pipeline.execute(TaskRequest(
                user_input="生成一个货架", intent="CREATE",
                project=project, work_dir=str(self.tmp), output_dir=str(self.tmp),
            ))
        self.assertTrue(result.success)
        events = _read_feedback(project)
        self.assertNotIn("semantic_repair_outcome", [e["kind"] for e in events])

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


# ── 4. pipeline：dsl_fallback（reason 透出 + 回落语义） ────

class TestDslFallbackFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_dsl_fallback_written_with_reason(self):
        project = _make_project(self.tmp)
        pipeline = _make_pipeline(MockLLM(), self.tmp)
        request = _make_request(project, self.tmp, user_input="把 shelf_count 改成 5 吗？")

        result = pipeline._try_param_modify(request)

        self.assertIsNone(result)  # 回落语义不变
        events = _read_feedback(project)
        dsl = [e for e in events if e["kind"] == "dsl_fallback"]
        self.assertEqual(len(dsl), 1)
        self.assertEqual(dsl[0]["detail"]["reason"], "question")
        self.assertLessEqual(len(dsl[0]["detail"]["instruction"]), 100)


# ── 5. patch_script：patch_failure ────────────────────────

class TestPatchFailureFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_zero_match_writes_patch_failure(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "NO SUCH TEXT ANYWHERE", "new": "x"}],
        }))
        self.assertFalse(result.ok)
        events = _read_feedback(project)
        pf = [e for e in events if e["kind"] == "patch_failure"]
        self.assertEqual(len(pf), 1)
        self.assertEqual(pf[0]["detail"]["match_count"], 0)
        self.assertEqual(pf[0]["detail"]["file_path"], "scripts/3d.gdl")

    def test_multi_match_writes_patch_failure(self):
        project = _make_project(self.tmp)
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nBLOCK A, B, ZZYZX\n"
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, 0.5"}],
        }))
        self.assertFalse(result.ok)
        events = _read_feedback(project)
        pf = [e for e in events if e["kind"] == "patch_failure"]
        self.assertEqual(len(pf), 1)
        self.assertEqual(pf[0]["detail"]["match_count"], 2)


# ── 6. assistant_service：plan_rejected ───────────────────

class TestPlanRejectedFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def _session(self, pending_plan=None):
        proj = self.project

        class _FakePipeline:
            def __init__(self, *a, **k):
                pass

            def execute(self, request):
                return type(
                    "R", (), {
                        "success": True, "intent": "MODIFY", "project": proj,
                        "plain_text": "✅ 已按确认的计划修改完成。",
                        "scripts": {}, "verification": {"checks": []}, "metadata": {},
                    },
                )()

        if pending_plan is None:
            pending_plan = {
                "plan": {"intent_summary": "给书架加一层层板", "user_visible_changes": ["x"], "affected_files": ["scripts/3d.gdl"], "risk": "无"},
                "body": {"message": "给书架加一层层板"},
                "project_epoch": 1,
            }
        return SimpleNamespace(
            source_path=self.project.root,
            project=self.project,
            project_epoch=1,
            pipeline_class=_FakePipeline,
            llm_model="mock",
            llm_api_key="",
            llm_api_base="",
            assistant_settings="",
            max_retries=5,
            pending_plan=pending_plan,
        )

    def test_confirm_reject_writes_plan_rejected(self):
        session = self._session()
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": False})

        self.assertTrue(response["ok"])
        self.assertTrue(response["cancelled"])
        self.assertIsNone(session.pending_plan)
        events = _read_feedback(self.project)
        pr = [e for e in events if e["kind"] == "plan_rejected"]
        self.assertEqual(len(pr), 1)
        self.assertEqual(pr[0]["detail"]["intent_summary"], "给书架加一层层板")

    def test_approve_writes_no_plan_rejected(self):
        session = self._session()
        service = WorkbenchAssistantService(session)
        response = service.confirm_modify({"approve": True})
        self.assertFalse(response.get("cancelled"))
        events = _read_feedback(self.project)
        self.assertNotIn("plan_rejected", [e["kind"] for e in events])


# ── 7. parse_param_modify reason 各分支 + 回落语义回归 ─────

class _FakeLLM:
    def __init__(self, content=None, error=False):
        self.content = content
        self.error = error
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise RuntimeError("boom")
        return LLMResponse(content=self.content or "", model="fake", usage={}, finish_reason="stop")


def _param_project() -> HSFProject:
    proj = HSFProject.create_new("Shelf", work_dir="./workdir")
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
    ]
    return proj


class TestParseParamModifyReasons(unittest.TestCase):
    def parse_with_reason(self, text: str, llm=None, project=None):
        reasons: list[str] = []
        plan = parse_param_modify(text, project or _param_project(), llm or _FakeLLM(), on_fallback=reasons.append)
        return plan, reasons

    def test_question_reason(self):
        plan, reasons = self.parse_with_reason("把 shelf_count 改成 5 吗？")
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["question"])

    def test_compound_reason(self):
        plan, reasons = self.parse_with_reason("把 shelf_count 改成 5，顺便加一层板")
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["compound"])

    def test_non_param_reason(self):
        plan, reasons = self.parse_with_reason("今天天气不错")
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["non_param"])

    def test_bad_json_reason(self):
        plan, reasons = self.parse_with_reason(
            "把 shelf_count 改成 5", llm=_FakeLLM(content="not json at all")
        )
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["bad_json"])

    def test_validation_reason(self):
        plan, reasons = self.parse_with_reason(
            "把 shelf_count 改成 5",
            llm=_FakeLLM(content=json.dumps({"operations": [{"op": "set_value", "param": "nope", "value": 1}]})),
        )
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["validation"])

    def test_llm_error_reason(self):
        plan, reasons = self.parse_with_reason("把 shelf_count 改成 5", llm=_FakeLLM(error=True))
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["llm_error"])

    def test_empty_or_no_params_non_param(self):
        plan, reasons = self.parse_with_reason("  ")
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["non_param"])
        empty_proj = HSFProject.create_new("Empty", work_dir="./workdir")
        empty_proj.parameters = []
        plan, reasons = self.parse_with_reason("把 shelf_count 改成 5", project=empty_proj)
        self.assertIsNone(plan)
        self.assertEqual(reasons, ["non_param"])

    def test_fallback_semantics_unchanged_success_still_returns_plan(self):
        """reason 透出不改变成功路径：合法 JSON 仍返回 ParamModifyPlan。"""
        reasons: list[str] = []
        llm = _FakeLLM(content=json.dumps({"operations": [{"op": "set_value", "param": "shelf_count", "value": 5}]}))
        plan = parse_param_modify("把 shelf_count 改成 5", _param_project(), llm, on_fallback=reasons.append)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.operations[0].op, "set_value")
        self.assertEqual(reasons, [])  # 成功不触发任何回落 reason

    def test_callback_exception_does_not_break_parse(self):
        def boom(_reason):
            raise RuntimeError("callback exploded")

        plan = parse_param_modify("把 shelf_count 改成 5 吗？", _param_project(), _FakeLLM(), on_fallback=boom)
        self.assertIsNone(plan)  # 回落语义不变


if __name__ == "__main__":
    unittest.main()
