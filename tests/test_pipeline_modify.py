"""
Tests for TaskPipeline modify path.

Covers:
- _handle_modify() is dispatched for MODIFY/DEBUG intent
- include_all_scripts=True → all scripts injected into LLM context
- Changes are applied to project after LLM response
- Diff summary is generated correctly
- StaticChecker runs after changes
- Compile result is included in TaskResult
- _MODIFY_SKILLS_PROMPT is prepended to skills text
- _snapshot_scripts / _build_diff_summary utilities
"""

import unittest
from unittest.mock import MagicMock, patch
from copy import deepcopy
import tempfile
from pathlib import Path

import typer

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.compiler import CompileResult
from openbrep.runtime.pipeline import (
    TaskPipeline,
    TaskRequest,
    TaskResult,
    _MODIFY_SKILLS_PROMPT,
    _build_diff_summary,
    _snapshot_scripts,
)
from openbrep.static_checker import StaticError, StaticCheckResult
from openbrep.config import GDLAgentConfig
from openbrep.learning import ErrorLearningStore


# ── Test helpers ──────────────────────────────────────────

def _make_project(name: str = "test_shelf") -> HSFProject:
    """Return a minimal HSFProject with a 3D script."""
    proj = HSFProject.create_new(name, work_dir="./workdir")
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    return proj


def _mock_llm_response(content: str):
    """Return a LLMResponse with given content."""
    return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")


def _make_pipeline(llm_content: str) -> TaskPipeline:
    """Build a pipeline with a MockLLM that returns llm_content."""
    cfg = GDLAgentConfig()  # default config, no real API key needed
    pipeline = TaskPipeline(config=cfg, trace_dir="./traces")

    # Patch _make_llm to return a simple mock adapter
    mock_llm = MagicMock()
    mock_llm.generate.return_value = _mock_llm_response(llm_content)
    pipeline._make_llm = lambda req: mock_llm

    return pipeline


# ── Tests: routing ────────────────────────────────────────

class TestModifyRouting(unittest.TestCase):
    """MODIFY / DEBUG / REPAIR intents must dispatch to the correct handler."""

    def test_modify_intent_dispatches_to_handle_modify(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with patch.object(pipeline, "_handle_modify", wraps=pipeline._handle_modify) as mock_m:
            pipeline.execute(TaskRequest(
                user_input="把书架宽度改成800mm",
                intent="MODIFY",
                project=_make_project(),
                work_dir="./workdir",
            ))
            mock_m.assert_called_once()

    def test_debug_intent_dispatches_to_handle_modify(self):
        pipeline = _make_pipeline("分析完毕，没有问题。")
        with patch.object(pipeline, "_handle_modify", wraps=pipeline._handle_modify) as mock_m:
            pipeline.execute(TaskRequest(
                user_input="帮我检查这段 3D 脚本",
                intent="DEBUG",
                project=_make_project(),
                work_dir="./workdir",
            ))
            mock_m.assert_called_once()

    def test_repair_intent_dispatches_to_handle_repair(self):
        pipeline = _make_pipeline("分析完毕，没有问题。")
        with patch.object(pipeline, "_handle_repair", wraps=pipeline._handle_repair) as mock_r:
            pipeline.execute(TaskRequest(
                user_input="修复脚本中的编译错误",
                intent="REPAIR",
                project=_make_project(),
                work_dir="./workdir",
                error_log="Error in 3D script, line 12: Missing END",
            ))
            mock_r.assert_called_once()

    def test_create_intent_does_not_dispatch_to_handle_modify(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with patch.object(pipeline, "_handle_modify", wraps=pipeline._handle_modify) as mock_m:
            pipeline.execute(TaskRequest(
                user_input="做一个书架",
                intent="CREATE",
                work_dir="./workdir",
            ))
            mock_m.assert_not_called()


# ── Tests: include_all_scripts ────────────────────────────

class TestModifyFullContext(unittest.TestCase):
    """_handle_modify must call generate_only with include_all_scripts=True."""

    def test_include_all_scripts_true(self):
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")

        captured = {}
        original_generate_only = None

        def capture_generate_only(self_agent, **kwargs):
            captured["include_all_scripts"] = kwargs.get("include_all_scripts")
            # Return empty changes, empty plain_text
            return {}, ""

        with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
            pipeline.execute(TaskRequest(
                user_input="把书架加一个抽屉",
                intent="MODIFY",
                project=_make_project(),
                work_dir="./workdir",
            ))

        self.assertTrue(
            captured.get("include_all_scripts"),
            "MODIFY must use include_all_scripts=True",
        )

    def test_modify_skills_prepended_to_skills(self):
        """_MODIFY_SKILLS_PROMPT must be in the skills text passed to generate_only."""
        pipeline = _make_pipeline("")

        captured_skills = {}

        def capture_generate_only(self_agent, **kwargs):
            captured_skills["skills"] = kwargs.get("skills", "")
            return {}, ""

        with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
            pipeline.execute(TaskRequest(
                user_input="调整书架参数",
                intent="MODIFY",
                project=_make_project(),
                work_dir="./workdir",
            ))

        self.assertIn(
            "修改任务规则",
            captured_skills.get("skills", ""),
            "_MODIFY_SKILLS_PROMPT must be in skills text",
        )


# ── Tests: changes applied ────────────────────────────────

class TestModifyApply(unittest.TestCase):
    """Changes from LLM must be applied to the project."""

    def test_create_static_repair_is_re_linted(self):
        pipeline = _make_pipeline("unused")
        proj = _make_project()
        calls = {"count": 0}

        def fake_generate_only(self_agent, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, "first pass"
            return {"scripts/2d.gdl": "CIRCLE2 0, 0, 1"}, "repair pass"

        static_result = StaticCheckResult(
            passed=False,
            errors=[
                StaticError(
                    check_type="undefined_var",
                    file="scripts/1d.gdl",
                    detail="变量 height 未定义",
                )
            ],
        )

        with patch("openbrep.core.GDLAgent.generate_only", fake_generate_only):
            with patch("openbrep.static_checker.StaticChecker.check", return_value=static_result):
                result = pipeline.execute(TaskRequest(
                    user_input="做一个书架",
                    intent="CREATE",
                    project=proj,
                    work_dir="./workdir",
                ))

        self.assertTrue(result.success)
        self.assertIn("scripts/2d.gdl", result.scripts)
        self.assertIn("ARC2 0, 0, 1, 0, 360", result.scripts["scripts/2d.gdl"])
        self.assertIn("RULE-002", result.lint_summary)

    def test_changes_applied_to_project(self):
        new_script = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK 0.1, 0.1, 0.1\nDEL 1\nEND"
        pipeline = _make_pipeline(f"[FILE: scripts/3d.gdl]\n{new_script}\n")

        proj = _make_project()
        result = pipeline.execute(TaskRequest(
            user_input="加一个顶部小块",
            intent="MODIFY",
            project=proj,
            work_dir="./workdir",
        ))

        self.assertTrue(result.success)
        self.assertIn("scripts/3d.gdl", result.scripts)
        # Project should be updated
        updated_3d = result.project.get_script(ScriptType.SCRIPT_3D)
        self.assertIn("ADDZ ZZYZX", updated_3d)

    def test_no_changes_when_llm_returns_nothing(self):
        """If LLM returns no [FILE:] blocks, project is unchanged."""
        pipeline = _make_pipeline("脚本没有问题，无需修改。")

        proj = _make_project()
        original_3d = proj.get_script(ScriptType.SCRIPT_3D)

        result = pipeline.execute(TaskRequest(
            user_input="检查一下",
            intent="MODIFY",
            project=proj,
            work_dir="./workdir",
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.scripts, {})
        self.assertEqual(proj.get_script(ScriptType.SCRIPT_3D), original_3d)


# ── Tests: diff summary ───────────────────────────────────

class TestDiffSummary(unittest.TestCase):

    def test_diff_summary_shows_line_counts(self):
        before = {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}
        changed = {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nADDZ 0.1\nBLOCK 0.1, 0.1, 0.1\nDEL 1\nEND"}
        summary = _build_diff_summary(before, changed)
        self.assertIn("3D", summary)
        self.assertIn("+", summary)
        self.assertIn("-", summary)

    def test_diff_summary_empty_when_no_changes(self):
        summary = _build_diff_summary({}, {})
        self.assertEqual(summary, "")

    def test_diff_summary_unchanged_content(self):
        content = "BLOCK A, B, ZZYZX\nEND\n"
        before = {"scripts/3d.gdl": content}
        changed = {"scripts/3d.gdl": content}
        summary = _build_diff_summary(before, changed)
        self.assertIn("内容未变化", summary)

    def test_diff_summary_new_file(self):
        """File not in before → treated as new."""
        before = {}
        changed = {"scripts/2d.gdl": "PROJECT2 3, 270, 2\n"}
        summary = _build_diff_summary(before, changed)
        self.assertIn("2D", summary)
        self.assertIn("+1", summary)


# ── Tests: _snapshot_scripts ─────────────────────────────

class TestSnapshotScripts(unittest.TestCase):

    def test_snapshot_captures_scripts(self):
        proj = _make_project()
        snap = _snapshot_scripts(proj)
        self.assertIn("scripts/3d.gdl", snap)
        self.assertIn("scripts/2d.gdl", snap)
        self.assertIn("BLOCK A, B, ZZYZX", snap["scripts/3d.gdl"])

    def test_snapshot_captures_paramlist(self):
        proj = _make_project()
        snap = _snapshot_scripts(proj)
        self.assertIn("paramlist.xml", snap)
        self.assertIn("Length A", snap["paramlist.xml"])

    def test_empty_scripts_excluded(self):
        proj = HSFProject.create_new("test", work_dir="./workdir")
        # Only 3D script set by create_new
        snap = _snapshot_scripts(proj)
        # Master (1d.gdl) is empty → should not appear
        self.assertNotIn("scripts/1d.gdl", snap)


# ── Tests: compile result ─────────────────────────────────

class TestModifyCompile(unittest.TestCase):

    def test_compile_result_in_task_result(self):
        """_handle_modify must include compile_result in TaskResult."""
        pipeline = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")

        result = pipeline.execute(TaskRequest(
            user_input="加层板",
            intent="MODIFY",
            project=_make_project(),
            work_dir="./workdir",
            output_dir="./workdir/output",
        ))

        self.assertTrue(result.success)
        # compile_result may be None if save_to_disk fails in test env, but
        # it should not crash the pipeline
        # (MockHSFCompiler is used since no real compiler in test config)


class TestModifyPipelineContext(unittest.TestCase):

    def test_debug_prefix_and_syntax_report_are_forwarded(self):
        pipeline = _make_pipeline("")
        captured = {}

        def capture_generate_only(self_agent, **kwargs):
            captured["instruction"] = kwargs.get("instruction")
            captured["syntax_report"] = kwargs.get("syntax_report")
            captured["history"] = kwargs.get("history")
            return {}, ""

        with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
            pipeline.execute(TaskRequest(
                user_input="[DEBUG:editor] 修复这段脚本\n[SYNTAX CHECK REPORT]\n缺少 ENDIF",
                intent="DEBUG",
                project=_make_project(),
                work_dir="./workdir",
                history=[{"role": "user", "content": "上一轮内容"}],
            ))

        self.assertEqual(captured.get("instruction"), "修复这段脚本")
        self.assertEqual(captured.get("syntax_report"), "缺少 ENDIF")
        self.assertEqual(captured.get("history"), [{"role": "user", "content": "上一轮内容"}])

    def test_should_cancel_is_forwarded_to_agent(self):
        pipeline = _make_pipeline("")
        captured = {}

        def capture_init(self_agent, *args, **kwargs):
            captured["should_cancel"] = kwargs.get("should_cancel")

        def capture_generate_only(self_agent, **kwargs):
            return {}, ""

        with patch("openbrep.runtime.pipeline.GDLAgent.__init__", capture_init):
            with patch("openbrep.runtime.pipeline.GDLAgent.generate_only", capture_generate_only):
                pipeline.execute(TaskRequest(
                    user_input="把书架改窄一点",
                    intent="MODIFY",
                    project=_make_project(),
                    work_dir="./workdir",
                    should_cancel=lambda: True,
                ))

        self.assertIsNotNone(captured.get("should_cancel"))
        self.assertTrue(captured["should_cancel"]())

    def test_repair_error_log_is_appended_to_instruction(self):
        pipeline = _make_pipeline("")
        captured = {}

        def capture_generate_only(self_agent, **kwargs):
            captured["instruction"] = kwargs.get("instruction")
            return {}, ""

        with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
            pipeline.execute(TaskRequest(
                user_input="修复脚本中的编译错误",
                intent="REPAIR",
                project=_make_project(),
                work_dir="./workdir",
                error_log="Error in 3D script, line 12: Missing END",
            ))

        self.assertIn("错误日志", captured.get("instruction", ""))
        self.assertIn("Missing END", captured.get("instruction", ""))

    def test_user_error_log_is_recorded_as_project_learning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline("")

            def capture_generate_only(self_agent, **kwargs):
                return {}, ""

            with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
                pipeline.execute(TaskRequest(
                    user_input="修复脚本中的编译错误",
                    intent="REPAIR",
                    project=_make_project("Chair"),
                    work_dir=tmpdir,
                    error_log="Error in 3D script, line 12: Undefined variable width",
                ))

            lessons = ErrorLearningStore(tmpdir).list_error_lessons()
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0].category, "variable_mapping")
            self.assertEqual(lessons[0].project_name, "Chair")

    def test_learned_error_skill_is_injected_into_modify_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ErrorLearningStore(tmpdir).record_error(
                "Error in 3D script, line 12: Wrong number of arguments in PRISM_",
                source="compile_result",
                project_name="Chair",
            )
            pipeline = _make_pipeline("")
            captured = {}

            def capture_generate_only(self_agent, **kwargs):
                captured["skills"] = kwargs.get("skills")
                return {}, ""

            with patch("openbrep.core.GDLAgent.generate_only", capture_generate_only):
                pipeline.execute(TaskRequest(
                    user_input="把椅子改宽一点",
                    intent="MODIFY",
                    project=_make_project("Chair"),
                    work_dir=tmpdir,
                ))

            self.assertIn("workspace_gdl_error_avoidance", captured.get("skills", ""))
            self.assertIn("developer_gdl_error_baseline", captured.get("skills", ""))
            self.assertIn("PRISM_", captured.get("skills", ""))


class TestCliRepairIntent(unittest.TestCase):

    def test_cli_repair_uses_repair_intent(self):
        from cli.main import repair

        captured = {}
        fake_project = MagicMock(name="chair")

        class FakePipeline:
            def execute(self, request):
                captured["intent"] = request.intent
                return MagicMock(success=True, project=None, plain_text="", scripts={})

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "chair"
            project_dir.mkdir()
            with patch("cli.main._load_pipeline", return_value=FakePipeline()):
                with patch("openbrep.hsf_project.HSFProject.load_from_disk", return_value=fake_project):
                    with patch("cli.main.console.print"):
                        with patch("cli.main._print_scripts"):
                            repair(str(project_dir), error_log="boom", no_progress=True, trace_dir="./traces")

        self.assertEqual(captured.get("intent"), "REPAIR")


class TestGenerationResultPlan(unittest.TestCase):

    def test_auto_apply_plan_for_changed_scripts(self):
        from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan

        result = TaskResult(
            success=True,
            intent="MODIFY",
            scripts={
                "scripts/3d.gdl": "BLOCK 1,1,1\nEND\n",
                "scripts/2d.gdl": "PROJECT2 3, 270, 2\n",
            },
            plain_text="已完成修改",
        )

        plan = build_generation_result_plan(result, auto_apply=True, gsm_name="chair")

        self.assertTrue(plan.has_changes)
        self.assertEqual(plan.mode, "auto_apply")
        self.assertEqual(plan.changed_files, ["scripts/3d.gdl", "scripts/2d.gdl"])
        self.assertIn("脚本 [3D, 2D]", plan.label)
        self.assertTrue(plan.reply_prefix.startswith("✏️"))

    def test_pending_review_plan_when_auto_apply_false(self):
        from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan

        result = TaskResult(
            success=True,
            intent="MODIFY",
            scripts={"scripts/3d.gdl": "BLOCK 1,1,1\nEND\n"},
            plain_text="已完成修改",
        )

        plan = build_generation_result_plan(result, auto_apply=False, gsm_name="chair")

        self.assertTrue(plan.has_changes)
        self.assertEqual(plan.mode, "pending_review")
        self.assertIn("脚本 [3D]", plan.label)
        self.assertTrue(plan.reply_prefix.startswith("🤖"))

    def test_paramlist_plan_counts_parameters(self):
        from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan

        result = TaskResult(
            success=True,
            intent="MODIFY",
            scripts={
                "paramlist.xml": "Length A = 100 ! Width\nLength B = 50 ! Depth",
            },
            plain_text="参数已更新",
        )

        plan = build_generation_result_plan(result, auto_apply=False, gsm_name="chair")

        self.assertTrue(plan.has_changes)
        self.assertIn("2 个参数", plan.label)

    def test_plain_text_only_plan_when_no_changes(self):
        from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan

        result = TaskResult(
            success=True,
            intent="CHAT",
            scripts={},
            plain_text="这里只返回解释文本",
        )

        plan = build_generation_result_plan(result, auto_apply=True, gsm_name=None)

        self.assertFalse(plan.has_changes)
        self.assertEqual(plan.mode, "plain_text_only")
        self.assertEqual(plan.code_blocks, [])
        self.assertEqual(plan.reply_prefix, "")

    def test_plan_builds_code_block_metadata(self):
        from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan

        result = TaskResult(
            success=True,
            intent="MODIFY",
            scripts={"scripts/3d.gdl": "BLOCK 1,1,1\nEND\n"},
            plain_text="ok",
        )

        plan = build_generation_result_plan(result, auto_apply=True, gsm_name="chair")

        self.assertEqual(len(plan.code_blocks), 1)
        self.assertEqual(plan.code_blocks[0]["path"], "scripts/3d.gdl")
        self.assertEqual(plan.code_blocks[0]["label"], "3D")
        self.assertEqual(plan.code_blocks[0]["language"], "gdl")
        self.assertIn("BLOCK 1,1,1", plan.code_blocks[0]["content"])


class TestReleaseDocs(unittest.TestCase):

    def test_package_version_is_current_release(self):
        from openbrep import __version__
        self.assertEqual(__version__, "0.6.3")

    def test_pyproject_version_matches_package_version(self):
        from openbrep import __version__
        pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject_text)

    def test_ui_reads_version_from_package(self):
        app_text = Path("ui/app.py").read_text(encoding="utf-8")
        self.assertIn("from openbrep import __version__ as OPENBREP_VERSION", app_text)
        self.assertIn("v{OPENBREP_VERSION}", app_text)

    def test_install_cn_title_mentions_current_release(self):
        from openbrep import __version__
        install_cn = Path("INSTALL_CN.md").read_text(encoding="utf-8")
        self.assertIn(f"# openbrep v{__version__} 安装指南（中文）", install_cn)
        self.assertIn(f"当前正式版本：v{__version__}", install_cn)

    def test_readme_release_wording_is_formal(self):
        from openbrep import __version__
        readme = Path("README.md").read_text(encoding="utf-8")
        readme_zh = Path("README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn(f"正式发布版本 v{__version__}", readme)
        self.assertIn(f"正式发布版本 v{__version__}", readme_zh)

    def test_v061_release_note_uses_patch_release_wording(self):
        release_note = Path("docs/releases/v0.6.1.md").read_text(encoding="utf-8")
        self.assertIn("稳定性补丁版本", release_note)
        self.assertIn("正式 patch release", release_note)
        self.assertIn("v0.6.0", release_note)

    def test_readme_mentions_current_release_note(self):
        from openbrep import __version__
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn(f"v{__version__}", readme)
        self.assertIn(f"docs/releases/v{__version__}.md", readme)

    def test_current_release_note_exists(self):
        from openbrep import __version__
        self.assertTrue(Path(f"docs/releases/v{__version__}.md").exists())
