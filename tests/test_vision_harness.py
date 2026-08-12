"""
tests/test_vision_harness — P5b Vision Harness（Schema Registry + S1 分型 + S2 定向提取）

覆盖：
1. schema registry：三个 YAML 加载、必需键校验、坏 YAML 报错（指名文件）、
   name/文件名不一致、required 未声明字段
2. triage：显式引用 > 位置启发（优先级用例）；schema 关键词命中 > generic 兜底
3. generic 零回归（硬门禁）：mock LLM 记录调用——多图 generic 路径产生的
   vision 调用消息 + hint 文本与 P5a 现状逐字节一致
4. lattice_window 提取：固定 JSON → fields 解析正确；坏 JSON → raw_description
   降级 + hint 含"【分析失败已降级】"标记 + vision_degraded 事件
5. 角色过滤：material 角色图不进生成调用 images；pass_raw_image=off 时不带图
6. ImageRef.sha256：预处理后哈希稳定（同字节同哈希）
"""

import base64
import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import ImageRef, TaskPipeline, TaskRequest
from openbrep.vision.harness import run as harness_run
from openbrep.vision.modeling_plan import ModelingPlan
from openbrep.vision.schema import VisualLayer, VisualStructure
from openbrep.vision.schema_registry import load_all_schemas, load_schemas_from_dir
from openbrep.vision.triage import derive_role, select_schema


_FAKE_VS = VisualStructure(
    component_type="斗",
    main_form="tapered_block",
    layers=[VisualLayer("base", "PRISM_", "台座", parametric=True)],
    key_features=["收分"],
    parametrize=["A"],
)


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


def _make_pipeline(gdl_response: str = "[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND") -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(content=gdl_response, model="mock", usage={}, finish_reason="stop")
    mock_llm.generate_with_images.return_value = LLMResponse(content=gdl_response, model="mock", usage={}, finish_reason="stop")
    pipeline._make_llm = lambda req: mock_llm
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda inst: ""
    return pipeline


# ── 1. Schema Registry ───────────────────────────────────

class TestSchemaRegistry(unittest.TestCase):
    def test_three_builtin_schemas_load(self):
        schemas = load_all_schemas()
        self.assertEqual(sorted(schemas), ["furniture_stack", "generic", "lattice_window"])
        lattice = schemas["lattice_window"]
        self.assertEqual(lattice.name, "lattice_window")
        self.assertIn("漏窗", lattice.trigger_keywords)
        self.assertIn("opening_shape", lattice.fields)
        self.assertEqual(lattice.required, ["opening_shape", "pattern_family", "grid_topology"])
        self.assertEqual(lattice.critic_checks, ["grid_topology.rows", "grid_topology.cols", "symmetry_group"])
        self.assertTrue(lattice.extract_prompt.strip())
        # generic 无 trigger 关键词（兜底 schema）
        self.assertEqual(schemas["generic"].trigger_keywords, [])
        self.assertEqual(schemas["furniture_stack"].required, ["component_type", "shelf_count"])

    def test_required_key_validation(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "broken.yaml").write_text(
                "name: broken\ntrigger: {}\nextract_prompt: x\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError) as ctx:
                load_schemas_from_dir(td)
            msg = str(ctx.exception)
            self.assertIn("broken.yaml", msg)
            self.assertIn("fields", msg)
            self.assertIn("required", msg)

    def test_bad_yaml_reports_clear_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bad.yaml").write_text("name: [unclosed\n  : :", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_schemas_from_dir(td)
            self.assertIn("bad.yaml", str(ctx.exception))

    def test_name_must_match_filename(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "other.yaml").write_text(
                "name: wrong_name\ntrigger: {keywords: []}\nextract_prompt: x\n"
                "fields: {}\nrequired: []\ncritic_checks: []\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_schemas_from_dir(td)
            self.assertIn("other.yaml", str(ctx.exception))
            self.assertIn("wrong_name", str(ctx.exception))

    def test_required_must_be_declared_in_fields(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "req.yaml").write_text(
                "name: req\ntrigger: {keywords: []}\nextract_prompt: x\n"
                "fields: {a: {type: string}}\nrequired: [a, ghost]\ncritic_checks: []\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_schemas_from_dir(td)
            self.assertIn("ghost", str(ctx.exception))

    def test_missing_schema_dir(self):
        with self.assertRaises(FileNotFoundError):
            load_schemas_from_dir("/definitely/not/a/real/schema/dir")


# ── 2. S1 triage ─────────────────────────────────────────

class TestTriage(unittest.TestCase):
    def test_explicit_reference_beats_positional_heuristic(self):
        # 显式引用优先于位置启发
        self.assertEqual(derive_role("图2", "按图2的轮廓生成", 2, 3), "outline")
        self.assertEqual(derive_role("图3", "参考图3的纹样", 3, 3), "pattern")
        self.assertEqual(derive_role("图1", "用图1的材质", 1, 3), "material")

    def test_positional_heuristic_first_image_outline(self):
        # 无显式引用：第 1 张默认 outline，其余 auto
        self.assertEqual(derive_role("图1", "做一个斗", 1, 2), "outline")
        self.assertEqual(derive_role("图2", "做一个斗", 2, 2), "auto")
        # 单图也是 outline
        self.assertEqual(derive_role("图1", "做一个斗", 1, 1), "outline")

    def test_explicit_reference_without_connective(self):
        self.assertEqual(derive_role("图1", "按图1轮廓做", 1, 2), "outline")

    def test_english_keyword(self):
        self.assertEqual(derive_role("图1", "use 图1 pattern", 1, 2), "pattern")
        self.assertEqual(derive_role("图2", "图2 material", 2, 2), "material")

    def test_schema_keyword_hit_beats_generic(self):
        schemas = load_all_schemas()
        self.assertEqual(select_schema("这是漏窗参考图", schemas), "lattice_window")
        self.assertEqual(select_schema("做一个窗花", schemas), "lattice_window")
        self.assertEqual(select_schema("lattice window", schemas), "lattice_window")
        self.assertEqual(select_schema("做一个书架", schemas), "furniture_stack")

    def test_generic_fallback_when_no_keyword(self):
        schemas = load_all_schemas()
        self.assertEqual(select_schema("生成一个斗", schemas), "generic")
        self.assertEqual(select_schema("", schemas), "generic")


# ── 3. generic 零回归（硬门禁）────────────────────────────

class TestGenericZeroRegression(unittest.TestCase):
    def _reference_p5a_messages(self, image_b64: str, image_mime: str, user_input: str) -> list:
        """P5a 现状 analyze_reference_image 发给 LLM 的 messages（原函数原 prompt）。"""
        from openbrep.vision.image_to_plan import analyze_reference_image

        captured: dict = {}

        class RecordingLLM:
            def generate(self, messages, **kwargs):
                captured["messages"] = messages
                return LLMResponse(
                    content=json.dumps({
                        "component_type": "斗", "main_form": "tapered",
                        "layers": [], "symmetry": [], "key_features": ["收分"],
                        "dimension_hints": {}, "parametrize": ["A"], "fix_as_ratio": [],
                        "raw_description": "一个斗",
                    }, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

        analyze_reference_image(image_b64, image_mime, user_input, RecordingLLM())
        return captured["messages"]

    def test_vision_call_messages_byte_identical_to_p5a(self):
        """hard gate：harness generic 路径产生的 vision 调用 messages 与 P5a 逐字节一致。"""
        image_b64 = base64.b64encode(b"fake-image-bytes").decode()
        user_input = "做一个斗"

        reference = self._reference_p5a_messages(image_b64, "image/jpeg", user_input)
        recorded: list = []

        class RecordingLLM:
            def generate(self, messages, **kwargs):
                recorded.append(messages)
                return LLMResponse(
                    content=json.dumps({
                        "component_type": "斗", "main_form": "tapered",
                        "layers": [], "symmetry": [], "key_features": ["收分"],
                        "dimension_hints": {}, "parametrize": ["A"], "fix_as_ratio": [],
                        "raw_description": "一个斗",
                    }, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

        harness_run(
            [ImageRef(token="图1", b64=image_b64, mime="image/jpeg")],
            "CREATE",
            user_input,
            RecordingLLM(),
        )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0], reference)

    def test_pipeline_generic_hint_byte_identical_to_p5a(self):
        """hard gate：多图 generic 路径的 hint 文本与 P5a 逐字节一致（user_input + 【图N】拼接）。"""
        pipeline = _make_pipeline()
        captured = {}

        def fake_generate_with_images(text_prompt, images, system_prompt=None, **kwargs):
            captured["images"] = images
            return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

        pipeline._make_llm = lambda req: MagicMock(
            generate_with_images=fake_generate_with_images,
            generate=MagicMock(
                return_value=LLMResponse(
                    content=json.dumps({
                        "component_type": "斗", "main_form": "tapered", "layers": [],
                        "symmetry": [], "key_features": ["收分"], "dimension_hints": {},
                        "parametrize": ["A"], "fix_as_ratio": [], "raw_description": "一个斗",
                    }, ensure_ascii=False),
                    model="m", usage={}, finish_reason="stop",
                ),
            ),
        )
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()) as mock_plan:
            request = TaskRequest(
                user_input="做一个斗",
                intent="CREATE",
                images=[
                    ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png"),
                    ImageRef(token="图2", b64=base64.b64encode(b"b").decode(), mime="image/jpeg"),
                ],
            )
            pipeline.execute(request)

        instruction = mock_plan.call_args.kwargs["instruction"]
        # 与 P5a 现状拼接格式逐字节一致
        self.assertTrue(instruction.startswith("做一个斗\n\n【图1】\n## 参考图建模计划（请严格按此计划生成 GDL）"))
        self.assertIn("\n\n【图2】\n## 参考图建模计划", instruction)
        # 生成调用两张图都进（图1 outline / 图2 auto 均在允许集）
        self.assertEqual(len(captured["images"]), 2)

    def test_harness_writes_roles_back_to_images(self):
        recorded = []

        class RecordingLLM:
            def generate(self, messages, **kwargs):
                recorded.append(messages)
                return LLMResponse(
                    content=json.dumps({"component_type": "斗"}, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

        images = [
            ImageRef(token="图1", b64="YQ==", mime="image/png"),
            ImageRef(token="图2", b64="Yg==", mime="image/jpeg"),
            ImageRef(token="图3", b64="Yw==", mime="image/jpeg"),
        ]
        harness_run(images, "CREATE", "按图3的材质做一个斗", RecordingLLM())
        self.assertEqual([img.role for img in images], ["outline", "auto", "material"])


# ── 4. lattice_window 定向提取 ───────────────────────────

class TestLatticeExtraction(unittest.TestCase):
    def test_fixed_json_parses_into_fields(self):
        events = []

        class FakeLLM:
            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                return LLMResponse(
                    content=json.dumps({
                        "opening_shape": "rect",
                        "pattern_family": "冰裂",
                        "grid_topology": {"kind": "grid", "rows": 4, "cols": 4, "cell_desc": "方冰裂单元"},
                        "bar_width_ratio": 0.08,
                        "frame_bar_ratio": 0.35,
                        "symmetry_group": "d4",
                        "motif_features": ["四角海棠瓣"],
                        "gdl_strategy": "PRISM_ 棱条 + FOR 网格平铺",
                        "raw_description": "方洞冰裂纹漏窗",
                    }, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

        plans = harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png")],
            "CREATE",
            "这是漏窗，按图生成",
            FakeLLM(),
            on_event=lambda *_: None,
        )
        plan = plans[0]
        self.assertIsNotNone(plan)
        self.assertEqual(plan.schema_name, "lattice_window")
        self.assertEqual(plan.fields["opening_shape"], "rect")
        self.assertEqual(plan.fields["pattern_family"], "冰裂")
        self.assertEqual(plan.fields["grid_topology"]["rows"], 4)
        self.assertEqual(plan.confidence, {k: "unknown" for k in plan.fields})
        self.assertEqual(plan.corrections, [])
        hint = plan.to_hint()
        self.assertIn("lattice_window", hint)
        self.assertIn("pattern_family: 冰裂", hint)
        self.assertNotIn("降级", hint)

    def test_bad_json_degrades_to_raw_description_with_marker(self):
        events = []

        class FakeLLM:
            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                return LLMResponse(
                    content="```json\n{这不是合法JSON\n```", model="mock", usage={}, finish_reason="stop"
                )

        def on_event(event_type, data):
            events.append((event_type, data))

        plans = harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png")],
            "CREATE",
            "这是漏窗，按图生成",
            FakeLLM(),
            on_event=on_event,
        )
        plan = plans[0]
        self.assertIsNotNone(plan)
        self.assertTrue(plan.degraded)
        self.assertTrue(plan.raw_description)
        hint = plan.to_hint()
        self.assertIn("【分析失败已降级】", hint)
        # 降级必须可见：事件流里出现 vision_degraded
        self.assertTrue(any(et == "vision_degraded" for et, _ in events))

    def test_schema_selection_hits_lattice_for_keyword(self):
        class FakeLLM:
            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                return LLMResponse(
                    content=json.dumps({"opening_shape": "circle", "pattern_family": "冰裂",
                                        "grid_topology": {"kind": "radial"}}, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

        plans = harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png")],
            "CREATE",
            "做一个窗花",
            FakeLLM(),
        )
        self.assertEqual(plans[0].schema_name, "lattice_window")


# ── 5. 角色过滤（D5/D9）─────────────────────────────────

class TestGenerationRoleFilter(unittest.TestCase):
    def test_material_image_excluded_from_generation(self):
        from openbrep.runtime.pipeline import _generation_images

        images = [
            ImageRef(token="图1", b64="YQ==", mime="image/png", role="outline"),
            ImageRef(token="图2", b64="Yg==", mime="image/jpeg", role="material"),
            ImageRef(token="图3", b64="Yw==", mime="image/jpeg", role="auto"),
        ]
        gen = _generation_images(images, GDLAgentConfig())
        self.assertEqual([g["token"] for g in gen], ["图1", "图3"])

    def test_pass_raw_image_off_returns_no_images(self):
        from openbrep.runtime.pipeline import _generation_images

        config = GDLAgentConfig()
        config.vision.pass_raw_image = False
        images = [ImageRef(token="图1", b64="YQ==", mime="image/png", role="outline")]
        self.assertEqual(_generation_images(images, config), [])

    def test_pipeline_end_to_end_material_filtered(self):
        """端到端：图3 显式 material 角色 → 生成调用不带图3。"""
        pipeline = _make_pipeline()
        captured = {}

        def fake_generate_with_images(text_prompt, images, system_prompt=None, **kwargs):
            captured["images"] = images
            return LLMResponse(content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND", model="mock", usage={}, finish_reason="stop")

        pipeline._make_llm = lambda req: MagicMock(
            generate_with_images=fake_generate_with_images,
            generate=MagicMock(
                return_value=LLMResponse(
                    content=json.dumps({"component_type": "斗"}, ensure_ascii=False),
                    model="m", usage={}, finish_reason="stop",
                ),
            ),
        )
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
            request = TaskRequest(
                user_input="按图3的材质做一个斗",
                intent="CREATE",
                images=[
                    ImageRef(token="图1", b64="YQ==", mime="image/png"),
                    ImageRef(token="图2", b64="Yg==", mime="image/jpeg"),
                    ImageRef(token="图3", b64="Yw==", mime="image/jpeg"),
                ],
            )
            pipeline.execute(request)

        # generate_only 会剥掉 token（只送 b64/mime），断言 material 图3 被过滤、图1图2 保留
        self.assertEqual(len(captured["images"]), 2)
        self.assertEqual(
            [g["b64"] for g in captured["images"]],
            [base64.b64encode(b"a").decode(), base64.b64encode(b"b").decode()],
        )


# ── 6. sha256 ────────────────────────────────────────────

class TestImageSha256(unittest.TestCase):
    def test_sha256_stable_for_same_bytes(self):
        from openbrep.vision.multi_image import sha256_of_b64

        b64 = base64.b64encode(b"same-bytes").decode()
        self.assertEqual(sha256_of_b64(b64), sha256_of_b64(b64))

    def test_sha256_differs_for_different_bytes(self):
        from openbrep.vision.multi_image import sha256_of_b64

        self.assertNotEqual(
            sha256_of_b64(base64.b64encode(b"a").decode()),
            sha256_of_b64(base64.b64encode(b"b").decode()),
        )

    def test_resolve_and_preprocess_sets_sha256(self):
        from openbrep.vision.multi_image import resolve_and_preprocess

        b64 = base64.b64encode(b"some-image").decode()
        resolved = resolve_and_preprocess([ImageRef(token="图1", b64=b64, mime="image/png")])
        self.assertEqual(len(resolved[0].sha256), 64)  # sha256 hex
        # 同字节同哈希
        resolved2 = resolve_and_preprocess([ImageRef(token="图1", b64=b64, mime="image/png")])
        self.assertEqual(resolved[0].sha256, resolved2[0].sha256)

    def test_no_bytes_sha256_empty(self):
        from openbrep.vision.multi_image import resolve_and_preprocess

        resolved = resolve_and_preprocess([ImageRef(token="图1", b64="", path=None)])
        self.assertEqual(resolved[0].sha256, "")


if __name__ == "__main__":
    unittest.main()
