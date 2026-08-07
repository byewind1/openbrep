"""patch_script 局部编辑工具 + diff 范围护栏（任务 V2）测试。

覆盖：
- patch_script：单段命中、多段顺序应用（含行号/行数报告）、old 零匹配/多匹配
  拒绝、全或无、非法 file_path、空 patches、缺 old/new 字段、paramlist.xml
  简化参数行补丁、sanitize 清洗
- diff 护栏：变更占比计算（update_script 全量改写 = 1.0 / 小改动低占比）、
  update_script >50% 触发 advisory、patch_script 不触发、metadata 记录
- loop 端到端：mock LLM 用 patch_script 完成修改并通过完成门禁
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import MockLLM, ToolCall
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


def _make_project(tmp_path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    return proj


def _make_registry(project: HSFProject, tmp_path) -> ModifyToolRegistry:
    agent = GDLAgent(llm=MockLLM(), compiler=MockHSFCompiler())
    return ModifyToolRegistry(
        project=project,
        compiler=MockHSFCompiler(),
        output_gsm=str(tmp_path / "out" / f"{project.name}.gsm"),
        apply_changes=agent._apply_changes,
    )


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=f"test_{name}", name=name, arguments=arguments)


class TestPatchScriptTool(unittest.TestCase):
    def setUp(self):
        import tempfile as _t
        self._td = _t.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_single_patch_applied_with_line_report(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, 0.5"}],
        }))
        self.assertTrue(result.ok)
        self.assertIn("已应用 1 段补丁", result.summary)
        self.assertIn("第 1 行起", result.summary)
        self.assertIn("old 1 行 → new 1 行", result.summary)
        self.assertEqual(project.get_script(ScriptType.SCRIPT_3D), "BLOCK A, B, 0.5\nEND\n")
        self.assertEqual(registry.write_methods["scripts/3d.gdl"], "patch_script")
        self.assertIn("scripts/3d.gdl", registry.changed_files)

    def test_multi_patch_applied_in_order(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [
                {"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX"},
                {"old": "ADDZ ZZYZX", "new": "ADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1"},
            ],
        }))
        self.assertTrue(result.ok)
        self.assertIn("已应用 2 段补丁", result.summary)
        self.assertIn("patches[1]", result.summary)
        self.assertEqual(
            project.get_script(ScriptType.SCRIPT_3D),
            "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n",
        )

    def test_zero_match_rejected(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "GARBAGE 123", "new": "X"}],
        }))
        self.assertFalse(result.ok)
        self.assertIn("匹配 0 次", result.summary)
        self.assertIn("全或无", result.summary)
        self.assertEqual(project.get_script(ScriptType.SCRIPT_3D), "BLOCK A, B, ZZYZX\nEND\n")

    def test_multi_match_rejected(self):
        project = _make_project(self.tmp)
        project.scripts[ScriptType.SCRIPT_3D] = "FOO\nFOO\nBAR\n"
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "FOO", "new": "BAZ"}],
        }))
        self.assertFalse(result.ok)
        self.assertIn("匹配 2 次", result.summary)
        self.assertIn("提供更长的上下文", result.summary)

    def test_all_or_nothing_when_later_patch_fails(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [
                {"old": "BLOCK A, B, ZZYZX", "new": "BLOCK A, B, 0.5"},
                {"old": "NOT THERE", "new": "X"},
            ],
        }))
        self.assertFalse(result.ok)
        self.assertIn("patches[1]", result.summary)
        # 全或无：第一段即使能匹配也不生效
        self.assertEqual(project.get_script(ScriptType.SCRIPT_3D), "BLOCK A, B, ZZYZX\nEND\n")
        self.assertNotIn("scripts/3d.gdl", registry.changed_files)

    def test_illegal_file_path_rejected(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "readme.md", "patches": [{"old": "a", "new": "b"}],
        }))
        self.assertFalse(result.ok)
        self.assertIn("非法 file_path", result.summary)

    def test_empty_patches_and_missing_fields_rejected(self):
        registry = _make_registry(_make_project(self.tmp), self.tmp)
        empty = registry.execute(_call("patch_script", {"file_path": "scripts/3d.gdl", "patches": []}))
        self.assertFalse(empty.ok)
        self.assertIn("patches 为空", empty.summary)
        missing = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl", "patches": [{"old": "BLOCK A, B, ZZYZX"}],
        }))
        self.assertFalse(missing.ok)
        self.assertIn("缺少 old/new", missing.summary)

    def test_paramlist_patch_applies_and_invalid_format_rejected(self):
        project = _make_project(self.tmp)
        project.parameters.append(GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"))
        registry = _make_registry(project, self.tmp)
        ok_result = registry.execute(_call("patch_script", {
            "file_path": "paramlist.xml",
            "patches": [{"old": "Length shelf_thk = 0.018", "new": "Length shelf_thk = 0.025"}],
        }))
        self.assertTrue(ok_result.ok)
        self.assertEqual(project.get_parameter("shelf_thk").value, "0.025")
        self.assertEqual(registry.write_methods["paramlist.xml"], "patch_script")

        bad_result = registry.execute(_call("patch_script", {
            "file_path": "paramlist.xml",
            "patches": [{"old": "Length shelf_thk = 0.025", "new": "<?xml><broken"}],
        }))
        self.assertFalse(bad_result.ok)
        self.assertIn("格式不合法", bad_result.summary)

    def test_patch_content_sanitized(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "END", "new": "```\nEND\n```"}],
        }))
        self.assertTrue(result.ok)
        self.assertNotIn("```", project.get_script(ScriptType.SCRIPT_3D))


class TestDiffGuardrail(unittest.TestCase):
    def setUp(self):
        import tempfile as _t
        self._td = _t.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _four_line_project(self):
        project = _make_project(self.tmp)
        project.scripts[ScriptType.SCRIPT_3D] = "L1\nL2\nL3\nL4\n"
        return project

    def test_change_ratios_small_vs_full_rewrite(self):
        registry = _make_registry(self._four_line_project(), self.tmp)
        # 小改动：1/4 行变更
        registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl", "patches": [{"old": "L2", "new": "L2X"}],
        }))
        ratios = registry.change_ratios()
        self.assertAlmostEqual(ratios["scripts/3d.gdl"], 0.25, places=2)

        # 全量改写：所有行都变 → 1.0
        registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl", "content": "X1\nX2\nX3\nX4\n",
        }))
        ratios = registry.change_ratios()
        self.assertEqual(ratios["scripts/3d.gdl"], 1.0)

    def test_update_script_over_half_triggers_advisory(self):
        registry = _make_registry(self._four_line_project(), self.tmp)
        registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl", "content": "X1\nX2\nX3\nX4\n",
        }))
        warnings, ratios = registry.diff_scope_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("超过文件一半", warnings[0])
        self.assertIn("update_script", warnings[0])
        self.assertEqual(ratios["scripts/3d.gdl"], 1.0)

    def test_update_script_small_change_does_not_trigger(self):
        registry = _make_registry(self._four_line_project(), self.tmp)
        registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl", "content": "L1\nL2X\nL3\nL4\n",
        }))
        warnings, _ = registry.diff_scope_warnings()
        self.assertEqual(warnings, [])

    def test_patch_script_never_triggers_advisory(self):
        # patch_script 即使改动大也不触发（护栏只针对 update_script 全量替换）
        registry = _make_registry(self._four_line_project(), self.tmp)
        registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "L1\nL2\nL3\nL4", "new": "X1\nX2\nX3\nX4"}],
        }))
        warnings, ratios = registry.diff_scope_warnings()
        self.assertEqual(ratios["scripts/3d.gdl"], 1.0)
        self.assertEqual(warnings, [])


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


class TestPatchScriptAgentLoop(unittest.TestCase):
    """端到端：mock LLM 用 patch_script 完成修改并通过完成门禁。"""

    def setUp(self):
        import tempfile as _t
        self._td = _t.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_agent_loop_patch_script_end_to_end(self):
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "patch_script", "arguments": {
                "file_path": "scripts/3d.gdl",
                "patches": [
                    {"old": "BLOCK A, B, ZZYZX\nEND",
                     "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND"},
                ],
            }}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已用 patch_script 完成局部修改，编译通过。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_make_request(project, self.tmp))

        self.assertTrue(result.success)
        self.assertIsNotNone(result.compile_result)
        self.assertTrue(result.compile_result.success)
        self.assertIn("patch_script×1", result.plain_text)  # 工具记录摘要
        self.assertIn("scripts/3d.gdl", result.scripts)
        self.assertIn("ADDZ ZZYZX", project.get_script(ScriptType.SCRIPT_3D))
        # 护栏：patch_script 小改动不产生 advisory
        self.assertNotIn("diff 范围护栏", result.plain_text)
        guardrail = result.metadata["agent_loop"]["diff_guardrail"]
        self.assertEqual(guardrail["write_methods"]["scripts/3d.gdl"], "patch_script")
        self.assertEqual(guardrail["warnings"], [])

    def test_agent_loop_update_script_rewrite_emits_advisory(self):
        # 整文件重写（全部行都变）→ 完成门禁通过 → advisory 出现在最终结果与 metadata
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "update_script", "arguments": {
                "file_path": "scripts/3d.gdl",
                "content": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n",
            }}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已重写 3D 脚本。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        result = pipeline.execute(_make_request(project, self.tmp))

        self.assertTrue(result.success)
        self.assertIn("diff 范围护栏", result.plain_text)
        self.assertIn("超过文件一半", result.plain_text)
        guardrail = result.metadata["agent_loop"]["diff_guardrail"]
        self.assertEqual(guardrail["write_methods"]["scripts/3d.gdl"], "update_script")
        self.assertGreaterEqual(guardrail["ratios"]["scripts/3d.gdl"], 0.5)
        self.assertEqual(len(guardrail["warnings"]), 1)


if __name__ == "__main__":
    unittest.main()
