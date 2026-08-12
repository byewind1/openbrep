"""
tests/test_modify_vision_harness — P5e MODIFY 接入简化档 Vision Harness + 提取复用

覆盖（验收门禁）：
1. 带图 MODIFY（agent loop）走 harness 且 critic 不被调用（critic_pass=False、
   critic 函数零调用）
2. sha256 命中 → 零 vision LLM 调用，hint 来自复用 plan（D7）
3. 提取 hint 出现在 messages[0]（system 层），不在 user 消息（D10）
4. 无图 MODIFY 的 messages 与现状逐字节一致（零变化回归，硬门禁的测试形态）
5. micro_modify 带图守卫：带图 + "把 shelf_count 改成 5" → 不走 micro_modify
6. 落盘失败 → warning 不阻断，结果照常交付
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.pipeline import ImageRef, TaskPipeline, TaskRequest

_GENERIC_JSON = json.dumps({
    "component_type": "书架",
    "main_form": "rect_prism",
    "layers": [],
    "symmetry": [],
    "key_features": ["层板"],
    "dimension_hints": {},
    "parametrize": ["shelf_count"],
    "fix_as_ratio": [],
    "raw_description": "按图调整",
}, ensure_ascii=False)


def _sha(b64: str) -> str:
    return hashlib.sha256(base64.b64decode(b64)).hexdigest()


def _make_project(tmp_path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return proj


def _make_pipeline(mock_llm, tmp_path) -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _make_request(project, tmp_path, **overrides) -> TaskRequest:
    kwargs = dict(
        user_input="按这张图调整这个构件",
        intent="MODIFY",
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
        agent_loop=True,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


def _image_b64(data: bytes = b"test-image-bytes") -> str:
    return base64.b64encode(data).decode()


class _TempDirMixin:
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


class TestModifyVisionHarness(_TempDirMixin, unittest.TestCase):
    """带图 MODIFY（agent loop）走简化档 harness：critic 不跑、事件/元数据透出。"""

    def test_with_image_runs_harness_without_critic(self):
        """带图 MODIFY → 走 harness（critic_pass=False）；critic 函数零调用。"""
        calls: list[dict] = []

        real_run_import = None
        from openbrep.vision import harness as harness_module

        original_run = harness_module.run

        def spy_run(images, intent, user_input, llm, on_event=None, critic_pass=True):
            calls.append({
                "intent": intent,
                "critic_pass": critic_pass,
                "n_images": len(images),
            })
            return original_run(images, intent, user_input, llm, on_event=on_event, critic_pass=critic_pass)

        mock_llm = MockLLM(responses=[
            _GENERIC_JSON,  # generic 提取（analyze_reference_image 走 llm.generate）
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已按图调整完成。",
        ])
        events: list[dict] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        with patch.object(harness_module, "run", side_effect=spy_run), \
             patch.object(harness_module, "_critic_pass", wraps=harness_module._critic_pass) as critic_spy:
            pipeline = _make_pipeline(mock_llm, self.tmp)
            request = _make_request(
                _make_project(self.tmp), self.tmp,
                images=[ImageRef(token="图1", b64=_image_b64(), mime="image/png")],
                on_event=on_event,
            )
            result = pipeline.execute(request)

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intent"], "MODIFY")
        self.assertIs(calls[0]["critic_pass"], False)
        critic_spy.assert_not_called()
        # 事件流携带 vision_analysis_done（前端只读卡片数据源）
        vision_events = [e for e in events if e["type"] == "vision_analysis_done"]
        self.assertEqual(len(vision_events), 1)
        self.assertEqual(vision_events[0]["data"]["token"], "图1")
        self.assertEqual(vision_events[0]["data"]["extraction"]["schema_name"], "generic")
        # metadata 同构透出
        self.assertEqual(len(result.metadata["vision_extractions"]), 1)
        self.assertEqual(result.metadata["vision_extractions"][0]["token"], "图1")

    def test_sha256_hit_reuses_plan_zero_vision_calls(self):
        """sha256 命中 → 零 vision LLM 调用，hint 来自复用 plan（含来源模型标注）。"""
        project = _make_project(self.tmp)
        b64 = _image_b64()
        sha = _sha(b64)
        # 预置提取工件（D7 内容哈希寻址；模拟此前 CREATE 落盘）
        vision_dir = project.root / ".openbrep" / "vision"
        vision_dir.mkdir(parents=True, exist_ok=True)
        (vision_dir / f"extraction-{sha[:12]}.json").write_text(json.dumps({
            "schema_name": "lattice_window",
            "fields": {"opening_shape": "rect", "pattern_family": "冰裂",
                       "grid_topology": {"kind": "grid", "rows": 3, "cols": 4}},
            "confidence": {"opening_shape": "high", "grid_topology.rows": "low"},
            "corrections": [],
            "degraded": False,
            "critic_degraded": False,
            "raw_description": "",
            "sha256": sha,
            "model": "mock-vision-model",
            "created_at": "2026-08-12T00:00:00+00:00",
        }, ensure_ascii=False), encoding="utf-8")

        mock_llm = MockLLM(responses=["已按图完成。"])

        with patch("openbrep.vision.harness.run") as harness_spy:
            pipeline = _make_pipeline(mock_llm, self.tmp)
            request = _make_request(
                project, self.tmp,
                images=[ImageRef(token="图1", b64=b64, mime="image/png")],
            )
            result = pipeline.execute(request)

        harness_spy.assert_not_called()  # 零 harness 调用（也就零 vision LLM 调用）
        self.assertTrue(result.success)
        # 提取内容来自复用 plan（非本次提取）
        self.assertEqual(result.metadata["vision_extractions"][0]["fields"]["pattern_family"], "冰裂")
        self.assertEqual(result.metadata["vision_extractions"][0]["reused_from_model"], "mock-vision-model")
        # hint 注入 system 消息 + 复用标注
        system_content = mock_llm.call_history[0][0]["content"]
        self.assertIn("【图1】", system_content)
        self.assertIn("冰裂", system_content)
        self.assertIn("复用缓存：由 mock-vision-model 模型提取", system_content)
        # 全流程无任何 vision 图片消息（零 LLM 调用证据）
        for msgs in mock_llm.call_history:
            for m in msgs:
                self.assertNotIn("image_url", str(m.get("content", "")))

    def test_hint_in_system_not_in_user_messages(self):
        """提取 hint 在 messages[0]（system 层），不在任何 user 消息（D10）。"""
        mock_llm = MockLLM(responses=[
            _GENERIC_JSON,
            "已按图调整完成。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(
            _make_project(self.tmp), self.tmp,
            images=[ImageRef(token="图1", b64=_image_b64(), mime="image/png")],
        )
        pipeline.execute(request)

        # 第一条记录是 generic 提取调用（vision），agent loop 起始消息在后续记录里
        loop_call = next(
            msgs for msgs in mock_llm.call_history
            if msgs and msgs[0].get("role") == "system" and "Agent Loop 工作模式" in str(msgs[0].get("content", ""))
        )
        self.assertIn("参考图结构提取", loop_call[0]["content"])
        self.assertIn("【图1】", loop_call[0]["content"])
        for m in loop_call[1:]:
            self.assertNotIn("【图1】", str(m.get("content", "")))
            self.assertNotIn("参考图结构提取", str(m.get("content", "")))

    def test_no_image_messages_unchanged(self):
        """无图 MODIFY：全流程零变化（硬门禁）——不预处理、无 vision 事件/元数据。"""
        mock_llm = MockLLM(responses=["已分析完成。"])
        events: list[dict] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        with patch("openbrep.vision.multi_image.resolve_and_preprocess") as preprocess_spy:
            pipeline = _make_pipeline(mock_llm, self.tmp)
            request = _make_request(
                _make_project(self.tmp), self.tmp, on_event=on_event,
            )
            result = pipeline.execute(request)

        preprocess_spy.assert_not_called()
        self.assertTrue(result.success)
        self.assertNotIn("vision_extractions", result.metadata)
        self.assertFalse([e for e in events if e["type"] == "vision_analysis_done"])
        # messages[0] 只追加了 agent loop 协议（无任何 vision 文本），与现状一致
        system_content = mock_llm.call_history[0][0]["content"]
        from openbrep.runtime.modify_agent_loop import _AGENT_LOOP_PROTOCOL
        self.assertTrue(system_content.endswith(_AGENT_LOOP_PROTOCOL.format(budget=10)))
        self.assertNotIn("参考图", system_content)
        self.assertNotIn("【图", system_content)

    def test_micro_modify_guarded_when_images_present(self):
        """带图 + "把 shelf_count 改成 5" → 跳过 micro_modify 直走 MODIFY（agent loop）。"""
        project = _make_project(self.tmp)
        from openbrep.hsf_project import GDLParameter
        project.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer",
                                               description="层板数量", value="4"))
        mock_llm = MockLLM(responses=["已分析完成。"])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(
            project, self.tmp,
            user_input="把 shelf_count 改成 5",
            images=[ImageRef(token="图1", b64=_image_b64(), mime="image/png")],
        )
        result = pipeline.execute(request)

        # micro_modify 命中文本（无图时走确定性修改）→ 带图时必须被守卫拦下
        self.assertIn("Agent loop（实验路径）", result.plain_text)
        self.assertIn("agent_loop", result.metadata)
        self.assertNotIn("micro_modify", str(result.metadata))
        # 参数未被确定性修改（agent loop 走了 LLM，mock 未改值）
        self.assertEqual(project.get_parameter("shelf_count").value, "4")

    def test_save_failure_warns_but_does_not_block(self):
        """落盘失败 → warning 不阻断，结果照常交付。"""
        mock_llm = MockLLM(responses=[
            _GENERIC_JSON,
            "已按图调整完成。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        request = _make_request(
            _make_project(self.tmp), self.tmp,
            images=[ImageRef(token="图1", b64=_image_b64(), mime="image/png")],
        )
        with patch(
            "openbrep.vision.extraction_store.save_extraction",
            side_effect=OSError("disk full"),
        ):
            result = pipeline.execute(request)

        self.assertTrue(result.success)
        # 提取结果照常交付（事件 + metadata），不因落盘失败丢失
        self.assertEqual(len(result.metadata["vision_extractions"]), 1)
        self.assertEqual(result.metadata["vision_extractions"][0]["token"], "图1")


if __name__ == "__main__":
    unittest.main()
