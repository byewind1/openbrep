"""
Tests for the verification seam (Phase 3/4/5).

Covers:
- VerificationReport / VerificationCheck / CheckStatus contract
- run_plan_validation_checks(): natural-language plan checks → executed pass/fail/unknown
- build_verification_report(): aggregation of static/lint/compile/plan checks
  - CREATE path (compile not run, reported honestly)
  - MODIFY path (compile pass / fail)
- Pipeline integration: result.verification populated, tracer records it
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import CompileResult
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.object_planner import GDLObjectPlan
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticIssue, SemanticVerificationResult
from openbrep.static_checker import StaticCheckResult, StaticError
from openbrep.verification import (
    CheckStatus,
    VerificationCheck,
    VerificationReport,
    build_verification_report,
    run_plan_validation_checks,
)


# ── helpers ───────────────────────────────────────────────

def _plan(validation_checks=None) -> GDLObjectPlan:
    """Build a minimal GDLObjectPlan with given validation_checks."""
    return GDLObjectPlan(
        object_type="test",
        validation_checks=validation_checks or [],
    )


def _project_with_3d(code: str = "BLOCK A, B, ZZYZX\nEND\n") -> HSFProject:
    proj = HSFProject.create_new("test_obj", work_dir="./workdir")
    proj.scripts[ScriptType.SCRIPT_3D] = code
    return proj


def _static(errors=None) -> StaticCheckResult:
    return StaticCheckResult(passed=not errors, errors=errors or [])


def _find_check(report: VerificationReport, check_type: str):
    for c in report.checks:
        if c.check_type == check_type:
            return c
    return None


# ── contract tests ─────────────────────────────────────────

class TestVerificationReportContract(unittest.TestCase):
    """VerificationReport derived views and serialization."""

    def test_passed_true_when_no_blocking_fail(self):
        r = VerificationReport(intent="CREATE", checks=[
            VerificationCheck("lint", "lint", CheckStatus.PASS),
            VerificationCheck("compile", "compile", CheckStatus.NOT_RUN),
        ])
        self.assertTrue(r.passed)

    def test_passed_false_on_static_fail(self):
        r = VerificationReport(intent="CREATE", checks=[
            VerificationCheck("static", "static", CheckStatus.FAIL),
        ])
        self.assertFalse(r.passed)

    def test_passed_false_on_compile_fail(self):
        r = VerificationReport(intent="MODIFY", checks=[
            VerificationCheck("compile", "compile", CheckStatus.FAIL),
        ])
        self.assertFalse(r.passed)

    def test_unknown_does_not_fail_passed(self):
        r = VerificationReport(intent="CREATE", checks=[
            VerificationCheck("custom", "plan_check", CheckStatus.UNKNOWN),
        ])
        self.assertTrue(r.passed)

    def test_counts(self):
        r = VerificationReport(intent="x", checks=[
            VerificationCheck("a", "static", CheckStatus.PASS),
            VerificationCheck("b", "lint", CheckStatus.FAIL),
            VerificationCheck("c", "plan_check", CheckStatus.UNKNOWN),
            VerificationCheck("d", "compile", CheckStatus.NOT_RUN),
        ])
        c = r.counts()
        self.assertEqual(c["pass"], 1)
        self.assertEqual(c["fail"], 1)
        self.assertEqual(c["unknown"], 1)
        self.assertEqual(c["not_run"], 1)

    def test_compile_status(self):
        r = VerificationReport(intent="x", checks=[
            VerificationCheck("compile", "compile", CheckStatus.PASS),
        ])
        self.assertEqual(r.compile_status(), "pass")

    def test_compile_status_absent_is_not_run(self):
        r = VerificationReport(intent="x", checks=[])
        self.assertEqual(r.compile_status(), "not_run")

    def test_to_dict_roundtrip(self):
        r = VerificationReport(
            intent="CREATE", goal="g",
            checks=[VerificationCheck("c", "static", CheckStatus.PASS)],
            confidence="medium",
        )
        d = r.to_dict()
        self.assertEqual(d["intent"], "CREATE")
        self.assertTrue(d["passed"])
        self.assertEqual(d["confidence"], "medium")
        self.assertEqual(len(d["checks"]), 1)
        self.assertEqual(d["checks"][0]["status"], "pass")

    def test_to_trace_dict_compact(self):
        r = VerificationReport(intent="x", checks=[
            VerificationCheck("c", "compile", CheckStatus.PASS),
            VerificationCheck("p", "plan_check", CheckStatus.UNKNOWN),
        ], errors_caught=["e1"], fixes_applied=["f1"])
        t = r.to_trace_dict()
        self.assertTrue(t["passed"])
        self.assertEqual(t["check_count"], 2)
        self.assertEqual(t["compile_status"], "pass")
        self.assertEqual(t["errors_caught_count"], 1)
        self.assertEqual(t["fixes_applied_count"], 1)

    def test_summary_text_contains_compile_label(self):
        r = VerificationReport(intent="x", checks=[
            VerificationCheck("compile", "compile", CheckStatus.NOT_RUN, detail="CREATE"),
        ])
        text = r.to_summary_text()
        self.assertIn("验证报告", text)
        self.assertIn("未执行", text)


# ── plan validation checks ─────────────────────────────────

class TestRunPlanValidationChecks(unittest.TestCase):
    """plan.validation_checks natural language → executed checklist."""

    def test_empty_plan_returns_empty(self):
        self.assertEqual(run_plan_validation_checks(None, None, None), [])
        self.assertEqual(run_plan_validation_checks(_plan(), None, None), [])

    def test_add_del_balance_pass(self):
        plan = _plan(validation_checks=["检查 ADD/DEL 是否平衡"])
        checks = run_plan_validation_checks(plan, _project_with_3d(), _static())
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, CheckStatus.PASS)

    def test_add_del_balance_fail(self):
        plan = _plan(validation_checks=["检查 ADD/DEL 是否平衡"])
        static = _static([StaticError("stack_imbalance", "scripts/3d.gdl", "push(2)!=pop(1)")])
        checks = run_plan_validation_checks(plan, _project_with_3d(), static)
        self.assertEqual(checks[0].status, CheckStatus.FAIL)
        self.assertIn("push(2)!=pop(1)", checks[0].detail)

    def test_for_next_pair(self):
        plan = _plan(validation_checks=["检查 FOR/NEXT 是否配对"])
        static = _static([StaticError("block_mismatch", "scripts/3d.gdl", "FOR/NEXT 不匹配：FOR=2, NEXT=1")])
        checks = run_plan_validation_checks(plan, None, static)
        self.assertEqual(checks[0].status, CheckStatus.FAIL)

    def test_if_endif_pair(self):
        plan = _plan(validation_checks=["检查 IF/ENDIF 配对"])
        static = _static([StaticError("block_mismatch", "scripts/3d.gdl", "IF/ENDIF 不匹配：IF=1, ENDIF=0")])
        checks = run_plan_validation_checks(plan, None, static)
        self.assertEqual(checks[0].status, CheckStatus.FAIL)

    def test_3d_ends_with_end_pass(self):
        plan = _plan(validation_checks=["检查 3D 脚本是否 END 结束"])
        checks = run_plan_validation_checks(plan, _project_with_3d("BLOCK A,B,ZZYZX\nEND\n"), _static())
        self.assertEqual(checks[0].status, CheckStatus.PASS)

    def test_3d_ends_with_end_fail(self):
        plan = _plan(validation_checks=["检查 3D 脚本末尾 END"])
        checks = run_plan_validation_checks(plan, _project_with_3d("BLOCK A,B,ZZYZX\n"), _static())
        self.assertEqual(checks[0].status, CheckStatus.FAIL)

    def test_3d_ends_with_end_unknown_when_empty(self):
        plan = _plan(validation_checks=["检查 3D 脚本是否 END 结束"])
        proj = HSFProject.create_new("x", work_dir="./workdir")
        proj.scripts[ScriptType.SCRIPT_3D] = ""  # explicit empty
        checks = run_plan_validation_checks(plan, proj, _static())
        self.assertEqual(checks[0].status, CheckStatus.UNKNOWN)

    def test_param_consistency_fail(self):
        plan = _plan(validation_checks=["检查参数表和脚本参数名是否一致"])
        static = _static([StaticError("undefined_var", "scripts/3d.gdl", "变量 'foo' 未声明")])
        checks = run_plan_validation_checks(plan, None, static)
        self.assertEqual(checks[0].status, CheckStatus.FAIL)

    def test_2d_visible_pass(self):
        plan = _plan(validation_checks=["检查 2D 脚本是否可见"])
        proj = _project_with_3d()
        proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
        checks = run_plan_validation_checks(plan, proj, _static())
        self.assertEqual(checks[0].status, CheckStatus.PASS)

    def test_2d_visible_fail(self):
        plan = _plan(validation_checks=["检查 2D 脚本是否可见"])
        proj = _project_with_3d()
        checks = run_plan_validation_checks(plan, proj, _static())
        self.assertEqual(checks[0].status, CheckStatus.FAIL)

    def test_unmatched_check_is_unknown(self):
        plan = _plan(validation_checks=["检查材质是否合理"])
        checks = run_plan_validation_checks(plan, _project_with_3d(), _static())
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, CheckStatus.UNKNOWN)
        self.assertIn("无自动化检查", checks[0].detail)

    def test_multiple_checks_mixed(self):
        plan = _plan(validation_checks=[
            "检查 ADD/DEL 是否平衡",
            "检查 FOR/NEXT 是否配对",
            "检查材质是否合理",  # unknown
        ])
        checks = run_plan_validation_checks(plan, _project_with_3d(), _static())
        self.assertEqual(len(checks), 3)
        self.assertEqual(checks[0].status, CheckStatus.PASS)
        self.assertEqual(checks[1].status, CheckStatus.PASS)
        self.assertEqual(checks[2].status, CheckStatus.UNKNOWN)


# ── build_verification_report ──────────────────────────────

class TestBuildVerificationReport(unittest.TestCase):
    """Aggregation into VerificationReport for CREATE and MODIFY paths."""

    def test_create_path_compile_not_run(self):
        plan = _plan(validation_checks=["检查 ADD/DEL 是否平衡"])
        r = build_verification_report(
            intent="CREATE", user_input="做一个书架",
            project=_project_with_3d(), object_plan=plan,
            static_result=_static(), lint_summary="",
            compile_result=None,
            compile_not_run_reason="CREATE 路径默认不执行编译验证",
        )
        self.assertTrue(r.passed)  # no blocking fail
        self.assertEqual(r.compile_status(), "not_run")
        self.assertIn("未编译验证", " ".join(r.remaining_risks))
        self.assertIn(r.confidence, ("low", "medium"))

    def test_create_path_with_unknown_plan_check(self):
        plan = _plan(validation_checks=["检查材质是否合理"])
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(), object_plan=plan,
            static_result=_static(), lint_summary="",
        )
        unknown_plan = [c for c in r.checks if c.check_type == "plan_check" and c.status == CheckStatus.UNKNOWN]
        self.assertEqual(len(unknown_plan), 1)
        self.assertIn("无自动化覆盖", " ".join(r.remaining_risks))

    def test_modify_path_compile_pass(self):
        r = build_verification_report(
            intent="MODIFY", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            compile_result=CompileResult(success=True),
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.compile_status(), "pass")
        self.assertEqual(r.confidence, "high")

    def test_modify_path_compile_fail(self):
        r = build_verification_report(
            intent="MODIFY", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            compile_result=CompileResult(success=False, stderr="Error: syntax"),
        )
        self.assertFalse(r.passed)
        self.assertEqual(r.compile_status(), "fail")
        self.assertEqual(r.confidence, "low")
        self.assertTrue(any("编译失败" in e for e in r.errors_caught))

    def test_static_fail_lowers_passed(self):
        static = _static([StaticError("undefined_var", "scripts/3d.gdl", "变量 'x' 未声明")])
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=static, lint_summary="",
        )
        self.assertFalse(r.passed)

    def test_lint_fixes_recorded(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(),
            lint_summary="🔧 Linter 修复了以下问题：\n- scripts/3d.gdl: 修复 3 处（rule_a）",
        )
        lint_chk = [c for c in r.checks if c.check_type == "lint"][0]
        self.assertEqual(lint_chk.status, CheckStatus.PASS)
        self.assertIn("3", lint_chk.detail)
        self.assertTrue(any("linter" in f for f in r.fixes_applied))

    def test_auto_repair_info_recorded(self):
        r = build_verification_report(
            intent="MODIFY", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            compile_result=CompileResult(success=True),
            auto_repair_info="🔧 自动修复后编译通过",
        )
        self.assertTrue(any("自动修复" in f for f in r.fixes_applied))

    def test_static_repair_triggered_recorded(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            static_repair_triggered=True,
        )
        self.assertTrue(any("静态检查自动修复" in f for f in r.fixes_applied))

    def test_semantic_result_none_omits_semantic_check(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
        )
        self.assertIsNone(_find_check(r, "semantic"))

    def test_semantic_pass_recorded(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            semantic_result=SemanticVerificationResult(passed=True),
        )
        semantic_chk = _find_check(r, "semantic")
        self.assertIsNotNone(semantic_chk)
        self.assertEqual(semantic_chk.status, CheckStatus.PASS)
        self.assertTrue(r.passed)

    def test_semantic_fail_lowers_passed_and_records_error(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            semantic_result=SemanticVerificationResult(
                passed=False,
                issues=[SemanticIssue(check_type="bbox_mismatch", detail="包围盒对不上")],
            ),
        )
        self.assertFalse(r.passed)
        semantic_chk = _find_check(r, "semantic")
        self.assertEqual(semantic_chk.status, CheckStatus.FAIL)
        self.assertTrue(any("bbox_mismatch" in e for e in r.errors_caught))

    def test_semantic_non_blocking_issue_becomes_remaining_risk_not_failure(self):
        r = build_verification_report(
            intent="CREATE", project=_project_with_3d(),
            static_result=_static(), lint_summary="",
            semantic_result=SemanticVerificationResult(
                passed=True,
                issues=[SemanticIssue(check_type="preview_error", detail="预览崩了", blocking=False)],
            ),
        )
        self.assertTrue(r.passed)
        semantic_chk = _find_check(r, "semantic")
        self.assertEqual(semantic_chk.status, CheckStatus.PASS)
        self.assertIn("预览崩了", r.remaining_risks)


# ── pipeline integration ───────────────────────────────────

def _make_pipeline(llm_content: str) -> TaskPipeline:
    from openbrep.config import GDLAgentConfig
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(content=llm_content, model="mock", usage={}, finish_reason="stop")
    pipeline._make_llm = lambda req: mock_llm
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda inst: ""
    return pipeline


class TestPipelineVerificationIntegration(unittest.TestCase):
    """End-to-end: pipeline.execute() produces result.verification."""

    def test_create_path_has_verification_with_compile_not_run(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("shelf", work_dir=tmpdir)
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, ""
                )
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(TaskRequest(
                    user_input="做一个书架",
                    intent="CREATE",
                    project=project,
                    work_dir=tmpdir,
                ))

        self.assertIsNotNone(result.verification)
        v = result.verification
        self.assertEqual(v["intent"], "CREATE")
        self.assertEqual(v["counts"]["not_run"], 1)  # compile not run
        compile_chk = [c for c in v["checks"] if c["check_type"] == "compile"][0]
        self.assertEqual(compile_chk["status"], "not_run")
        self.assertIn("验证报告", result.plain_text)

    def test_create_path_runs_semantic_verification_without_a_compiler(self):
        """Semantic verification uses the lightweight previewer, not
        LP_XMLConverter, so it must run and pass even when compile is
        NOT_RUN (no compiler configured)."""
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("shelf", work_dir=tmpdir)
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, ""
                )
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(TaskRequest(
                    user_input="做一个书架",
                    intent="CREATE",
                    project=project,
                    work_dir=tmpdir,
                ))

        semantic_chk = [c for c in result.verification["checks"] if c["check_type"] == "semantic"][0]
        self.assertEqual(semantic_chk["status"], "pass")

    def test_modify_path_has_verification_with_compile_status(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("chair", work_dir=tmpdir)
            project.save_to_disk()

            class OkCompiler:
                def hsf2libpart(self, *_a):
                    return CompileResult(success=True)

            pipeline._make_compiler = lambda: OkCompiler()

            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, ""
                )
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(TaskRequest(
                    user_input="加层板",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "out"),
                ))

        v = result.verification
        self.assertEqual(v["compile_status"] if "compile_status" in v else
                         [c for c in v["checks"] if c["check_type"] == "compile"][0]["status"],
                         "pass")
        self.assertTrue(v["passed"])

    def test_modify_path_compile_fail_verification(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("chair", work_dir=tmpdir)
            project.save_to_disk()

            class FailCompiler:
                def hsf2libpart(self, *_a):
                    return CompileResult(success=False, stderr="boom error", exit_code=1)

            pipeline._make_compiler = lambda: FailCompiler()

            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, ""
                )
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(TaskRequest(
                    user_input="加层板",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "out"),
                ))

        v = result.verification
        compile_chk = [c for c in v["checks"] if c["check_type"] == "compile"][0]
        self.assertEqual(compile_chk["status"], "fail")
        self.assertFalse(v["passed"])
        self.assertEqual(v["confidence"], "low")

    def test_trace_records_verification_summary(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("shelf", work_dir=tmpdir)
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, ""
                )
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(TaskRequest(
                    user_input="做一个书架",
                    intent="CREATE",
                    project=project,
                    work_dir=tmpdir,
                ))

            trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))

        self.assertIn("verification", trace)
        vt = trace["verification"]
        self.assertIsNotNone(vt)
        self.assertIn("passed", vt)
        self.assertIn("compile_status", vt)
        self.assertEqual(vt["compile_status"], "not_run")


if __name__ == "__main__":
    unittest.main()
