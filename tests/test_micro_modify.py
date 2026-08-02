"""
Tests for P2 deterministic micro-modify (openbrep/runtime/micro_modify.py +
TaskPipeline._try_micro_modify).

Covers:
- detection matrix: name/description resolution, unit conversion, boolean,
  ambiguity/compound/question fallback (None = fall back to LLM path)
- pipeline interception: MODIFY with pure param-value change never calls LLM,
  persists paramlist, creates before-revision with micro_modify metadata
- non-micro MODIFY / DEBUG intent still route to the LLM path
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.micro_modify import MicroModify, detect_micro_modify
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


def _make_project() -> HSFProject:
    proj = HSFProject.create_new("test_shelf", work_dir="./workdir")
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
        GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"),
        GDLParameter(name="show_frame", type_tag="Boolean", description="显示边框", value="1"),
        GDLParameter(name="ratio", type_tag="RealNum", description="比例", value="1.0"),
        GDLParameter(name="mat_name", type_tag="String", description="材质名", value="oak"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    return proj


class TestDetectMicroModify(unittest.TestCase):
    def detect(self, text: str) -> MicroModify | None:
        return detect_micro_modify(text, _make_project())

    def test_name_match_chinese(self):
        micro = self.detect("把 shelf_count 改成 5")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "shelf_count")
        self.assertEqual(micro.new_value, "5")
        self.assertEqual(micro.old_value, "4")
        self.assertEqual(micro.matched_via, "name")

    def test_m03_benchmark_phrasing(self):
        micro = self.detect(
            "把层板厚度参数 shelf_thk 的默认值从 18mm 改为 25mm（即 0.025 米），"
            "参数名和其他内容保持不变"
        )
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "shelf_thk")
        self.assertEqual(micro.new_value, "0.025")

    def test_description_match(self):
        micro = self.detect("把层板厚度改成 20mm")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "shelf_thk")
        self.assertEqual(micro.new_value, "0.02")
        self.assertEqual(micro.matched_via, "description")

    def test_description_longest_match_wins(self):
        project = _make_project()
        project.parameters.append(
            GDLParameter(name="depth_gap", type_tag="Length", description="深度方向间距", value="0.3")
        )
        micro = detect_micro_modify("把深度方向间距改成 600mm", project)
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "depth_gap")
        self.assertEqual(micro.new_value, "0.6")

        micro = detect_micro_modify("把深度改成 500mm", project)
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "B")

    def test_ambiguous_param_names_fall_through(self):
        self.assertIsNone(self.detect("把 shelf_count 和 shelf_thk 改成 5"))

    def test_unknown_param_falls_through(self):
        self.assertIsNone(self.detect("把 leg_count 改成 4"))

    def test_compound_request_falls_through(self):
        self.assertIsNone(self.detect("把 shelf_count 改成 5，然后把高度改成 1.2"))
        self.assertIsNone(self.detect("把 shelf_count 改成 5，另外检查一下脚本"))

    def test_question_falls_through(self):
        self.assertIsNone(self.detect("为什么 shelf_count 是 4？"))
        self.assertIsNone(self.detect("把 shelf_count 改成 5 为什么塌了？"))

    def test_boolean_word(self):
        micro = self.detect("把 show_frame 改成关闭")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "0")

        micro = self.detect("把 show_frame 设为开启")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "1")

    def test_boolean_numeric(self):
        micro = self.detect("把 show_frame 改成 0")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "0")

    def test_length_meter_without_unit(self):
        micro = self.detect("把 A 改成 0.9")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "0.9")

    def test_length_large_without_unit_falls_through(self):
        # 无单位大数值更可能是 mm 意图，不能静默当米
        self.assertIsNone(self.detect("把 A 改成 900"))

    def test_length_with_units(self):
        for text, expected in (
            ("把 A 改成 900mm", "0.9"),
            ("把 A 改成 90cm", "0.9"),
            ("把 A 改成 1.2米", "1.2"),
            ("把 A 改成 1.2m", "1.2"),
        ):
            with self.subTest(text=text):
                micro = self.detect(text)
                self.assertIsNotNone(micro)
                self.assertEqual(micro.new_value, expected)

    def test_integer_non_integer_falls_through(self):
        self.assertIsNone(self.detect("把 shelf_count 改成 5.5"))

    def test_realnum(self):
        micro = self.detect("把 ratio 改成 1.5")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "1.5")
        # RealNum 带长度单位语义不明
        self.assertIsNone(self.detect("把 ratio 改成 5mm"))

    def test_string_param_falls_through(self):
        self.assertIsNone(self.detect("把 mat_name 改成 5"))

    def test_english(self):
        micro = self.detect("set shelf_count to 5")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.param_name, "shelf_count")
        self.assertEqual(micro.new_value, "5")

        micro = self.detect("change A to 0.9")
        self.assertIsNotNone(micro)
        self.assertEqual(micro.new_value, "0.9")

    def test_rename_request_falls_through(self):
        self.assertIsNone(self.detect("把 shelf_count 重命名为 layer_count"))

    def test_no_value_verb_falls_through(self):
        self.assertIsNone(self.detect("shelf_count 现在是 4"))
        self.assertIsNone(self.detect("给书架加一扇门"))


def _make_pipeline(llm_content: str) -> tuple[TaskPipeline, MagicMock]:
    cfg = GDLAgentConfig()
    pipeline = TaskPipeline(config=cfg, trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content=llm_content, model="mock", usage={}, finish_reason="stop"
    )
    pipeline._make_llm = lambda req: mock_llm
    return pipeline, mock_llm


class TestPipelineMicroModify(unittest.TestCase):
    def test_modify_intercepts_micro_modify_without_llm(self):
        pipeline, mock_llm = _make_pipeline("SHOULD NOT BE USED")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _make_project()
            project.work_dir = Path(tmpdir)
            project.root = Path(tmpdir) / project.name
            project.save_to_disk()

            result = pipeline.execute(TaskRequest(
                user_input="把 shelf_count 改成 5",
                intent="MODIFY",
                project=project,
                work_dir=tmpdir,
                output_dir=str(Path(tmpdir) / "out"),
            ))

            self.assertTrue(result.success)
            mock_llm.generate.assert_not_called()
            self.assertEqual(project.get_parameter("shelf_count").value, "5")
            self.assertIn("确定性微修改", result.plain_text)
            self.assertIn("shelf_count", result.plain_text)
            self.assertIsNotNone(result.compile_result)
            # paramlist.xml 已落盘
            paramlist = (Path(tmpdir) / project.name / "paramlist.xml").read_text(encoding="utf-8")
            self.assertIn("<Value>5</Value>", paramlist)

    def test_micro_modify_creates_before_revision_with_metadata(self):
        pipeline, _mock_llm = _make_pipeline("SHOULD NOT BE USED")
        calls = []

        def fake_create_revision(*args, **kwargs):
            calls.append(kwargs)
            return MagicMock(revision_id="r0001")

        with tempfile.TemporaryDirectory() as tmpdir:
            project = _make_project()
            project.work_dir = Path(tmpdir)
            project.root = Path(tmpdir) / project.name
            project.save_to_disk()

            with patch("openbrep.runtime.pipeline.create_revision", fake_create_revision):
                result = pipeline.execute(TaskRequest(
                    user_input="把 shelf_thk 改成 25mm",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "out"),
                ))

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["message"], "auto: before modify")
        self.assertEqual(calls[0]["changed_files"], ["paramlist.xml"])
        self.assertEqual(
            calls[0]["metadata"]["micro_modify"],
            {"param": "shelf_thk", "old_value": "0.018", "new_value": "0.025", "matched_via": "name"},
        )

    def test_non_micro_modify_falls_through_to_llm(self):
        pipeline, mock_llm = _make_pipeline("[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _make_project()
            project.work_dir = Path(tmpdir)
            project.root = Path(tmpdir) / project.name
            project.save_to_disk()

            result = pipeline.execute(TaskRequest(
                user_input="给书架加一扇门",
                intent="MODIFY",
                project=project,
                work_dir=tmpdir,
                output_dir=str(Path(tmpdir) / "out"),
            ))

        mock_llm.generate.assert_called()
        self.assertIsNotNone(result)

    def test_debug_intent_not_intercepted(self):
        pipeline, mock_llm = _make_pipeline("分析完毕，没有问题。")
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _make_project()
            project.work_dir = Path(tmpdir)
            project.root = Path(tmpdir) / project.name
            project.save_to_disk()

            pipeline.execute(TaskRequest(
                user_input="把 shelf_count 改成 5",
                intent="DEBUG",
                project=project,
                work_dir=tmpdir,
                output_dir=str(Path(tmpdir) / "out"),
            ))

        mock_llm.generate.assert_called()

    def test_compile_failure_marks_unsuccess_but_keeps_value(self):
        pipeline, _mock_llm = _make_pipeline("SHOULD NOT BE USED")

        class FailingCompiler:
            def hsf2libpart(self, *_args):
                from openbrep.compiler import CompileResult
                return CompileResult(success=False, stderr="boom", exit_code=1)

        pipeline._make_compiler = lambda: FailingCompiler()

        with tempfile.TemporaryDirectory() as tmpdir:
            project = _make_project()
            project.work_dir = Path(tmpdir)
            project.root = Path(tmpdir) / project.name
            project.save_to_disk()

            result = pipeline.execute(TaskRequest(
                user_input="把 shelf_count 改成 5",
                intent="MODIFY",
                project=project,
                work_dir=tmpdir,
                output_dir=str(Path(tmpdir) / "out"),
            ))

        self.assertFalse(result.success)
        self.assertEqual(project.get_parameter("shelf_count").value, "5")
        self.assertIn("编译：❌ 失败", result.plain_text)


if __name__ == "__main__":
    unittest.main()
