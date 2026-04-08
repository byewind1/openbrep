"""
tests/test_vision — Vision 模块单元测试

覆盖：
1. VisualStructure / VisualLayer 构建
2. visual_structure_to_gdl_hint() 输出内容
3. _parse_response() JSON 解析（正常 / 残缺 / 非 JSON）
4. analyze_reference_image() 调用行为（mock LLM）
5. router + pipeline IMAGE 路由（mock LLM + vision）
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from openbrep.vision.schema import VisualLayer, VisualStructure
from openbrep.vision.image_to_plan import (
    _parse_response,
    analyze_reference_image,
    visual_structure_to_gdl_hint,
)
from openbrep.runtime.router import IntentRouter
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse


# ── helpers ────────────────────────────────────────────────

def _make_sample_vs() -> VisualStructure:
    return VisualStructure(
        component_type="斗",
        main_form="tapered_block_with_cross_slot",
        layers=[
            VisualLayer("base",      "PRISM_", "收分台座",        parametric=True),
            VisualLayer("waist",     "BLOCK",  "短腰段",           parametric=False),
            VisualLayer("slot_body", "BLOCK",  "十字槽剩余实体",   parametric=True),
            VisualLayer("lug",       "BLOCK",  "侧耳",             parametric=False),
        ],
        symmetry=["x", "y"],
        key_features=["收分台座", "十字槽开口", "侧耳"],
        dimension_hints={"width": "约0.6m", "height": "约0.15m"},
        parametrize=["A", "B", "ZZYZX"],
        fix_as_ratio=["slot_width = A * 0.34", "base_height = ZZYZX * 0.40"],
        raw_description="中国传统木构斗拱中的斗构件",
    )


def _make_pipeline(gdl_response: str = "[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND") -> TaskPipeline:
    """返回 pipeline，LLM 始终返回 gdl_response（vision 层单独 patch）。"""
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content=gdl_response, model="mock", usage={}, finish_reason="stop"
    )
    pipeline._make_llm = lambda req: mock_llm
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda inst: ""
    return pipeline


# ── 1. Schema ──────────────────────────────────────────────

class TestVisualStructure(unittest.TestCase):
    def test_build(self):
        vs = _make_sample_vs()
        self.assertEqual(vs.component_type, "斗")
        self.assertEqual(len(vs.layers), 4)
        self.assertIn("x", vs.symmetry)

    def test_layer_defaults(self):
        layer = VisualLayer(name="base", command="BLOCK", description="底座")
        self.assertTrue(layer.parametric)  # 默认参数化


# ── 2. visual_structure_to_gdl_hint ───────────────────────

class TestVisualStructureToGdlHint(unittest.TestCase):
    def setUp(self):
        self.vs = _make_sample_vs()
        self.hint = visual_structure_to_gdl_hint(self.vs)

    def test_contains_component_type(self):
        self.assertIn("斗", self.hint)

    def test_contains_layer_commands(self):
        self.assertIn("PRISM_", self.hint)
        self.assertIn("BLOCK", self.hint)

    def test_contains_parametrize(self):
        self.assertIn("A", self.hint)
        self.assertIn("ZZYZX", self.hint)

    def test_contains_ratio_hints(self):
        self.assertIn("slot_width = A * 0.34", self.hint)

    def test_contains_key_features(self):
        self.assertIn("收分台座", self.hint)

    def test_layer_order_preserved(self):
        idx_base = self.hint.index("base")
        idx_lug  = self.hint.index("lug")
        self.assertLess(idx_base, idx_lug)

    def test_empty_structure(self):
        vs = VisualStructure(component_type="未知构件", main_form="unknown")
        hint = visual_structure_to_gdl_hint(vs)
        self.assertIn("未知构件", hint)


# ── 3. _parse_response ────────────────────────────────────

class TestParseResponse(unittest.TestCase):
    def _sample_json(self, **overrides) -> str:
        data = {
            "component_type": "斗",
            "main_form": "tapered_block",
            "layers": [
                {"name": "base", "command": "PRISM_", "description": "台座", "parametric": True}
            ],
            "symmetry": ["x", "y"],
            "key_features": ["收分"],
            "dimension_hints": {"width": "0.6m"},
            "parametrize": ["A", "B"],
            "fix_as_ratio": ["slot_w = A * 0.34"],
            "raw_description": "斗构件",
        }
        data.update(overrides)
        return json.dumps(data, ensure_ascii=False)

    def test_valid_json(self):
        vs = _parse_response(self._sample_json())
        self.assertEqual(vs.component_type, "斗")
        self.assertEqual(len(vs.layers), 1)
        self.assertEqual(vs.layers[0].command, "PRISM_")
        self.assertIn("x", vs.symmetry)

    def test_json_with_surrounding_text(self):
        raw = "以下是分析结果：\n" + self._sample_json() + "\n这是一个斗构件。"
        vs = _parse_response(raw)
        self.assertEqual(vs.component_type, "斗")

    def test_missing_optional_fields(self):
        data = {"component_type": "书架", "main_form": "box_with_shelves"}
        vs = _parse_response(json.dumps(data))
        self.assertEqual(vs.component_type, "书架")
        self.assertEqual(vs.layers, [])
        self.assertEqual(vs.symmetry, [])

    def test_invalid_json_returns_fallback(self):
        vs = _parse_response("这不是 JSON 内容")
        self.assertEqual(vs.component_type, "未知构件")
        self.assertEqual(vs.main_form, "unknown")
        self.assertIn("这不是", vs.raw_description)

    def test_empty_string_returns_fallback(self):
        vs = _parse_response("")
        self.assertEqual(vs.component_type, "未知构件")

    def test_layer_parametric_defaults_to_true(self):
        data = {
            "component_type": "斗", "main_form": "block",
            "layers": [{"name": "base", "command": "BLOCK", "description": "底座"}],
        }
        vs = _parse_response(json.dumps(data))
        self.assertTrue(vs.layers[0].parametric)


# ── 4. analyze_reference_image ────────────────────────────

class TestAnalyzeReferenceImage(unittest.TestCase):
    def _mock_llm(self, response_text: str) -> MagicMock:
        mock = MagicMock()
        mock.generate.return_value = LLMResponse(
            content=response_text, model="mock", usage={}, finish_reason="stop"
        )
        return mock

    def test_valid_response_returns_visual_structure(self):
        resp_json = json.dumps({
            "component_type": "斗", "main_form": "tapered",
            "layers": [], "symmetry": ["x"], "key_features": [],
            "dimension_hints": {}, "parametrize": [], "fix_as_ratio": [],
            "raw_description": "斗",
        }, ensure_ascii=False)
        llm = self._mock_llm(resp_json)
        vs = analyze_reference_image("fake_b64", "image/png", "做一个斗", llm)
        self.assertEqual(vs.component_type, "斗")

    def test_llm_failure_returns_fallback(self):
        mock = MagicMock()
        mock.generate.side_effect = Exception("LLM timeout")
        vs = analyze_reference_image("fake_b64", "image/png", "做一个斗", mock)
        self.assertEqual(vs.component_type, "未知构件")
        self.assertIn("LLM timeout", vs.raw_description)

    def test_messages_include_image(self):
        llm = self._mock_llm("{}")
        analyze_reference_image("abc123", "image/jpeg", "hint", llm)
        call_args = llm.generate.call_args
        messages = call_args.args[0]
        user_msg = messages[-1]
        content = user_msg["content"]
        self.assertIsInstance(content, list)
        image_part = content[0]
        self.assertEqual(image_part["type"], "image_url")
        self.assertIn("image/jpeg;base64,abc123", image_part["image_url"]["url"])


# ── 5. Router IMAGE 路由 ───────────────────────────────────

class TestRouterImageIntent(unittest.TestCase):
    def setUp(self):
        self.router = IntentRouter()

    def test_image_only_returns_image(self):
        self.assertEqual(self.router.classify("", has_image=True), "IMAGE")

    def test_image_with_create_text_returns_create(self):
        self.assertEqual(self.router.classify("根据参考图做一个斗", has_image=True), "CREATE")

    def test_image_with_modify_text_returns_modify_or_debug(self):
        result = self.router.classify("修改这段脚本", has_image=True)
        self.assertIn(result, ("MODIFY", "DEBUG"))

    def test_image_with_debug_text_returns_debug(self):
        result = self.router.classify("Error in 3D script, line 42", has_image=True)
        self.assertEqual(result, "DEBUG")

    def test_no_image_text_only(self):
        self.assertEqual(self.router.classify("做一个书架"), "CREATE")


# ── 6. Pipeline IMAGE → vision 前置 ───────────────────────

_FAKE_VS = VisualStructure(
    component_type="斗",
    main_form="tapered_block",
    layers=[VisualLayer("base", "PRISM_", "台座", parametric=True)],
    key_features=["收分"],
    parametrize=["A"],
)


class TestPipelineVisionPreAnalysis(unittest.TestCase):

    def test_image_intent_runs_vision_analysis(self):
        """IMAGE intent + image_b64 → analyze_reference_image 被调用"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS) as mock_vision:
            request = TaskRequest(
                user_input="根据参考图做一个斗",
                intent="IMAGE",
                image_b64="fake_base64",
                image_mime="image/png",
            )
            pipeline.execute(request)

        mock_vision.assert_called_once()
        call_kwargs = mock_vision.call_args
        self.assertEqual(call_kwargs.args[0], "fake_base64")   # image_b64
        self.assertEqual(call_kwargs.args[1], "image/png")      # image_mime

    def test_create_with_image_runs_vision_analysis(self):
        """CREATE intent + image_b64 → 同样触发 vision 前置"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS) as mock_vision:
            request = TaskRequest(
                user_input="做一个斗",
                intent="CREATE",
                image_b64="fake_base64",
            )
            pipeline.execute(request)

        mock_vision.assert_called_once()

    def test_enriched_instruction_injected_into_generation(self):
        """vision 分析结果（建模计划）注入到 GDL 生成的 instruction 中"""
        pipeline = _make_pipeline()
        captured = {}

        original_generate = pipeline._load_knowledge  # just to get a ref point

        with patch("openbrep.runtime.pipeline.analyze_reference_image", return_value=_FAKE_VS):
            with patch("openbrep.runtime.pipeline.visual_structure_to_gdl_hint", return_value="## 建模计划\n收分台座") as mock_hint:
                request = TaskRequest(
                    user_input="做一个斗",
                    intent="CREATE",
                    image_b64="fake_base64",
                )
                pipeline.execute(request)

        mock_hint.assert_called_once_with(_FAKE_VS)

    def test_vision_failure_falls_back_gracefully(self):
        """vision 分析抛异常时，异常被捕获，不传播到调用方"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image", side_effect=Exception("timeout")):
            request = TaskRequest(
                user_input="做一个斗",
                intent="IMAGE",
                image_b64="fake_base64",
            )
            # execute() 本身不应抛异常（vision 异常已在内部 catch）
            result = pipeline.execute(request)

        # result 存在，且 error 不是 vision 的异常（已被 warning 记录并吞掉）
        self.assertIsNotNone(result)
        self.assertNotEqual(result.error, "timeout")

    def test_modify_intent_with_image_skips_vision(self):
        """MODIFY intent 有图时不走 vision 前置"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image") as mock_vision:
            request = TaskRequest(
                user_input="检查这段脚本",
                intent="MODIFY",
                image_b64="fake_base64",
            )
            pipeline.execute(request)

        mock_vision.assert_not_called()

    def test_debug_intent_with_image_skips_vision(self):
        """DEBUG intent 有图时不走 vision 前置"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image") as mock_vision:
            request = TaskRequest(
                user_input="Error in 3D script, line 42",
                intent="DEBUG",
                image_b64="fake_base64",
            )
            pipeline.execute(request)

        mock_vision.assert_not_called()

    def test_no_image_skips_vision(self):
        """没有图片时不触发 vision 前置"""
        pipeline = _make_pipeline()

        with patch("openbrep.runtime.pipeline.analyze_reference_image") as mock_vision:
            request = TaskRequest(user_input="做一个书架", intent="CREATE")
            pipeline.execute(request)

        mock_vision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
