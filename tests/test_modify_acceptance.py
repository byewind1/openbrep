"""MODIFY 交付物升级（V5）测试：验收摘要生成器 + pipeline 三条路径携带 acceptance。

覆盖（任务 V5 测试要求）：
- 摘要生成器：参数变更句、几何变化句、算不出时不写、预览不可用降级、checks
- pipeline 端到端：micro_modify 命中、DSL 命中、agent loop 命中都带 acceptance；
  失败修改不带
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.modify_acceptance import (
    build_modify_acceptance,
    preview_geometry_summary,
)
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


class _CompileOk:
    success = True


class _CompileFail:
    success = False
    stderr = "boom"


def _base_summary(**overrides) -> dict:
    summary = {
        "available": True,
        "reason": "",
        "mesh_count": 1,
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 0.4, 1.8]},
        "line_count": 2,
        "polygon_count": 0,
        "circle_count": 0,
        "arc_count": 0,
    }
    summary.update(overrides)
    return summary


class TestBuildModifyAcceptance(unittest.TestCase):
    def test_parameter_change_sentence(self):
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            parameter_changes=[{"name": "shelf_count", "from": "2", "to": "5"}],
            changed_files=["paramlist.xml"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        self.assertIn("参数 shelf_count 从 2 改为 5", acc["summary_lines"])
        self.assertIn("已修改文件：paramlist.xml", acc["summary_lines"])

    def test_geometry_change_sentences(self):
        after = _base_summary(
            mesh_count=2,
            bbox={"min": [0.0, 0.0, 0.0], "max": [1.0, 0.4, 2.0]},
            line_count=3,
        )
        acc = build_modify_acceptance(
            before=_base_summary(), after=after,
            compile_result=_CompileOk(), semantic_issues=[],
        )
        self.assertIn("几何体数量从 1 变为 2", acc["summary_lines"])
        self.assertIn("包围盒尺寸（宽×深×高）从 1×0.4×1.8 变为 1×0.4×2", acc["summary_lines"])
        self.assertIn("平面元素数量（线/多边形/圆/弧）从 2/0/0/0 变为 3/0/0/0", acc["summary_lines"])
        delta = acc["geometry_delta"]
        self.assertEqual(delta["status"], "ok")
        self.assertEqual(delta["mesh_count"], {"from": 1, "to": 2})
        self.assertEqual(delta["bbox_size"]["from"], [1.0, 0.4, 1.8])
        self.assertEqual(delta["counts_2d"]["from"]["lines"], 2)

    def test_unchanged_geometry_writes_nothing_fabricated(self):
        # HF3：几何未变文案改为中性表述，不得读成"未变化 = 失败"。
        # 传 changed_files 是非零产出场景（AC-2 去重只作用于零产出）。
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            changed_files=["scripts/3d.gdl"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertIn("当前参数下几何未变化", joined)
        self.assertNotIn("几何未发生变化", joined)
        self.assertEqual(acc["geometry_delta"]["status"], "unchanged")
        self.assertNotIn("mesh_count", acc["geometry_delta"])

    def test_unchanged_geometry_with_param_definition_update_shows_hint(self):
        """HF3：新增枚举类 MODIFY（vl.gdl/paramlist.xml 落盘 + 几何未变）→
        中性文案 + 参数面板提示（不呈现失败意味）。"""
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            changed_files=["scripts/3d.gdl", "scripts/vl.gdl"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertIn("当前参数下几何未变化", joined)
        self.assertIn("参数/枚举定义已更新（vl.gdl）", joined)
        self.assertIn("在参数面板切换新选项可查看效果", joined)
        self.assertEqual(acc["geometry_delta"]["status"], "unchanged")

    def test_unchanged_geometry_without_param_files_keeps_neutral_only(self):
        """HF3：仅脚本变更且几何未变 → 只保留中性句，不猜参数面提示。"""
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            changed_files=["scripts/3d.gdl"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertIn("当前参数下几何未变化", joined)
        self.assertNotIn("参数/枚举定义已更新", joined)

    def test_paramlist_xml_also_triggers_param_hint(self):
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            changed_files=["paramlist.xml"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        self.assertIn("参数/枚举定义已更新（paramlist.xml）", "\n".join(acc["summary_lines"]))

    def test_zero_changed_files_shows_prominent_warning_first(self):
        """AC-2（HF4）：零 changed_files 交付 → 验收摘要首行显性中性警示，
        绝不呈现为"已成功修改"（真实事件：模型误解指代指令 → 零文件零 [FILE:]）。"""
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            compile_result=_CompileOk(), semantic_issues=[],
        )
        lines = acc["summary_lines"]
        self.assertTrue(lines)
        self.assertEqual(
            lines[0],
            "本次未修改任何文件（如预期有修改，请检查指令或重试）",
        )
        # 门禁判定不变：零变更 + compile 绿仍是 success，只动呈现层
        self.assertEqual(acc["geometry_delta"]["status"], "unchanged")

    def test_zero_changed_files_dedups_hf3_geometry_unchanged(self):
        """AC-2 与 HF3 并存：零产出 + 几何未变 → 只保留 AC-2 首行，
        去掉"当前参数下几何未变化"，两条"什么都没发生"语义不并列。"""
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertIn("本次未修改任何文件", joined)
        self.assertNotIn("当前参数下几何未变化", joined)

    def test_nonzero_changed_files_keeps_hf3_geometry_line(self):
        """非零产出（HF3 既有行为）：几何未变文案原样保留，不回归。"""
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            changed_files=["scripts/3d.gdl"],
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertNotIn("本次未修改任何文件", joined)
        self.assertIn("当前参数下几何未变化", joined)

    def test_zero_changed_files_still_shows_geometry_change(self):
        """零文件但几何真实变化（罕见预览差异）：变化行仍如实呈现，不退化成模糊。"""
        after = _base_summary(mesh_count=3)
        acc = build_modify_acceptance(
            before=_base_summary(), after=after,
            compile_result=_CompileOk(), semantic_issues=[],
        )
        joined = "\n".join(acc["summary_lines"])
        self.assertEqual(
            acc["summary_lines"][0],
            "本次未修改任何文件（如预期有修改，请检查指令或重试）",
        )
        self.assertIn("几何体数量从 1 变为 3", joined)

    def test_before_preview_unavailable_degrades(self):
        before = _base_summary(available=False, reason="3D 预览不可用：boom")
        acc = build_modify_acceptance(
            before=before, after=_base_summary(),
            compile_result=_CompileOk(), semantic_issues=[],
        )
        self.assertIn("修改前预览不可用", acc["summary_lines"])
        # AC-2：零产出首行警示 + 预览诊断行并存（诊断不是易混文案，不去重）
        self.assertEqual(
            acc["summary_lines"][0],
            "本次未修改任何文件（如预期有修改，请检查指令或重试）",
        )
        self.assertEqual(acc["geometry_delta"]["status"], "before_unavailable")

    def test_after_preview_unavailable_degrades(self):
        acc = build_modify_acceptance(
            before=_base_summary(),
            after=_base_summary(available=False, reason="2D 预览不可用：x"),
            compile_result=_CompileOk(), semantic_issues=[],
        )
        self.assertIn("修改后预览不可用", acc["summary_lines"])
        self.assertEqual(acc["geometry_delta"]["status"], "after_unavailable")

    def test_checks_reflect_compile_semantic_revision(self):
        acc = build_modify_acceptance(
            before=_base_summary(), after=_base_summary(),
            parameter_changes=[{"name": "A", "from": "1", "to": "2"}],
            compile_result=_CompileFail(), semantic_issues=["几何为空"],
            revision_id="r0009",
        )
        by_name = {c["name"]: c for c in acc["checks"]}
        self.assertEqual(by_name["compile"]["status"], "fail")
        self.assertEqual(by_name["semantic"]["status"], "fail")
        self.assertEqual(by_name["semantic"]["detail"], "1 个阻塞问题")
        self.assertEqual(by_name["revision"]["status"], "pass")
        self.assertIn("r0009", by_name["revision"]["detail"])

    def test_checks_revision_skipped_and_preview_degraded(self):
        acc = build_modify_acceptance(
            before=None, after=None,
            compile_result=None, semantic_issues=None, revision_id=None,
        )
        by_name = {c["name"]: c for c in acc["checks"]}
        self.assertEqual(by_name["compile"]["status"], "not_run")
        self.assertEqual(by_name["semantic"]["status"], "not_run")
        self.assertEqual(by_name["revision"]["status"], "skipped")
        self.assertEqual(by_name["3D 预览"]["status"], "fail")
        self.assertIn("预览不可用", by_name["3D 预览"]["detail"])
        self.assertEqual(acc["geometry_delta"]["status"], "before_unavailable")


class TestPreviewGeometrySummary(unittest.TestCase):
    def test_project_with_3d_geometry(self):
        proj = HSFProject.create_new("Shelf", work_dir="./workdir")
        proj.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="2"))
        proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        summary = preview_geometry_summary(proj)
        self.assertTrue(summary["available"])
        self.assertGreaterEqual(summary["mesh_count"], 1)
        self.assertIsNotNone(summary["bbox"])

    def test_project_without_scripts_still_available_with_zero_counts(self):
        proj = HSFProject.create_new("Shelf", work_dir="./workdir")
        proj.scripts.clear()
        summary = preview_geometry_summary(proj)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["mesh_count"], 0)
        self.assertIsNone(summary["bbox"])
        self.assertEqual(summary["line_count"], 0)


def _make_project(tmp_path: Path) -> HSFProject:
    proj = HSFProject.create_new("Shelf", work_dir=str(tmp_path))
    proj.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="2"))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    proj.save_to_disk()
    return proj


class TestPipelineAcceptance(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _pipeline(self, mock_llm) -> TaskPipeline:
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(self.tmp / "traces"))
        pipeline._make_llm = lambda _req: mock_llm
        pipeline._make_compiler = lambda: MockHSFCompiler()
        return pipeline

    def test_micro_modify_carries_acceptance(self):
        pipeline = self._pipeline(MockLLM(responses=["unused"]))
        project = _make_project(self.tmp)
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5", intent="MODIFY", project=project,
            work_dir=str(self.tmp), output_dir=str(self.tmp / "out"), agent_loop=False,
        ))
        acceptance = result.metadata.get("acceptance")
        self.assertIsNotNone(acceptance)
        self.assertTrue(any("参数 shelf_count 从 2 改为 5" in line for line in acceptance["summary_lines"]))
        self.assertEqual(acceptance["checks"][0]["name"], "compile")

    def test_dsl_modify_carries_acceptance(self):
        plan_json = json.dumps({"operations": [{"op": "set_value", "param": "shelf_count", "value": 5}]})
        pipeline = self._pipeline(MockLLM(responses=[plan_json]))
        project = _make_project(self.tmp)
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5，把 shelf_thk 改成 20mm", intent="MODIFY", project=project,
            work_dir=str(self.tmp), output_dir=str(self.tmp / "out"), agent_loop=True,
        ))
        acceptance = result.metadata.get("acceptance")
        self.assertIsNotNone(acceptance)
        self.assertTrue(any("参数 shelf_count 从 2 改为 5" in line for line in acceptance["summary_lines"]))
        self.assertTrue(any("已修改文件：paramlist.xml" in line for line in acceptance["summary_lines"]))

    def test_agent_loop_carries_acceptance_with_param_diff(self):
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "update_script", "arguments": {"file_path": "paramlist.xml", "content": "Integer shelf_count = 5"}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已修改。",
        ])
        pipeline = self._pipeline(mock_llm)
        project = _make_project(self.tmp)
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5", intent="MODIFY", project=project,
            work_dir=str(self.tmp), output_dir=str(self.tmp / "out"), agent_loop=True,
        ))
        acceptance = result.metadata.get("acceptance")
        self.assertIsNotNone(acceptance)
        self.assertTrue(any("参数 shelf_count 从 2 改为 5" in line for line in acceptance["summary_lines"]))
        self.assertTrue(any("已修改文件" in line for line in acceptance["summary_lines"]))

    def test_failed_modify_has_no_acceptance(self):
        # 修改在应用前抛异常（save_to_disk 失败）→ execute 兜底返回，不带 acceptance
        pipeline = self._pipeline(MockLLM(responses=["unused"]))
        project = _make_project(self.tmp)

        def broken_save():
            raise OSError("disk full")

        project.save_to_disk = broken_save
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5", intent="MODIFY", project=project,
            work_dir=str(self.tmp), output_dir=str(self.tmp / "out"), agent_loop=False,
        ))
        self.assertFalse(result.success)
        self.assertFalse(result.metadata.get("acceptance"))


if __name__ == "__main__":
    unittest.main()
