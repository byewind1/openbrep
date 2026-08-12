"""
tests/test_vision_extraction_gate — P5d-2 提取确认门（Vision Harness 生成前拦截）

覆盖（验收门禁）：
pipeline：
1. confirm_extraction=True + 无 confirmed → 早退：TaskResult
   metadata.awaiting_extraction_confirmation=True + vision_extractions，
   不进 plan_gdl_object / 生成 / 编译（LLM 调用只到 harness 为止）
2. confirmed_extractions 非空 → 跳过 harness（零 vision 调用），
   编辑值经 from_dict → to_hint 进入生成指令；metadata 携带确认后 fields
3. 确认但不编辑 → hint 与原始 harness 路径逐字节一致（零变化回归）
4. 非交互路径（confirm_extraction=False 默认）行为逐字节不变（照旧交付）
5. 全部 skipped（无字节图）→ 无提取可确认，照旧流程继续
6. generic from_dict 往返 → to_hint 逐字节一致；编辑值进 hint

service：
7. 早退存 session.pending_extraction（含 project_epoch，防跨项目确认）
8. project_epoch 不匹配 → NO_PENDING_EXTRACTION 报错并清 pending
9. 确认后正常交付，且 P5d-1 落盘用**确认后**的 fields
10. 无 pending 时带 confirmed_extractions → NO_PENDING_EXTRACTION
"""

import base64
import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import ImageRef, TaskPipeline, TaskRequest, TaskResult
from openbrep.vision.extraction_store import plan_to_dict
from openbrep.vision.modeling_plan import ModelingPlan
from openbrep.vision.schema import VisualLayer, VisualStructure
from openbrep.workbench_api import WorkbenchSession


@dataclass
class _FakeObjectPlan:
    object_type: str = "bookshelf"
    knowledge_sources: list = field(default_factory=list)

    def to_prompt(self) -> str:
        return "## 对象规划\n书架"

    def to_user_summary(self) -> str:
        return "书架"

    def to_dict(self) -> dict:
        return {"object_type": "bookshelf"}

    @property
    def validation_checks(self) -> list:
        return []


def _make_pipeline() -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop"
    )
    mock_llm.generate_with_images.return_value = LLMResponse(
        content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop"
    )
    pipeline._make_llm = lambda req: mock_llm
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda inst: ""
    return pipeline


def _lattice_plan(rows: int = 4, opening: str = "rect", family: str = "冰裂") -> ModelingPlan:
    return ModelingPlan(
        schema_name="lattice_window",
        fields={
            "opening_shape": opening,
            "pattern_family": family,
            "grid_topology": {"kind": "grid", "rows": rows, "cols": 4, "cell_desc": "方冰裂单元"},
        },
        confidence={"opening_shape": "high", "grid_topology.rows": "low"},
        corrections=[],
        source_images=["aa" * 32],
        required=["opening_shape", "pattern_family", "grid_topology"],
        critic_checks=["grid_topology.rows", "grid_topology.cols", "symmetry_group"],
    )


def _fake_harness(*args, **kwargs):
    return [_lattice_plan()]


class TestPipelineExtractionGate(unittest.TestCase):
    def test_confirm_extraction_early_exit_no_generation(self):
        """confirm_extraction=True + 无 confirmed → 早退，不进规划/生成（LLM 只到 harness）。"""
        pipeline = _make_pipeline()
        with patch("openbrep.vision.harness.run", side_effect=_fake_harness) as mock_h:
            with patch(
                "openbrep.runtime.pipeline.plan_gdl_object",
                side_effect=AssertionError("should not plan before extraction confirmation"),
            ) as mock_plan:
                request = TaskRequest(
                    user_input="这是漏窗，按图生成",
                    intent="IMAGE",
                    confirm_extraction=True,
                    images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
                )
                result = pipeline.execute(request)

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["awaiting_extraction_confirmation"], True)
        extractions = result.metadata["vision_extractions"]
        self.assertEqual(len(extractions), 1)
        self.assertEqual(extractions[0]["schema_name"], "lattice_window")
        self.assertEqual(extractions[0]["fields"]["opening_shape"], "rect")
        # 可编辑卡片数据源：required + critic_checks 随提取透出
        self.assertEqual(extractions[0]["required"], ["opening_shape", "pattern_family", "grid_topology"])
        self.assertIn("grid_topology.rows", extractions[0]["critic_checks"])
        json.dumps(extractions[0], ensure_ascii=False)  # 纯 JSON 可序列化
        self.assertEqual(mock_h.call_count, 1)
        self.assertEqual(mock_plan.call_count, 0)  # 不进 plan_gdl_object → 无生成 LLM 调用

    def test_confirmed_extractions_skip_harness_edited_value_in_hint(self):
        """confirmed_extractions 非空 → 跳过 harness（零 vision 调用），编辑值进 hint。"""
        pipeline = _make_pipeline()
        confirmed = [
            {
                "token": "图1",
                "schema_name": "lattice_window",
                "fields": {
                    "opening_shape": "circle",  # 用户编辑：rect → circle
                    "pattern_family": "海棠",   # 用户编辑：冰裂 → 海棠
                    "grid_topology": {"kind": "grid", "rows": 5, "cols": 4, "cell_desc": "方冰裂单元"},
                },
                "confidence": {},
                "corrections": [],
                "degraded": False,
                "critic_degraded": False,
                "raw_description": "",
                "sha256": "aa" * 32,
                "required": ["opening_shape", "pattern_family", "grid_topology"],
                "critic_checks": ["grid_topology.rows", "grid_topology.cols", "symmetry_group"],
            }
        ]
        with patch(
            "openbrep.vision.harness.run",
            side_effect=AssertionError("harness must be skipped on confirmed re-send"),
        ) as mock_h:
            with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
                request = TaskRequest(
                    user_input="这是漏窗，按图生成",
                    intent="IMAGE",
                    confirm_extraction=True,
                    confirmed_extractions=confirmed,
                    images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
                )
                result = pipeline.execute(request)

        mock_h.assert_not_called()  # 零 vision 重调
        self.assertEqual(mock_plan.call_count, 1)
        instruction = mock_plan.call_args.kwargs["instruction"]
        self.assertIn("circle", instruction)
        self.assertIn("海棠", instruction)
        self.assertIn('"rows": 5', instruction)  # 编辑值自然进 hint
        # metadata 携带确认后 fields（service 落盘用确认后 fields）
        entry = (result.metadata or {})["vision_extractions"][0]
        self.assertEqual(entry["fields"]["opening_shape"], "circle")
        self.assertNotIn("awaiting_extraction_confirmation", result.metadata)

    def test_confirmed_without_edit_hint_byte_identical_to_harness_path(self):
        """确认但不编辑 → 重建 plan 的 hint 与原始 harness 路径逐字节一致（零变化回归）。"""
        pipeline = _make_pipeline()
        with patch("openbrep.vision.harness.run", side_effect=_fake_harness) as mock_h:
            with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
                request = TaskRequest(
                    user_input="这是漏窗，按图生成",
                    intent="IMAGE",
                    images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
                )
                pipeline.execute(request)
        instruction_original = mock_plan.call_args.kwargs["instruction"]

        # 用 harness 产出的 extraction（未编辑）走确认重发路径
        confirmed = [plan_to_dict(_lattice_plan())]
        confirmed[0]["token"] = "图1"
        with patch(
            "openbrep.vision.harness.run",
            side_effect=AssertionError("harness must be skipped"),
        ):
            with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan2:
                request = TaskRequest(
                    user_input="这是漏窗，按图生成",
                    intent="IMAGE",
                    confirm_extraction=True,
                    confirmed_extractions=confirmed,
                    images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
                )
                pipeline.execute(request)
        instruction_confirmed = mock_plan2.call_args.kwargs["instruction"]
        self.assertEqual(instruction_original, instruction_confirmed)

    def test_non_interactive_path_unchanged(self):
        """confirm_extraction=False（默认，benchmark/CLI）→ 照旧交付，无确认门。"""
        pipeline = _make_pipeline()
        with patch("openbrep.vision.harness.run", side_effect=_fake_harness) as mock_h:
            with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
                request = TaskRequest(
                    user_input="这是漏窗，按图生成",
                    intent="IMAGE",
                    images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
                )
                result = pipeline.execute(request)

        self.assertEqual(mock_plan.call_count, 1)
        self.assertNotIn("awaiting_extraction_confirmation", (result.metadata or {}))
        # 提取照旧透出（P5d-1 行为不变）
        self.assertEqual((result.metadata or {})["vision_extractions"][0]["schema_name"], "lattice_window")

    def test_all_skipped_no_gate(self):
        """全部图无字节（skipped）→ 无提取可确认，照旧流程继续（不卡确认门）。"""
        pipeline = _make_pipeline()
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
            request = TaskRequest(
                user_input="这是漏窗，按图生成",
                intent="IMAGE",
                confirm_extraction=True,
                images=[ImageRef(token="图1", b64="", path=None)],  # 无字节 → skipped
            )
            result = pipeline.execute(request)
        self.assertEqual(mock_plan.call_count, 1)
        self.assertNotIn("awaiting_extraction_confirmation", (result.metadata or {}))

    def test_generic_from_dict_hint_byte_identical(self):
        """generic 确认往返：from_dict 还原 VisualStructure → to_hint 逐字节一致；编辑值生效。"""
        vs = VisualStructure(
            component_type="斗",
            main_form="tapered_block",
            layers=[VisualLayer("base", "PRISM_", "台座", parametric=True)],
            key_features=["收分"],
            parametrize=["A"],
            raw_description="一个斗",
        )
        plan = ModelingPlan(schema_name="generic", fields={"visual_structure": vs}, source_images=["cd" * 32])
        data = plan_to_dict(plan)
        rebuilt = ModelingPlan.from_dict(data)
        self.assertEqual(rebuilt.to_hint(), plan.to_hint())

        # 编辑 component_type → hint 随之变
        edited = dict(data)
        edited["fields"]["visual_structure"]["component_type"] = "窗"
        rebuilt2 = ModelingPlan.from_dict(edited)
        self.assertIn("构件：窗", rebuilt2.to_hint())


def _sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


class _GatePipeline:
    """service 测试用假 pipeline：confirm_extraction → 早退；confirmed → 交付。"""

    def __init__(self, trace_dir="./traces"):
        self.trace_dir = trace_dir

    def execute(self, request: TaskRequest) -> TaskResult:
        if request.confirmed_extractions:
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(
                success=True,
                intent="IMAGE",
                scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已按确认的读图结果创建对象",
                project=project,
                metadata={"vision_extractions": request.confirmed_extractions},
            )
        if request.confirm_extraction:
            return TaskResult(
                success=True,
                intent="IMAGE",
                project=HSFProject.create_new(request.gsm_name, request.work_dir),
                metadata={
                    "awaiting_extraction_confirmation": True,
                    "vision_extractions": [
                        {
                            "token": "图1",
                            "schema_name": "lattice_window",
                            "fields": {"opening_shape": "rect", "pattern_family": "冰裂"},
                            "confidence": {},
                            "corrections": [],
                            "degraded": False,
                            "critic_degraded": False,
                            "raw_description": "",
                            "sha256": _sha(b"a"),
                            "required": ["opening_shape", "pattern_family", "grid_topology"],
                            "critic_checks": ["grid_topology.rows"],
                        }
                    ],
                },
            )
        project = HSFProject.create_new(request.gsm_name, request.work_dir)
        project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
        return TaskResult(
            success=True,
            intent="IMAGE",
            scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
            plain_text="创建完成",
            project=project,
            metadata={},
        )


class TestServiceExtractionGate(unittest.TestCase):
    def _image_payload(self):
        return [{"token": "图1", "b64": base64.b64encode(b"a").decode(), "mime": "image/png"}]

    def test_early_exit_stores_pending_extraction_with_epoch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session = WorkbenchSession(pipeline_class=_GatePipeline)
            body = {
                "prompt": "这是漏窗，按图生成",
                "output_dir": str(tmp),
                "images": self._image_payload(),
                "confirm_extraction": True,
            }
            response = session.route("POST", "/api/project/create", body)

            self.assertTrue(response["ok"])
            self.assertTrue(response["awaiting_extraction_confirmation"])
            self.assertEqual(len(response["extractions"]), 1)
            # 不挂载项目、不落盘：无 project / snapshot 字段
            self.assertNotIn("project", response)
            # session 存 pending_extraction（含 project_epoch，防跨项目确认）
            self.assertIsNotNone(session.pending_extraction)
            self.assertEqual(session.pending_extraction["project_epoch"], session.project_epoch)
            self.assertEqual(session.pending_extraction["body"], body)
            self.assertIsNone(session.project)

    def test_epoch_mismatch_rejects_confirm(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session = WorkbenchSession(pipeline_class=_GatePipeline)
            body = {
                "prompt": "这是漏窗，按图生成",
                "output_dir": str(tmp),
                "images": self._image_payload(),
                "confirm_extraction": True,
            }
            session.route("POST", "/api/project/create", body)
            confirmed = [dict(session.pending_extraction["extractions"][0])]
            # 模拟项目切换：epoch +1 → 确认被拒
            session.project_epoch += 1
            response = session.route(
                "POST", "/api/project/create", {**body, "confirmed_extractions": confirmed}
            )
            self.assertFalse(response["ok"])
            self.assertEqual(response.get("code"), "NO_PENDING_EXTRACTION")
            self.assertIsNone(session.pending_extraction)  # 失效 pending 被清

    def test_confirm_delivers_and_persists_confirmed_fields(self):
        import glob
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session = WorkbenchSession(pipeline_class=_GatePipeline)
            body = {
                "prompt": "这是漏窗，按图生成",
                "output_dir": str(tmp),
                "images": self._image_payload(),
                "confirm_extraction": True,
            }
            early = session.route("POST", "/api/project/create", body)
            self.assertTrue(early["awaiting_extraction_confirmation"])
            # 用户编辑：rect → circle
            confirmed = [dict(early["extractions"][0], fields={"opening_shape": "circle", "pattern_family": "海棠"})]
            response = session.route(
                "POST", "/api/project/create", {**body, "confirmed_extractions": confirmed}
            )
            self.assertTrue(response["ok"])
            self.assertEqual(response["assistant"]["kind"], "create")
            self.assertIsNone(session.pending_extraction)  # 确认后消费
            # P5d-1 落盘用**确认后** fields
            files = glob.glob(response["project"]["path"] + "/.openbrep/vision/extraction-*.json")
            self.assertTrue(files)
            data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
            self.assertEqual(data["fields"]["opening_shape"], "circle")
            self.assertEqual(data["fields"]["pattern_family"], "海棠")

    def test_confirm_without_pending_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session = WorkbenchSession(pipeline_class=_GatePipeline)
            response = session.route(
                "POST",
                "/api/project/create",
                {
                    "prompt": "这是漏窗，按图生成",
                    "output_dir": str(tmp),
                    "images": self._image_payload(),
                    "confirm_extraction": True,
                    "confirmed_extractions": [{"token": "图1", "schema_name": "lattice_window", "fields": {}}],
                },
            )
            self.assertFalse(response["ok"])
            self.assertEqual(response.get("code"), "NO_PENDING_EXTRACTION")


if __name__ == "__main__":
    unittest.main()
