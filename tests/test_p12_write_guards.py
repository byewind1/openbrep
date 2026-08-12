"""P12 agent loop 写盘守卫测试：GDL 散文拦截 + 字符串参数引用一致性。

覆盖：
- 散文守卫：update_script/patch_script 写盘前拦截 `## 标题` / markdown 表格 /
  `**粗体**` / 反引号行内代码；纯 `!` 注释里的同样文字放行；合法 GDL 放行；
  拦截时不写入
- 引用一致性：paramlist 字符串值 直棂→zhileng 且脚本仍引用 直棂 → 阻断并说明；
  同步把脚本的 IF/VALUES 也改掉 → 放行；数值参数改值 → 不触发
- 编译失败空错误消息回填（P12 顺带小修）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openbrep.compiler import CompileResult, MockHSFCompiler
from openbrep.core import GDLAgent
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import MockLLM, ToolCall
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry


def _make_project(tmp_path, name: str = "LuChuang") -> HSFProject:
    """带 String 参数 pattern_type（值 直棂）+ Length 参数 shelf_thk 的项目。

    3d.gdl 用 IF 比较、vl.gdl 用 VALUES 引用该参数——复刻 P12 漏窗事故形状。
    """
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = 'IF pattern_type = "直棂" THEN\nBLOCK A, B, ZZYZX\nENDIF\nEND\n'
    proj.scripts[ScriptType.PARAM] = 'VALUES "pattern_type" "直棂" "冰花"\n'
    proj.parameters.append(GDLParameter(name="pattern_type", type_tag="String", value="直棂"))
    proj.parameters.append(GDLParameter(name="shelf_thk", type_tag="Length", value="0.018"))
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


class TestProseGuard(unittest.TestCase):
    """守卫 1：update_script / patch_script 写盘前拦截 markdown 散文泄漏。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_update_script_blocks_md_heading(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "scripts/vl.gdl",
            "content": "## 关键修复说明\nVALUES \"pattern_type\" \"zhileng\"\n",
        }))
        self.assertFalse(result.ok)
        self.assertIn("散文泄漏", result.summary)
        self.assertIn("第 1 行", result.summary)
        self.assertIn("md_heading", result.summary)
        # 未写入
        self.assertEqual(project.get_script(ScriptType.PARAM), 'VALUES "pattern_type" "直棂" "冰花"\n')
        self.assertNotIn("scripts/vl.gdl", registry.changed_files)

    def test_update_script_blocks_md_table(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl",
            "content": "| 修复说明 | 内容 |\n| --- | --- |\n| 说明1 | 内容1 |\nBLOCK A, B, ZZYZX\nEND\n",
        }))
        self.assertFalse(result.ok)
        self.assertIn("散文泄漏", result.summary)
        self.assertIn("md_table", result.summary)
        self.assertNotIn("scripts/3d.gdl", registry.changed_files)

    def test_update_script_blocks_bold_and_inline_code(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        bold = registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl",
            "content": "BLOCK A, B, ZZYZX\n**关键修复说明：**\nEND\n",
        }))
        self.assertFalse(bold.ok)
        self.assertIn("md_bold", bold.summary)
        code = registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl",
            "content": "BLOCK A, B, ZZYZX\n`pattern_type = \"直棂\"`\nEND\n",
        }))
        self.assertFalse(code.ok)
        self.assertIn("md_inline_code", code.summary)
        # 两次都被拒绝，脚本保持原样
        self.assertIn('IF pattern_type = "直棂"', project.get_script(ScriptType.SCRIPT_3D))

    def test_prose_in_comment_lines_allowed(self):
        """合法 `!` 注释里的标题/表格/加粗文字不误伤。"""
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        content = (
            "! ## 关键修复说明\n"
            "! | 参数 | 说明 |\n"
            'BLOCK A, B, ZZYZX ! **粗体** 与 `行内代码` 都在注释里\n'
            "END\n"
        )
        result = registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl", "content": content,
        }))
        self.assertTrue(result.ok, msg=result.summary)
        written = project.get_script(ScriptType.SCRIPT_3D)
        self.assertIn("## 关键修复说明", written)
        self.assertIn("**粗体**", written)

    def test_legit_gdl_allowed(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "scripts/3d.gdl",
            "content": "BLOCK A, B, ZZYZX\nEND\n",
        }))
        self.assertTrue(result.ok)
        self.assertIn("scripts/3d.gdl", registry.changed_files)

    def test_patch_script_blocks_prose_all_or_nothing(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": "BLOCK A, B, ZZYZX\nENDIF\nEND", "new": "BLOCK A, B, ZZYZX\nENDIF\nEND\n## 修复说明\n"}],
        }))
        self.assertFalse(result.ok)
        self.assertIn("散文泄漏", result.summary)
        self.assertIn("全或无", result.summary)
        self.assertNotIn("## 修复说明", project.get_script(ScriptType.SCRIPT_3D))
        self.assertNotIn("scripts/3d.gdl", registry.changed_files)


class TestStringParamConsistency(unittest.TestCase):
    """守卫 2：paramlist 字符串参数值改动的脚本引用一致性检查。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_blocked_when_scripts_still_reference_old_value(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "paramlist.xml",
            "content": 'String pattern_type = "zhileng" ! 棂条样式\nLength shelf_thk = 0.018\n',
        }))
        self.assertFalse(result.ok)
        self.assertIn("zhileng", result.summary)
        self.assertIn("直棂", result.summary)
        self.assertIn("scripts/3d.gdl", result.summary)  # 说明旧值仍被哪个文件引用
        self.assertIn("scripts/vl.gdl", result.summary)
        # 未写入
        self.assertEqual(project.get_parameter("pattern_type").value, "直棂")
        self.assertNotIn("paramlist.xml", registry.changed_files)

    def test_blocked_when_only_values_references_old_value(self):
        """旧值只出现在 VALUES 行（无 IF 比较）同样阻断。"""
        project = _make_project(self.tmp)
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "paramlist.xml",
            "content": 'String pattern_type = "zhileng"\n',
        }))
        self.assertFalse(result.ok)
        self.assertIn("zhileng", result.summary)
        self.assertIn("scripts/vl.gdl", result.summary)

    def test_allowed_when_scripts_updated_in_sync(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        # 模型同步改了脚本引用
        registry.execute(_call("patch_script", {
            "file_path": "scripts/3d.gdl",
            "patches": [{"old": 'IF pattern_type = "直棂" THEN', "new": 'IF pattern_type = "zhileng" THEN'}],
        }))
        registry.execute(_call("patch_script", {
            "file_path": "scripts/vl.gdl",
            "patches": [{"old": 'VALUES "pattern_type" "直棂" "冰花"', "new": 'VALUES "pattern_type" "zhileng" "冰花"'}],
        }))
        result = registry.execute(_call("update_script", {
            "file_path": "paramlist.xml",
            "content": 'String pattern_type = "zhileng" ! 棂条样式\nLength shelf_thk = 0.018\n',
        }))
        self.assertTrue(result.ok, msg=result.summary)
        self.assertEqual(project.get_parameter("pattern_type").value, "zhileng")

    def test_numeric_param_change_not_triggered(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("patch_script", {
            "file_path": "paramlist.xml",
            "patches": [{"old": "Length shelf_thk = 0.018", "new": "Length shelf_thk = 0.025"}],
        }))
        self.assertTrue(result.ok, msg=result.summary)
        self.assertEqual(project.get_parameter("shelf_thk").value, "0.025")

    def test_string_param_value_unchanged_not_triggered(self):
        project = _make_project(self.tmp)
        registry = _make_registry(project, self.tmp)
        result = registry.execute(_call("update_script", {
            "file_path": "paramlist.xml",
            "content": 'String pattern_type = "直棂"\nLength shelf_thk = 0.018\n',
        }))
        self.assertTrue(result.ok, msg=result.summary)


class _SilentFailCompiler:
    """hsf2libpart 返回失败但 stderr/stdout 全空的编译器（事故现场形状）。"""

    def hsf2libpart(self, hsf_dir: str, output_gsm: str) -> CompileResult:
        return CompileResult(success=False, exit_code=3, stderr="", stdout="")


class TestCompileEmptyErrorBackfill(unittest.TestCase):
    """守卫 3（顺带）：编译失败但编译器无输出时回填明确标注。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_empty_error_backfilled_with_label(self):
        project = _make_project(self.tmp)
        agent = GDLAgent(llm=MockLLM(), compiler=MockHSFCompiler())
        registry = ModifyToolRegistry(
            project=project,
            compiler=_SilentFailCompiler(),
            output_gsm=str(self.tmp / "out" / f"{project.name}.gsm"),
            apply_changes=agent._apply_changes,
        )
        result = registry.execute(_call("compile_script", {}))
        self.assertFalse(result.ok)
        self.assertIn("编译失败", result.summary)
        self.assertIn("无错误输出", result.summary)
        self.assertIn("exit_code=3", result.summary)
        self.assertNotEqual(result.summary.strip().endswith("："), True)


if __name__ == "__main__":
    unittest.main()
