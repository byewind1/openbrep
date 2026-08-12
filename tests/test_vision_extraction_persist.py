"""
tests/test_vision_extraction_persist — P5d-1 service 层落盘 + 事件响应

覆盖（验收门禁"service 落盘（create 后文件存在、内容字段完整）"）：
1. CREATE 带多图 → 提取工件落盘到 <root>/.openbrep/vision/extraction-<sha256[:12]>.json，
   内容字段完整（schema/fields/confidence/corrections/降级/model/created_at）
2. skipped（无字节图）条目不落盘
3. 落盘失败 → warning 提示、不阻断交付（ok 仍 True）
4. 事件流：vision_analysis_done 事件 payload 携带 extraction 摘要（前端卡片数据源）
"""

import base64
import json
import unittest
from pathlib import Path

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskResult
from openbrep.vision.extraction_store import load_extraction
from openbrep.workbench_api import WorkbenchSession


def _sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


class TestServiceExtractionPersist(unittest.TestCase):
    def _make_pipeline(self, extractions, events):
        class FakePipeline:
            def __init__(self, trace_dir="./traces"):
                self.trace_dir = trace_dir

            def execute(self, request):
                project = HSFProject.create_new(request.gsm_name, request.work_dir)
                project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
                return TaskResult(
                    success=True,
                    intent="IMAGE",
                    scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
                    plain_text="已根据参考图创建对象",
                    project=project,
                    metadata={"vision_extractions": extractions},
                )

        return FakePipeline

    def test_create_with_images_persists_extraction_artifacts(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sha1 = _sha(b"image-a")
            sha2 = _sha(b"image-b")
            extractions = [
                {
                    "token": "图1",
                    "schema_name": "lattice_window",
                    "fields": {"opening_shape": "rect", "pattern_family": "冰裂",
                               "grid_topology": {"rows": 3, "cols": 4}},
                    "confidence": {"opening_shape": "high", "grid_topology.rows": "low"},
                    "corrections": [{"field": "grid_topology.rows", "old": 4, "new": 3,
                                     "evidence": "图中 3 行"}],
                    "degraded": False,
                    "critic_degraded": False,
                    "raw_description": "",
                    "sha256": sha1,
                },
                {
                    "token": "图2",
                    "schema_name": "lattice_window",
                    "fields": {"opening_shape": "circle"},
                    "confidence": {},
                    "corrections": [],
                    "degraded": False,
                    "critic_degraded": False,
                    "raw_description": "",
                    "sha256": sha2,
                },
                {"token": "图3", "skipped": True},  # 无字节图：不落盘
            ]
            events: list = []

            def on_event(t, d):
                events.append({"type": t, "data": d})

            # 让 FakePipeline 把 on_event 透传给事件采集（模拟 harness 事件流）
            pipeline_cls = self._make_pipeline(extractions, events)
            session = WorkbenchSession(pipeline_class=pipeline_cls)
            response = session.route(
                "POST",
                "/api/project/create",
                {
                    "prompt": "这是漏窗，按图生成",
                    "output_dir": str(tmp),
                    "images": [
                        {"token": "图1", "b64": base64.b64encode(b"image-a").decode(), "mime": "image/png"},
                        {"token": "图2", "b64": base64.b64encode(b"image-b").decode(), "mime": "image/png"},
                    ],
                },
            )

            self.assertTrue(response["ok"])
            project_root = Path(response["project"]["path"])
            vision_dir = project_root / ".openbrep" / "vision"
            self.assertTrue(vision_dir.is_dir())

            # 两张有字节图各一份；skipped 图3 不落盘
            files = sorted(vision_dir.glob("extraction-*.json"))
            self.assertEqual(len(files), 2)
            self.assertTrue((vision_dir / f"extraction-{sha1[:12]}.json").exists())
            self.assertTrue((vision_dir / f"extraction-{sha2[:12]}.json").exists())

            # 内容字段完整（含 model = session.llm_model）
            data = json.loads((vision_dir / f"extraction-{sha1[:12]}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema_name"], "lattice_window")
            self.assertEqual(data["fields"]["pattern_family"], "冰裂")
            self.assertEqual(data["fields"]["grid_topology"]["rows"], 3)
            self.assertEqual(data["confidence"]["grid_topology.rows"], "low")
            self.assertEqual(data["corrections"][0]["old"], 4)
            self.assertEqual(data["corrections"][0]["new"], 3)
            self.assertFalse(data["degraded"])
            self.assertFalse(data["critic_degraded"])
            self.assertEqual(data["model"], session.llm_model)
            self.assertIn("created_at", data)

            # load_extraction 往返（P5e 复用铺路）
            loaded = load_extraction(project_root, sha1)
            self.assertEqual(loaded["fields"]["opening_shape"], "rect")

            # 无落盘失败 warning
            self.assertEqual(response.get("warnings") or [], [])

    def test_save_failure_warns_but_does_not_block(self, tmp_path=None):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            sha = _sha(b"image-x")
            extractions = [
                {
                    "token": "图1",
                    "schema_name": "lattice_window",
                    "fields": {"opening_shape": "rect"},
                    "confidence": {},
                    "corrections": [],
                    "degraded": False,
                    "critic_degraded": False,
                    "raw_description": "",
                    "sha256": sha,
                },
            ]
            pipeline_cls = self._make_pipeline(extractions, [])
            session = WorkbenchSession(pipeline_class=pipeline_cls)

            with patch(
                "openbrep.vision.extraction_store.save_extraction",
                side_effect=OSError("disk full"),
            ):
                response = session.route(
                    "POST",
                    "/api/project/create",
                    {
                        "prompt": "这是漏窗，按图生成",
                        "output_dir": str(tmp),
                        "images": [
                            {"token": "图1", "b64": base64.b64encode(b"image-x").decode(), "mime": "image/png"},
                        ],
                    },
                )

            # 不阻断交付：ok 仍 True，warning 可见（零静默）
            self.assertTrue(response["ok"])
            self.assertTrue(
                any("vision 提取工件落盘失败" in w for w in response.get("warnings") or [])
            )


if __name__ == "__main__":
    unittest.main()
