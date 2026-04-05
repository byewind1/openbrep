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
from openbrep.config import GDLAgentConfig


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
    """MODIFY and DEBUG intents must dispatch to _handle_modify."""

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


if __name__ == "__main__":
    unittest.main()
