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
    _EXTRACT_ENVELOPE = {
        "fields": {
            "opening_shape": "rect",
            "pattern_family": "冰裂",
            "grid_topology": {"kind": "grid", "rows": 4, "cols": 4, "cell_desc": "方冰裂单元"},
            "bar_width_ratio": 0.08,
            "frame_bar_ratio": 0.35,
            "symmetry_group": "d4",
            "motif_features": ["四角海棠瓣"],
            "gdl_strategy": "PRISM_ 棱条 + FOR 网格平铺",
        },
        "confidence": {
            "opening_shape": "high",
            "pattern_family": "high",
            "grid_topology": "high",
            "bar_width_ratio": "high",
            "frame_bar_ratio": "high",
            "symmetry_group": "high",
            "motif_features": "high",
            "gdl_strategy": "high",
        },
        "raw_description": "方洞冰裂纹漏窗",
    }
    _CRITIC_ALL_MATCH = {
        "verdicts": {
            "grid_topology.rows": {"verdict": "match", "evidence": "图中行数与提取一致"},
            "grid_topology.cols": {"verdict": "match", "evidence": "图中列数与提取一致"},
            "symmetry_group": {"verdict": "match", "evidence": "四重旋转对称"},
        }
    }

    def test_fixed_json_parses_into_fields(self):
        """P5c 信封解析 + critic 全 match：字段/置信度正确，hint 无降级无低置信。"""
        responses = [
            json.dumps(self._EXTRACT_ENVELOPE, ensure_ascii=False),
            json.dumps(self._CRITIC_ALL_MATCH, ensure_ascii=False),
        ]

        class FakeLLM:
            def __init__(self):
                self.call_count = 0

            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                content = responses[self.call_count]
                self.call_count += 1
                return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")

        llm = FakeLLM()
        plans = harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png")],
            "CREATE",
            "这是漏窗，按图生成",
            llm,
            on_event=lambda *_: None,
        )
        plan = plans[0]
        self.assertIsNotNone(plan)
        self.assertEqual(plan.schema_name, "lattice_window")
        self.assertEqual(plan.fields["opening_shape"], "rect")
        self.assertEqual(plan.fields["pattern_family"], "冰裂")
        self.assertEqual(plan.fields["grid_topology"]["rows"], 4)
        # P5c：字段级置信度来自提取信封 + critic 核对（不再是全 unknown）
        self.assertEqual(plan.confidence["opening_shape"], "high")
        self.assertEqual(plan.confidence["grid_topology.rows"], "high")  # critic match
        self.assertEqual(plan.confidence["grid_topology.cols"], "high")
        self.assertEqual(plan.confidence["symmetry_group"], "high")
        self.assertEqual(plan.corrections, [])
        self.assertFalse(plan.critic_degraded)
        # 每图两次调用：提取 1 → critic 1
        self.assertEqual(llm.call_count, 2)
        hint = plan.to_hint()
        self.assertIn("lattice_window", hint)
        self.assertIn("pattern_family: 冰裂", hint)
        self.assertNotIn("降级", hint)
        self.assertNotIn("低置信", hint)

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


# ── 4b. S3 critic 校验（P5c，设计 D3）────────────────────

class TestCriticPass(unittest.TestCase):
    """S3 critic：修正语义、越权防护、降级（D8）、开关、degraded 跳过、hint 渲染。"""

    _EXTRACT = TestLatticeExtraction._EXTRACT_ENVELOPE

    def _seq_llm(self, responses, raise_on=None):
        """按序返回响应的假 LLM（提取 1 → critic 1 → 提取 2 → critic 2 …）。"""
        class _Seq:
            def __init__(self):
                self.call_count = 0
                self.prompts = []

            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                self.prompts.append(text_prompt)
                idx = self.call_count
                self.call_count += 1
                if raise_on and idx == raise_on:
                    raise RuntimeError(f"simulated failure at call {idx}")
                content = responses[idx] if isinstance(responses, list) else responses
                return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")
        return _Seq()

    def _run(self, llm, intent="CREATE", critic_pass=True, user_input="这是漏窗", on_event=None):
        return harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png")],
            intent, user_input, llm,
            on_event=on_event or (lambda *_: None),
            critic_pass=critic_pass,
        )[0]

    # ── 修正语义（D3）────────────────────────────────────

    def test_mismatch_with_evidence_corrects_value_and_records(self):
        critic = json.dumps({"verdicts": {
            "grid_topology.rows": {"verdict": "mismatch", "evidence": "图中棂条为 3 行", "value": 3},
            "grid_topology.cols": {"verdict": "match", "evidence": "图中列数与提取一致"},
            "symmetry_group": {"verdict": "unknown", "evidence": "图被遮挡，无法判断"},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), critic])
        plan = self._run(llm)
        # mismatch + 依据 → 改值，corrections 记 old/new/evidence，confidence=high
        self.assertEqual(plan.fields["grid_topology"]["rows"], 3)
        self.assertEqual(plan.corrections, [{
            "field": "grid_topology.rows",
            "old": 4,
            "new": 3,
            "evidence": "图中棂条为 3 行",
        }])
        self.assertEqual(plan.confidence["grid_topology.rows"], "high")
        # match → high；unknown → low 不改值
        self.assertEqual(plan.fields["grid_topology"]["cols"], 4)
        self.assertEqual(plan.confidence["grid_topology.cols"], "high")
        self.assertEqual(plan.fields["symmetry_group"], "d4")
        self.assertEqual(plan.confidence["symmetry_group"], "low")
        self.assertFalse(plan.critic_degraded)

    def test_mismatch_without_evidence_or_value_flags_low_without_change(self):
        critic = json.dumps({"verdicts": {
            "grid_topology.rows": {"verdict": "mismatch", "value": 2},          # 无依据
            "grid_topology.cols": {"verdict": "mismatch", "evidence": "不对"},  # 无修正值
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), critic])
        plan = self._run(llm)
        self.assertEqual(plan.fields["grid_topology"]["rows"], 4)
        self.assertEqual(plan.fields["grid_topology"]["cols"], 4)
        self.assertEqual(plan.corrections, [])
        self.assertEqual(plan.confidence["grid_topology.rows"], "low")
        self.assertEqual(plan.confidence["grid_topology.cols"], "low")

    def test_critic_value_type_coerced_to_int(self):
        """old 是 int 而 critic 给字符串数字 → 转回 int，corrections 记录 int。"""
        critic = json.dumps({"verdicts": {
            "grid_topology.rows": {"verdict": "mismatch", "evidence": "3 行", "value": "3"},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), critic])
        plan = self._run(llm)
        self.assertEqual(plan.fields["grid_topology"]["rows"], 3)
        self.assertIs(type(plan.fields["grid_topology"]["rows"]), int)
        self.assertEqual(plan.corrections[0]["new"], 3)

    def test_furniture_shelf_count_corrected(self):
        """顶层字段（shelf_count）修正：critic_checks 覆盖 furniture_stack。"""
        extract = {
            "fields": {"component_type": "书架", "shelf_count": 4, "shelf_layout": "等距",
                       "frame_profile": "方管", "symmetry": ["x"], "key_features": ["多层"],
                       "parametrize": ["width", "shelf_count"], "fix_as_ratio": []},
            "confidence": {k: "high" for k in
                           ["component_type", "shelf_count", "shelf_layout", "frame_profile",
                            "symmetry", "key_features", "parametrize", "fix_as_ratio"]},
            "raw_description": "四层书架",
        }
        critic = json.dumps({"verdicts": {
            "shelf_count": {"verdict": "mismatch", "evidence": "图中可见 5 层板", "value": 5},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(extract, ensure_ascii=False), critic])
        plan = self._run(llm, user_input="做一个书架")
        self.assertEqual(plan.schema_name, "furniture_stack")
        self.assertEqual(plan.fields["shelf_count"], 5)
        self.assertEqual(plan.corrections, [{"field": "shelf_count", "old": 4, "new": 5,
                                             "evidence": "图中可见 5 层板"}])
        self.assertEqual(plan.confidence["shelf_count"], "high")

    # ── 越权防护（D3）────────────────────────────────────

    def test_out_of_scope_fields_ignored(self):
        critic = json.dumps({"verdicts": {
            "grid_topology.rows": {"verdict": "match", "evidence": "一致"},
            "opening_shape": {"verdict": "mismatch", "evidence": "其实是圆形", "value": "circle"},
            "gdl_strategy": {"verdict": "mismatch", "evidence": "换成别的", "value": "HACK"},
            "bar_width_ratio": {"verdict": "mismatch", "evidence": "更粗", "value": 0.2},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), critic])
        plan = self._run(llm)
        # critic_checks 之外的字段：值不动、不记 corrections、置信度不被 critic 改动
        self.assertEqual(plan.fields["opening_shape"], "rect")
        self.assertEqual(plan.fields["gdl_strategy"], "PRISM_ 棱条 + FOR 网格平铺")
        self.assertEqual(plan.fields["bar_width_ratio"], 0.08)
        self.assertEqual(plan.corrections, [])
        # 范围内的字段正常处理
        self.assertEqual(plan.confidence["grid_topology.rows"], "high")

    # ── 降级（D8）────────────────────────────────────────

    def test_critic_failure_marks_all_unknown_and_continues(self):
        events = []
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False)], raise_on=1)
        plan = self._run(llm, on_event=lambda et, d: events.append((et, d)))
        # 不阻塞流程：plan 仍返回，字段值保留
        self.assertIsNotNone(plan)
        self.assertEqual(plan.fields["opening_shape"], "rect")
        # 全字段 confidence=unknown + critic_degraded 标记
        self.assertTrue(plan.critic_degraded)
        self.assertEqual(set(plan.confidence.values()), {"unknown"})
        self.assertEqual(plan.corrections, [])
        # 降级必须在事件流可见
        self.assertTrue(any(et == "vision_degraded" for et, _ in events))
        self.assertIn("【critic 校验已降级】", plan.to_hint())

    def test_critic_unparseable_output_degrades_like_failure(self):
        """critic 输出不是 JSON / 没有 verdicts → 视为校验不可用（D8），不装核过。"""
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), "```json\n不是JSON\n```"])
        plan = self._run(llm)
        self.assertTrue(plan.critic_degraded)
        self.assertEqual(set(plan.confidence.values()), {"unknown"})
        self.assertIn("【critic 校验已降级】", plan.to_hint())

    def test_degraded_extraction_skips_critic(self):
        """degraded 的图没有可信 JSON 可核 → 跳过 critic（只 1 次调用）。"""
        llm = self._seq_llm(["not json at all"])
        plan = self._run(llm)
        self.assertTrue(plan.degraded)
        self.assertFalse(plan.critic_degraded)
        self.assertEqual(llm.call_count, 1)

    # ── 开关与触发条件 ───────────────────────────────────

    def test_critic_pass_off_skips_critic_call(self):
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False)])
        plan = self._run(llm, critic_pass=False)
        self.assertEqual(llm.call_count, 1)  # 只有提取
        self.assertEqual(plan.corrections, [])
        self.assertFalse(plan.critic_degraded)
        self.assertEqual(plan.confidence["opening_shape"], "high")  # 提取置信度保留

    def test_critic_not_run_for_modify_intent(self):
        """D3：MODIFY 简化档不跑 critic。"""
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False)])
        plan = self._run(llm, intent="MODIFY")
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(plan.corrections, [])
        self.assertFalse(plan.critic_degraded)

    # ── hint 渲染（P5c）──────────────────────────────────

    def test_hint_renders_low_and_correction_markers(self):
        critic = json.dumps({"verdicts": {
            "grid_topology.rows": {"verdict": "mismatch", "evidence": "3 行", "value": 3},
            "grid_topology.cols": {"verdict": "unknown", "evidence": "看不清"},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(self._EXTRACT, ensure_ascii=False), critic])
        plan = self._run(llm)
        hint = plan.to_hint()
        # 嵌套修正：critic 修正：路径 旧→新
        self.assertIn("（critic 修正：grid_topology.rows 4→3）", hint)
        # 嵌套低置信：点路径标注
        self.assertIn("（低置信：grid_topology.cols）", hint)
        self.assertNotIn("降级", hint)

    def test_hint_renders_top_level_correction_marker(self):
        extract = {
            "fields": {"component_type": "书架", "shelf_count": 4, "shelf_layout": "等距",
                       "frame_profile": "方管", "symmetry": ["x"], "key_features": ["多层"],
                       "parametrize": ["width", "shelf_count"], "fix_as_ratio": []},
            "confidence": {"shelf_count": "high"},
            "raw_description": "四层书架",
        }
        critic = json.dumps({"verdicts": {
            "shelf_count": {"verdict": "mismatch", "evidence": "5 层", "value": 5},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(extract, ensure_ascii=False), critic])
        plan = self._run(llm, user_input="做一个书架")
        hint = plan.to_hint()
        # 顶层修正：shelf_count: 5（critic 修正：4→5）
        self.assertIn("shelf_count: 5（critic 修正：4→5）", hint)

    def test_hint_renders_top_level_low_marker(self):
        extract = {
            "fields": {"component_type": "书架", "shelf_count": None, "shelf_layout": "等距",
                       "frame_profile": "方管", "symmetry": ["x"], "key_features": ["多层"],
                       "parametrize": ["width", "shelf_count"], "fix_as_ratio": []},
            "confidence": {"component_type": "high", "shelf_count": "high"},  # null 值强制 low
            "raw_description": "书架",
        }
        critic = json.dumps({"verdicts": {
            "shelf_count": {"verdict": "unknown", "evidence": "层板被遮挡"},
        }}, ensure_ascii=False)
        llm = self._seq_llm([json.dumps(extract, ensure_ascii=False), critic])
        plan = self._run(llm, user_input="做一个书架")
        hint = plan.to_hint()
        self.assertIn("shelf_count: null（低置信）", hint)


# ── 4c. 端到端：critic_pass 从 [vision] config 接线 ───────

class TestCriticPipelineWiring(unittest.TestCase):
    """IMAGE 任务走生产 pipeline：critic_pass 由 config 控制，修正值进入生成指令。"""

    _EXTRACT = TestLatticeExtraction._EXTRACT_ENVELOPE

    def _run(self, critic_pass: bool) -> tuple:
        pipeline = _make_pipeline()
        pipeline.config.vision.critic_pass = critic_pass
        captured = {"instruction": None, "vision_calls": 0}

        class FakeLLM:
            def generate(self, messages, **kwargs):
                return LLMResponse(
                    content=json.dumps({"component_type": "漏窗"}, ensure_ascii=False),
                    model="m", usage={}, finish_reason="stop",
                )

            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                captured["vision_calls"] += 1
                if captured["vision_calls"] == 1:
                    content = json.dumps(TestCriticPipelineWiring._EXTRACT, ensure_ascii=False)
                else:
                    content = json.dumps({"verdicts": {
                        "grid_topology.rows": {"verdict": "mismatch", "evidence": "图中 3 行", "value": 3},
                        "grid_topology.cols": {"verdict": "match", "evidence": "一致"},
                        "symmetry_group": {"verdict": "match", "evidence": "四重对称"},
                    }}, ensure_ascii=False)
                return LLMResponse(content=content, model="m", usage={}, finish_reason="stop")

            def generate_with_images(self, text_prompt, images, system_prompt=None, **kwargs):
                captured["instruction"] = text_prompt
                return LLMResponse(
                    content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND",
                    model="m", usage={}, finish_reason="stop",
                )

        llm = FakeLLM()
        pipeline._make_llm = lambda req: llm
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
            request = TaskRequest(
                user_input="这是漏窗，按图生成",
                intent="IMAGE",
                images=[ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png")],
            )
            result = pipeline.execute(request)
        return captured, result

    def test_critic_pass_on_corrects_value_into_generation_instruction(self):
        captured, result = self._run(critic_pass=True)
        # 每图两次调用：提取 + critic
        self.assertEqual(captured["vision_calls"], 2)
        # 修正值进入生成指令（hint 渲染 旧→新）
        self.assertIn("（critic 修正：grid_topology.rows 4→3）", captured["instruction"])

    def test_critic_pass_off_skips_critic_end_to_end(self):
        captured, result = self._run(critic_pass=False)
        self.assertEqual(captured["vision_calls"], 1)  # 只有提取
        self.assertNotIn("critic 修正", captured["instruction"])


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


# ── 7. P5d-1 提取透出（metadata + 事件 payload）──────────

class TestExtractionPayload(unittest.TestCase):
    """P5d-1：提取结果成为可见工件——harness 事件 payload 携带提取摘要，
    pipeline 把 plans 序列化进 TaskResult.metadata["vision_extractions"]。"""

    _EXTRACT = TestLatticeExtraction._EXTRACT_ENVELOPE

    def _schema_llm(self, critic_pass: bool = True):
        extract = self._EXTRACT  # 闭包捕获（_Seq 实例没有类属性）

        class _Seq:
            def __init__(self):
                self.call_count = 0

            def generate_with_image(self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs):
                idx = self.call_count
                self.call_count += 1
                if critic_pass and idx % 2 == 1:
                    return LLMResponse(
                        content=json.dumps({"verdicts": {
                            "grid_topology.rows": {"verdict": "mismatch", "evidence": "图中 3 行", "value": 3},
                            "grid_topology.cols": {"verdict": "match", "evidence": "一致"},
                            "symmetry_group": {"verdict": "match", "evidence": "四重对称"},
                        }}, ensure_ascii=False),
                        model="mock", usage={}, finish_reason="stop",
                    )
                return LLMResponse(
                    content=json.dumps(extract, ensure_ascii=False),
                    model="mock", usage={}, finish_reason="stop",
                )

            def generate_with_images(self, text_prompt, images, system_prompt=None, **kwargs):
                return LLMResponse(
                    content="[FILE: scripts/3d.gdl]\nBLOCK 1,1,1\nEND",
                    model="mock", usage={}, finish_reason="stop",
                )

        return _Seq()

    def test_harness_event_payload_carries_extraction_summary(self):
        """vision_analysis_done 事件 payload.extraction = 提取摘要（含 critic 修正后状态）。"""
        events = []
        llm = self._schema_llm(critic_pass=True)
        sha256 = "11" * 32
        plans = harness_run(
            [ImageRef(token="图1", b64="YQ==", mime="image/png", sha256=sha256)],
            "CREATE",
            "这是漏窗，按图生成",
            llm,
            on_event=lambda t, d: events.append((t, d)),
        )
        self.assertIsNotNone(plans[0])
        done = [d for t, d in events if t == "vision_analysis_done"]
        self.assertEqual(len(done), 1)
        payload = done[0]
        self.assertEqual(payload["schema_name"], "lattice_window")
        self.assertEqual(payload["token"], "图1")
        ext = payload["extraction"]
        # 提取摘要：字段/置信度/修正/降级标记/sha256 齐全
        self.assertEqual(ext["schema_name"], "lattice_window")
        self.assertEqual(ext["fields"]["opening_shape"], "rect")
        self.assertEqual(ext["confidence"]["grid_topology.rows"], "high")
        self.assertEqual(ext["corrections"][0]["field"], "grid_topology.rows")
        self.assertEqual(ext["corrections"][0]["old"], 4)
        self.assertEqual(ext["corrections"][0]["new"], 3)
        self.assertFalse(ext["degraded"])
        self.assertFalse(ext["critic_degraded"])
        self.assertEqual(ext["sha256"], sha256)
        # payload 必须 JSON 可序列化（纯 JSON 形状，dataclass 已 asdict）
        json.dumps(payload, ensure_ascii=False)

    def test_pipeline_metadata_lists_plans_and_skipped_images(self):
        """metadata["vision_extractions"]：每图一条（含 schema/fields/confidence/
        corrections/degraded/critic_degraded/sha256）；无字节图记 {token, skipped: true}。"""
        pipeline = _make_pipeline()
        llm = self._schema_llm(critic_pass=True)
        pipeline._make_llm = lambda req: llm
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
            request = TaskRequest(
                user_input="这是漏窗，按图生成",
                intent="IMAGE",
                images=[
                    ImageRef(token="图1", b64=base64.b64encode(b"a").decode(), mime="image/png"),
                    ImageRef(token="图2", b64=base64.b64encode(b"b").decode(), mime="image/jpeg"),
                    ImageRef(token="图3", b64="", path=None),  # 无字节 → skipped
                ],
            )
            result = pipeline.execute(request)

        extractions = (result.metadata or {}).get("vision_extractions")
        self.assertIsNotNone(extractions)
        self.assertEqual(len(extractions), 3)
        for idx in (0, 1):
            entry = extractions[idx]
            self.assertEqual(entry["token"], ["图1", "图2"][idx])
            self.assertEqual(entry["schema_name"], "lattice_window")
            self.assertEqual(entry["fields"]["pattern_family"], "冰裂")
            self.assertIn("confidence", entry)
            self.assertIn("corrections", entry)
            self.assertIn("degraded", entry)
            self.assertIn("critic_degraded", entry)
            self.assertEqual(len(entry["sha256"]), 64)
            # 条目必须 JSON 可序列化（service 落盘直接消费）
            json.dumps(entry, ensure_ascii=False)
        # 无字节图：{token, skipped: true}
        self.assertEqual(extractions[2], {"token": "图3", "skipped": True})
        # injected_skills 合并后 vision_extractions 仍保留
        self.assertIn("injected_skills", result.metadata)

    def test_pipeline_metadata_empty_for_plain_create(self):
        """无图 CREATE：metadata 不含 vision_extractions（不污染）。"""
        pipeline = _make_pipeline()
        with patch("openbrep.runtime.pipeline.plan_gdl_object", return_value=_FakeObjectPlan()):
            result = pipeline.execute(TaskRequest(user_input="做一个书架", intent="CREATE"))
        self.assertNotIn("vision_extractions", result.metadata or {})


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
