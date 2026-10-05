"""RF06（返修派单 P1）：普通（非 Codex）agent loop 的任务级截止。

原计划偏离：卡03 只为 Codex 桥接实现了 idle/task/tool 三计时器，普通 agent
loop 只有每轮 llm.timeout 与工具预算，无任务总上限。本卡补齐：

- ``[agent] agent_task_timeout`` 作用于普通 agent loop 整个任务（跨轮共享）；
- 到期按当前进度如实收尾（timeout_reason=task_deadline，非完整交付）；
- 取消与工具预算语义不变；不透明重跑。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from tests.test_modify_agent_loop import _make_project, _make_pipeline, _make_request


def _forever_tools_llm() -> MockLLM:
    """永远要求继续调用工具的替身（耗尽任务时间预算）。"""

    import time as _time

    class _LoopLLM(MockLLM):
        def generate_with_tools(self, messages, tools=None, **kwargs):
            self.call_count += 1
            _time.sleep(0.3)  # 每轮略耗时，确保任务截止先于工具预算触发
            return MockLLM(responses=[
                {"tool_calls": [{"name": "read_parameters", "arguments": {}}]},
            ]).generate_with_tools(messages, tools=tools, **kwargs)

    return _LoopLLM()


class TestAgentLoopTaskDeadline(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_task_timeout_breaks_loop_with_structured_reason(self):
        """任务截止到期：循环退出、如实收尾、结构化 task_deadline。"""
        config = GDLAgentConfig()
        config.agent.agent_task_timeout = 1  # 秒级，真实时钟可等待
        pipeline = _make_pipeline(_forever_tools_llm(), self.tmp)
        pipeline.config = config
        project = _make_project(self.tmp)
        project.save_to_disk()
        events: list[tuple[str, dict]] = []
        request = _make_request(project, self.tmp, agent_loop_budget=20,
                                on_event=lambda k, d: events.append((k, dict(d))))
        result = pipeline.execute(request)
        self.assertFalse(result.success)
        execution = result.metadata.get("execution") or {}
        self.assertTrue(execution.get("timeout"), execution)
        self.assertEqual(execution.get("timeout_reason"), "task_deadline")
        # 部分进度如实报告，不伪称完成
        self.assertIn("时间预算", result.plain_text)

    def test_task_deadline_does_not_break_normal_completion(self):
        """充足预算下正常完成：timeout=False，reason 为 None。"""
        fingerprint_path = _make_project(self.tmp)
        fingerprint_path.save_to_disk()
        from openbrep.source_fingerprint import compute_source_fingerprint

        fingerprint = compute_source_fingerprint(fingerprint_path.root)
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "edit_parameters", "arguments": {
                "expected_source_fingerprint": fingerprint,
                "operations": [{"op": "set_value", "name": "A", "value": "2"}],
            }}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "完成。",
        ])
        pipeline = _make_pipeline(mock_llm, self.tmp)
        project = _make_project(self.tmp)
        project.save_to_disk()
        result = pipeline.execute(_make_request(project, self.tmp))
        self.assertTrue(result.success, result.plain_text)
        execution = result.metadata.get("execution") or {}
        self.assertFalse(execution.get("timeout"))
        self.assertIsNone(execution.get("timeout_reason"))


if __name__ == "__main__":
    unittest.main()
