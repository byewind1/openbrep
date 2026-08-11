"""
tests/test_multi_image — P5a 多图摄取通道（Vision Harness S0）

覆盖：
1. validate_image_payload 的 images 数组校验（>4 / 坏 b64 / 坏 mime / 超 5MB /
   路径不存在且 error 指名路径 / 合法数组）
2. workbench create 路由把 images 传入 TaskRequest（intent=IMAGE）
3. 多图 CREATE：analyze_reference_image 按序每图一次、hint 带【图N】前缀、
   生成调用走 generate_with_images（多图 content 数组）
4. llm.generate_with_images content 数组结构
5. 单图旧路径零回归：image_b64 请求产生的 vision 调用消息与引入前逐字节一致
   （不 import PIL、不预处理、prompt 不变、走 generate_with_image 而非新方法）
"""

import base64
import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig, LLMConfig
from openbrep.llm import LLMAdapter, LLMResponse
from openbrep.runtime.pipeline import ImageRef, TaskPipeline, TaskRequest
from openbrep.vision.schema import VisualLayer, VisualStructure
from openbrep.workbench.project_session_service import validate_image_payload


# ── helpers ────────────────────────────────────────────────

_FAKE_VS = VisualStructure(
    component_type="斗",
    main_form="tapered_block",
    layers=[VisualLayer("base", "PRISM_", "台座", parametric=True)],
    key_features=["收分"],
    parametrize=["A"],
)


def _make_pipeline(gdl_response: str = "[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND") -> TaskPipeline:
    """返回 pipeline，LLM 始终返回 gdl_response；vision/planning 单独 patch。"""
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(content=gdl_response, model="mock", usage={}, finish_reason="stop")
    mock_llm.generate_with_images.return_value = LLMResponse(content=gdl_response, model="mock", usage={}, finish_reason="stop")
    pipeline._make_llm = lambda req: mock_llm
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda inst: ""
    return pipeline


def _valid_png_b64() -> str:
    """真实 1x1 PNG（可被 Pillow 解码）。"""
    return base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
    )).decode()


@dataclass
class _FakeObjectPlan:
    object_type: str = "bookshelf"
    knowledge_sources: list = field(default_factory=list)

    def to_prompt(self) -> str:
        return "## 对象规划\n书架"


# ── 1. validate_image_payload ─────────────────────────────

class TestValidateImagePayloadMulti(unittest.TestCase):
    def test_more_than_4_images_rejected(self):
        result = validate_image_payload({
            "images": [{"b64": _valid_png_b64()} for _ in range(5)],
        })
        self.assertFalse(result["ok"])
        self.assertIn("Max 4 images", result["error"])

    def test_bad_base64_rejected(self):
        result = validate_image_payload({"images": [{"b64": "!!!not-base64!!!", "mime": "image/png"}]})
        self.assertFalse(result["ok"])
        self.assertIn("Invalid image data", result["error"])

    def test_bad_mime_rejected(self):
        result = validate_image_payload({"images": [{"b64": "aGVsbG8=", "mime": "image/gif"}]})
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported image type", result["error"])

    def test_over_5mb_rejected(self):
        big = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
        result = validate_image_payload({"images": [{"b64": big, "mime": "image/png"}]})
        self.assertFalse(result["ok"])
        self.assertIn("too large", result["error"])

    def test_missing_path_names_specific_path(self):
        result = validate_image_payload({"images": [{"path": "/no/such/dir/pic.jpg"}]})
        self.assertFalse(result["ok"])
        self.assertIn("/no/such/dir/pic.jpg", result["error"])

    def test_path_that_is_not_a_file(self, tmp_path=MagicMock()):
        # 目录路径：指名路径且说明不是文件
        result = validate_image_payload({"images": [{"path": "/tmp"}]})
        self.assertFalse(result["ok"])
        self.assertIn("/tmp", result["error"])

    def test_valid_b64_array(self):
        result = validate_image_payload({
            "images": [
                {"b64": _valid_png_b64(), "mime": "image/png"},
                {"b64": "aGVsbG8=", "mime": "image/jpeg"},
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["images"]), 2)
        self.assertEqual(result["images"][0]["token"], "图1")
        self.assertEqual(result["images"][1]["token"], "图2")
        self.assertIsNone(result["images"][0]["path"])
        self.assertEqual(result["images"][0]["b64"], _valid_png_b64())
        self.assertEqual(result["image_b64"], None)

    def test_valid_path_array(self, tmp_path=MagicMock()):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pic.jpg"
            p.write_bytes(b"jpeg-bytes")
            result = validate_image_payload({"images": [{"path": str(p)}]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["images"][0]["path"], str(p))
        self.assertEqual(result["images"][0]["b64"], "")

    def test_legacy_single_image_unchanged(self):
        """旧 image_b64 字段：行为与引入前完全一致（含 error 文案）。"""
        result = validate_image_payload({"image_b64": "aGVsbG8=", "image_mime": "image/gif"})
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported image type", result["error"])
        self.assertIn("image/gif", result["error"])

        ok = validate_image_payload({"image_b64": "aGVsbG8=", "image_mime": "image/png"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["image_b64"], "aGVsbG8=")
        self.assertEqual(ok["image_mime"], "image/png")
        self.assertEqual(ok["images"], [])

    def test_legacy_fields_take_priority_over_images(self):
        """旧字段存在时走旧路径：images 数组被忽略（行为与现在完全一致）。"""
        result = validate_image_payload({
            "image_b64": "aGVsbG8=",
            "image_mime": "image/png",
            "images": [{"b64": "bad!!!"}],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["image_b64"], "aGVsbG8=")
        self.assertEqual(result["images"], [])


# ── 2. workbench create 路由 ──────────────────────────────

class TestWorkbenchCreateWithImages(unittest.TestCase):
    def test_create_route_forwards_images_to_task_request(self):
        from openbrep.hsf_project import HSFProject, ScriptType
        from openbrep.runtime.pipeline import TaskResult
        from openbrep.workbench_api import WorkbenchSession

        captured = {}

        class FakePipeline:
            def __init__(self, trace_dir="./traces"):
                pass

            def execute(self, request):
                captured["request"] = request
                project = HSFProject.create_new(request.gsm_name, request.work_dir)
                project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
                return TaskResult(success=True, intent=request.intent, plain_text="ok", project=project)

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            existing_pic = Path(td) / "pic.jpg"
            existing_pic.write_bytes(b"jpg")
            session = WorkbenchSession(pipeline_class=FakePipeline)
            response = session.route(
                "POST",
                "/api/project/create",
                {
                    "prompt": "按参考图生成书架",
                    "output_dir": td,
                    "images": [
                        {"b64": "aGVsbG8=", "mime": "image/png"},
                        {"path": str(existing_pic)},
                    ],
                },
            )

        self.assertTrue(response["ok"])
        req = captured["request"]
        self.assertEqual(req.intent, "IMAGE")
        self.assertEqual(len(req.images), 2)
        self.assertEqual(req.images[0].token, "图1")
        self.assertEqual(req.images[0].b64, "aGVsbG8=")
        self.assertEqual(req.images[1].path, str(existing_pic))
        self.assertIsInstance(req.images[1], ImageRef)

    def test_create_route_rejects_missing_image_path(self):
        from openbrep.workbench_api import WorkbenchSession

        session = WorkbenchSession()
        response = session.route(
            "POST",
            "/api/project/create",
            {
                "prompt": "按图生成",
                "output_dir": "/tmp",
                "images": [{"path": "/definitely/not/here.png"}],
            },
        )
        self.assertFalse(response["ok"])
        self.assertIn("/definitely/not/here.png", response["error"])

    def test_modify_route_forwards_images_to_task_request(self):
        """/api/assistant/generate（MODIFY）多图：images 传入 TaskRequest（多挂载点自查）。"""
        import tempfile

        from openbrep.hsf_project import HSFProject, ScriptType
        from openbrep.runtime.pipeline import TaskResult
        from openbrep.workbench_api import WorkbenchSession

        captured = {}

        class FakePipeline:
            def __init__(self, trace_dir="./traces"):
                pass

            def execute(self, request):
                captured["request"] = request
                request.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
                return TaskResult(
                    success=True,
                    intent=request.intent,
                    scripts={"scripts/3d.gdl": request.project.get_script(ScriptType.SCRIPT_3D)},
                    plain_text="已按图调整",
                    project=request.project,
                )

        with tempfile.TemporaryDirectory() as td:
            project = HSFProject.create_new("ModifyShelf", td)
            hsf_dir = project.save_to_disk()
            session = WorkbenchSession(pipeline_class=FakePipeline)
            session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
            response = session.route(
                "POST",
                "/api/assistant/generate",
                {
                    "message": "按图2的纹样改",
                    "images": [
                        {"b64": "Zmlyc3Q=", "mime": "image/png"},
                        {"b64": "c2Vjb25k", "mime": "image/jpeg"},
                    ],
                },
            )

        self.assertTrue(response["ok"])
        req = captured["request"]
        self.assertEqual(req.intent, "MODIFY")
        self.assertEqual(len(req.images), 2)
        self.assertEqual(req.images[0].token, "图1")
        self.assertEqual(req.images[0].b64, "Zmlyc3Q=")
        self.assertEqual(req.images[1].token, "图2")
        self.assertEqual(req.images[1].mime, "image/jpeg")

    def test_modify_route_legacy_image_b64_unchanged(self):
        """MODIFY 旧单图字段：原样走旧路径，images 数组被忽略。"""
        import tempfile

        from openbrep.hsf_project import HSFProject
        from openbrep.runtime.pipeline import TaskResult
        from openbrep.workbench_api import WorkbenchSession

        captured = {}

        class FakePipeline:
            def __init__(self, trace_dir="./traces"):
                pass

            def execute(self, request):
                captured["request"] = request
                return TaskResult(success=True, intent="MODIFY", plain_text="ok", project=request.project)

        with tempfile.TemporaryDirectory() as td:
            project = HSFProject.create_new("ModifyLegacy", td)
            hsf_dir = project.save_to_disk()
            session = WorkbenchSession(pipeline_class=FakePipeline)
            session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
            session.route(
                "POST",
                "/api/assistant/generate",
                {
                    "message": "按图改",
                    "image_b64": "bGVnYWN5",
                    "image_mime": "image/png",
                    "images": [{"b64": "bm90LXVzZWQ=", "mime": "image/png"}],
                },
            )

        req = captured["request"]
        self.assertEqual(req.image_b64, "bGVnYWN5")
        self.assertEqual(req.images, [])


# ── 3. 多图 CREATE pipeline ───────────────────────────────

class TestMultiImagePipeline(unittest.TestCase):
    def test_analyze_called_per_image_in_order_with_prefix_hints(self):
        pipeline = _make_pipeline()
        captured = {}

        def fake_analyze(b64, mime, user_hint, llm):
            captured.setdefault("analyze_calls", []).append((b64, mime, user_hint))
            return _FAKE_VS

        with patch("openbrep.runtime.pipeline.analyze_reference_image", side_effect=fake_analyze) as mock_analyze, \
             patch("openbrep.runtime.pipeline.visual_structure_to_gdl_hint", return_value="## 建模计划\n收分台座"), \
             patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
            request = TaskRequest(
                user_input="做一个斗",
                intent="CREATE",
                images=[
                    ImageRef(token="图1", b64="Zmlyc3Q=", mime="image/png"),
                    ImageRef(token="图2", b64="c2Vjb25k", mime="image/jpeg"),
                ],
            )
            pipeline.execute(request)

        # 每图一次、按序、prompt 不变（user_input 原样传入）
        self.assertEqual(mock_analyze.call_count, 2)
        calls = captured["analyze_calls"]
        self.assertEqual(calls[0], ("Zmlyc3Q=", "image/png", "做一个斗"))
        self.assertEqual(calls[1], ("c2Vjb25k", "image/jpeg", "做一个斗"))

        # hint 以 【图N】 前缀标注后拼入 enriched_instruction
        instruction = mock_plan.call_args.kwargs["instruction"]
        self.assertIn("【图1】", instruction)
        self.assertIn("【图2】", instruction)
        self.assertIn("## 建模计划", instruction)
        self.assertLess(instruction.index("【图1】"), instruction.index("【图2】"))

    def test_generation_uses_multi_image_content_array(self):
        pipeline = _make_pipeline()
        captured = {}

        with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS), \
             patch("openbrep.runtime.pipeline.visual_structure_to_gdl_hint", return_value="hint"), \
             patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
            def fake_generate_with_images(text_prompt, images, system_prompt=None, **kwargs):
                captured["images"] = images
                return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

            pipeline._make_llm = lambda req: MagicMock(
                generate_with_images=fake_generate_with_images,
                generate=MagicMock(return_value=LLMResponse(content="x", model="m", usage={}, finish_reason="stop")),
            )
            request = TaskRequest(
                user_input="做一个斗",
                intent="CREATE",
                images=[
                    ImageRef(token="图1", b64="Zmlyc3Q=", mime="image/png"),
                    ImageRef(token="图2", b64="c2Vjb25k", mime="image/jpeg"),
                ],
            )
            pipeline.execute(request)

        self.assertEqual(
            captured["images"],
            [{"b64": "Zmlyc3Q=", "mime": "image/png"},
             {"b64": "c2Vjb25k", "mime": "image/jpeg"}],
        )

    def test_modify_with_images_skips_analysis_passes_images_directly(self):
        """MODIFY 多图：不做结构化分析，图作上下文直传 generate_only。"""
        pipeline = _make_pipeline()
        captured = {}

        def fake_generate_with_images(text_prompt, images, system_prompt=None, **kwargs):
            captured["images"] = images
            return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

        pipeline._make_llm = lambda req: MagicMock(
            generate_with_images=fake_generate_with_images,
            generate=MagicMock(return_value=LLMResponse(content="x", model="m", usage={}, finish_reason="stop")),
        )
        with patch("openbrep.runtime.pipeline.analyze_reference_image") as mock_analyze:
            request = TaskRequest(
                user_input="按图修改",
                intent="MODIFY",
                agent_loop=False,
                images=[ImageRef(token="图1", b64="Zmlyc3Q=", mime="image/png")],
            )
            result = pipeline.execute(request)

        mock_analyze.assert_not_called()
        self.assertEqual(captured["images"], [{"b64": "Zmlyc3Q=", "mime": "image/png"}])
        self.assertIsNotNone(result)

    def test_path_images_resolved_and_preprocessed(self):
        """路径来源被读取成字节 + 长边缩放预处理，path 置 None。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            # 2000x1000 PNG（长边 > 1568 → 应缩到 1568x784）
            from PIL import Image as PILImage

            big = PILImage.new("RGB", (2000, 1000), "red")
            p = Path(td) / "big.png"
            big.save(p)
            pipeline = _make_pipeline()
            with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS), \
                 patch("openbrep.runtime.pipeline.visual_structure_to_gdl_hint", return_value="hint"), \
                 patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
                captured = {}

                def fake_generate_with_images(text_prompt, images, system_prompt=None, **kwargs):
                    captured["images"] = images
                    return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

                pipeline._make_llm = lambda req: MagicMock(
                    generate_with_images=fake_generate_with_images,
                    generate=MagicMock(return_value=LLMResponse(content="x", model="m", usage={}, finish_reason="stop")),
                )
                request = TaskRequest(
                    user_input="做一个斗",
                    intent="CREATE",
                    images=[ImageRef(token="图1", path=str(p))],
                )
                pipeline.execute(request)

            img_b64 = captured["images"][0]["b64"]
            raw = base64.b64decode(img_b64)
            with PILImage.open(io_bytes(raw)) as img:
                self.assertEqual(img.size, (1568, 784))

    def test_single_image_old_path_zero_regression(self):
        """零回归门禁：image_b64 单图请求走旧路径——不 import PIL、不预处理、
        不走多图方法；analyze 收到的是原始字节、prompt 一字不变。"""
        pipeline = _make_pipeline()
        captured = {}

        def fake_generate_with_image(text_prompt, image_b64, image_mime="image/png", system_prompt=None, **kwargs):
            captured["generate_with_image"] = (text_prompt, image_b64, image_mime, system_prompt)
            return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(content="x", model="m", usage={}, finish_reason="stop")
        mock_llm.generate_with_image.side_effect = fake_generate_with_image
        pipeline._make_llm = lambda req: mock_llm

        with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS) as mock_analyze, \
             patch("openbrep.runtime.pipeline.visual_structure_to_gdl_hint", return_value="hint"), \
             patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()), \
             patch("openbrep.vision.multi_image.resolve_and_preprocess", side_effect=AssertionError("旧路径不得进入多图通道")) as mock_resolve:
            request = TaskRequest(
                user_input="做一个斗",
                intent="CREATE",
                image_b64="fake_base64",
                image_mime="image/png",
            )
            pipeline.execute(request)

        # 不经过多图预处理
        mock_resolve.assert_not_called()
        # 走旧 generate_with_image（不是新方法）
        mock_llm.generate_with_image.assert_called_once()
        mock_llm.generate_with_images.assert_not_called()
        # analyze 收到原始字节（无预处理）、prompt 不变
        analyze_args = mock_analyze.call_args.args
        self.assertEqual(analyze_args[0], "fake_base64")
        self.assertEqual(analyze_args[1], "image/png")
        self.assertEqual(analyze_args[2], "做一个斗")
        # 生成调用收到的是原始字节
        _, gen_b64, gen_mime, _ = captured["generate_with_image"]
        self.assertEqual(gen_b64, "fake_base64")
        self.assertEqual(gen_mime, "image/png")

    def test_multi_image_module_imports_pil_lazily(self):
        """openbrep.vision.multi_image 模块级不 import PIL（仅预处理函数内延迟 import）。"""
        import re
        import openbrep.vision.multi_image as mi

        src = open(mi.__file__, encoding="utf-8").read()
        module_header, preprocess_fn = src.split("def preprocess_image_bytes")
        # 只匹配真正的 import 语句行（忽略 docstring/注释里的文字）
        real_imports = [
            line.strip()
            for line in module_header.splitlines()
            if re.match(r"^(from PIL|import PIL)", line.strip())
        ]
        self.assertEqual(real_imports, [])
        self.assertIn("from PIL import Image", preprocess_fn)

    def test_preprocess_small_image_passes_through_byte_identical(self):
        """长边 ≤1568 的图片原字节直通（不重编码、字节不变）。"""
        import tempfile
        from pathlib import Path

        from openbrep.vision.multi_image import preprocess_image_bytes
        from PIL import Image as PILImage

        with tempfile.TemporaryDirectory() as td:
            small = PILImage.new("RGB", (800, 600), "green")
            p = Path(td) / "small.png"
            small.save(p)
            original = base64.b64encode(p.read_bytes()).decode()

        b64, mime = preprocess_image_bytes(original, "image/png")
        self.assertEqual(b64, original)
        self.assertEqual(mime, "image/png")


def io_bytes(raw):
    import io

    return io.BytesIO(raw)


# ── 4. llm.generate_with_images ───────────────────────────

class TestGenerateWithImages(unittest.TestCase):
    def _mock_response(self, model_name="openai/gpt-4o"):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.model = model_name
        mock_response.usage = {"prompt_tokens": 1}
        return mock_response

    def test_content_array_structure_ordered(self):
        config = LLMConfig(model="gpt-4o", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)
        built = self._mock_response()
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built

        adapter.generate_with_images(
            text_prompt="describe them",
            images=[
                {"b64": "YWJj", "mime": "image/png"},
                {"b64": "ZGVm", "mime": "image/jpeg"},
            ],
            system_prompt="sys",
        )

        messages = adapter._litellm.completion.call_args.kwargs["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "sys"})
        content = messages[1]["content"]
        self.assertEqual(
            content,
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZGVm"}},
                {"type": "text", "text": "describe them"},
            ],
        )

    def test_generate_with_image_is_thin_wrapper_with_identical_message(self):
        """单图旧调用：generate_with_image 产出消息与旧实现逐字节一致。"""
        config = LLMConfig(model="gpt-4o", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)
        built = self._mock_response()
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built

        adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
            system_prompt="sys",
        )

        messages = adapter._litellm.completion.call_args.kwargs["messages"]
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}},
                        {"type": "text", "text": "describe"},
                    ],
                },
            ],
        )

    def test_generate_with_images_passes_auth_and_timeout_settings(self):
        config = LLMConfig(model="gpt-4o", api_key="test-key", api_base="https://example.com/v1", timeout=12)
        adapter = LLMAdapter(config)
        built = self._mock_response()
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built

        result = adapter.generate_with_images("d", [{"b64": "YWJj", "mime": "image/png"}])

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 12)
        self.assertEqual(kwargs["api_key"], "test-key")


if __name__ == "__main__":
    unittest.main()
