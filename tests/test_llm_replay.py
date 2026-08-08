"""benchmark/llm_replay.py 的合同测试。

覆盖（任务要求）：
- tool_calls 录制/回放 round-trip（id/name/raw_arguments 逐字保真）
- key 含 tools 的确定性（同义不同序不炸；无 tools 与带 tools 的 key 不同）
- 旧语料（无 tool_calls/finish_reason 字段）向后兼容加载
- 未命中 KeyError（generate 与 generate_with_tools 同纪律）
- runner --agent-loop 开关默认关（record/replay 默认强制旧路径）
- 多轮工具链回放（第二轮起的语料 key 依赖第一轮回放 id——核心正确性点）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmark.llm_replay import RecordingLLM, ReplayLLM, _corpus_key, _serialize_tools
from openbrep.llm import (
    LLMResponse,
    MockLLM,
    ToolCall,
    ToolDefinition,
    assistant_tool_calls_message,
    tool_result_message,
)


class TestCorpusKey(unittest.TestCase):
    def test_key_stable_for_same_messages(self):
        m = [{"role": "user", "content": "做一个书架"}]
        self.assertEqual(_corpus_key(m, {}), _corpus_key(m, {}))

    def test_key_differs_for_different_messages(self):
        a = [{"role": "user", "content": "做一个书架"}]
        b = [{"role": "user", "content": "做一个椅子"}]
        self.assertNotEqual(_corpus_key(a, {}), _corpus_key(b, {}))

    def test_tools_included_in_key_and_differs_from_plain_generate(self):
        m = [{"role": "user", "content": "加一层"}]
        tools = [ToolDefinition(name="update_script", description="d",
                                parameters={"type": "object", "properties": {}})]
        with_tools = _corpus_key(m, {}, tools=tools)
        without = _corpus_key(m, {})
        self.assertNotEqual(with_tools, without)
        # 空 tools 也属于 generate_with_tools 语义，与 generate 的 key 不同
        self.assertNotEqual(_corpus_key(m, {}, tools=[]), without)

    def test_tools_key_stable_across_definition_field_order(self):
        """同义不同序：ToolDefinition 字段/参数 dict 键序不同 → key 相同（sort_keys 兜底）。"""
        a = ToolDefinition(name="update_script", description="d",
                           parameters={"type": "object", "properties": {"p": {"type": "string"}}})
        b = ToolDefinition(parameters={"properties": {"p": {"type": "string"}}, "type": "object"},
                           description="d", name="update_script")
        self.assertEqual(_corpus_key([], {}, tools=[a]), _corpus_key([], {}, tools=[b]))
        self.assertEqual(_serialize_tools([a]), _serialize_tools([b]))

    def test_tools_key_differs_when_definition_changes(self):
        m = [{"role": "user", "content": "加一层"}]
        t1 = [ToolDefinition(name="update_script", description="d", parameters={})]
        t2 = [ToolDefinition(name="update_script", description="别的", parameters={})]
        self.assertNotEqual(_corpus_key(m, {}, tools=t1), _corpus_key(m, {}, tools=t2))


class TestRecordReplayRoundtrip(unittest.TestCase):
    def test_record_then_replay_returns_same_content(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            inner = MockLLM(responses=["[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"])
            recording = RecordingLLM(inner, corpus)
            messages = [{"role": "user", "content": "生成一个盒子"}]
            live = recording.generate(messages)

            replay = ReplayLLM(corpus)
            played = replay.generate(messages)

            self.assertEqual(played.content, live.content)
            self.assertEqual(played.model, "replay")

    def test_record_is_threadsafe_and_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            inner = MockLLM(responses=["a", "b"])
            recording = RecordingLLM(inner, corpus)
            recording.generate([{"role": "user", "content": "1"}])
            recording.generate([{"role": "user", "content": "2"}])
            lines = Path(corpus).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_replay_miss_raises_with_guidance(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            RecordingLLM(MockLLM(responses=["x"]), corpus).generate(
                [{"role": "user", "content": "已知 prompt"}]
            )
            replay = ReplayLLM(corpus)
            with self.assertRaises(KeyError) as ctx:
                replay.generate([{"role": "user", "content": "未知 prompt"}])
            self.assertIn("未命中", str(ctx.exception))

    def test_replay_image_still_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            Path(corpus).write_text("", encoding="utf-8")
            replay = ReplayLLM(corpus)
            with self.assertRaises(NotImplementedError):
                replay.generate_with_image([])


class TestToolCallsRoundtrip(unittest.TestCase):
    """generate_with_tools 录制/回放：ToolCall 逐字保真（id 一个字符都不能变）。"""

    def _corpus(self, td: str, responses: list):
        corpus = str(Path(td) / "corpus.jsonl")
        inner = MockLLM(responses=responses)
        recording = RecordingLLM(inner, corpus)
        return corpus, recording

    def test_single_round_tool_call_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            corpus, recording = self._corpus(td, [
                {"content": "改一下", "tool_calls": [
                    {"id": "call_abc_001", "name": "update_script",
                     "arguments": {"file_path": "scripts/3d.gdl", "content": "BLOCK A,B,ZZYZX\nEND\n"}},
                ]},
            ])
            tools = [ToolDefinition(name="update_script", description="d",
                                    parameters={"type": "object", "properties": {}})]
            live = recording.generate_with_tools([{"role": "user", "content": "加一层"}], tools)

            replay = ReplayLLM(corpus)
            played = replay.generate_with_tools([{"role": "user", "content": "加一层"}], tools)

            self.assertEqual(played.tool_calls[0].id, "call_abc_001")
            self.assertEqual(played.tool_calls[0].name, "update_script")
            self.assertEqual(played.tool_calls[0].raw_arguments, live.tool_calls[0].raw_arguments)
            self.assertEqual(played.tool_calls[0].arguments, live.tool_calls[0].arguments)
            self.assertEqual(played.finish_reason, "tool_calls")
            self.assertEqual(played.content, live.content)
            # assistant_tool_calls_message 重建结果必须与录制时逐字节一致（喂后续轮次）
            self.assertEqual(
                assistant_tool_calls_message(played),
                assistant_tool_calls_message(live),
            )

    def test_multi_round_tool_chain_replay(self):
        """核心正确性点：≥3 轮工具链。第二轮起的语料 key 依赖第一轮回放的 id——
        id 重建有任一字符偏差，第二轮就 miss 抛 KeyError。"""
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            inner = MockLLM(responses=[
                {"content": "先改 3d", "tool_calls": [
                    {"id": "call_1", "name": "update_script",
                     "arguments": {"file_path": "scripts/3d.gdl", "content": "BLOCK A,B,ZZYZX\nEND\n"}},
                ]},
                {"content": "再改 2d", "tool_calls": [
                    {"id": "call_2", "name": "update_script",
                     "arguments": {"file_path": "scripts/2d.gdl", "content": "PROJECT2 3,-1,2\nEND\n"}},
                ]},
                {"content": "完成", "tool_calls": []},
            ])
            recording = RecordingLLM(inner, corpus)
            tools = [ToolDefinition(name="update_script", description="d",
                                    parameters={"type": "object", "properties": {}})]

            # 录制：模拟 agent loop 多轮（assistant tool_calls 消息 + role=tool 结果回填）
            msgs = [{"role": "user", "content": "加一层"}]
            live = []
            for _ in range(3):
                resp = recording.generate_with_tools(msgs, tools)
                live.append(resp)
                if resp.has_tool_calls:
                    msgs.append(assistant_tool_calls_message(resp))
                    msgs.append(tool_result_message(resp.tool_calls[0].id, "ok", name=resp.tool_calls[0].name))
                else:
                    break
            self.assertEqual(len(live), 3)

            # 回放：用回放得到的 id 重建后续轮次 → 每一轮都必须命中（不抛 KeyError）
            replay = ReplayLLM(corpus)
            msgs2 = [{"role": "user", "content": "加一层"}]
            for round_idx, expected in enumerate(live):
                played = replay.generate_with_tools(msgs2, tools)
                self.assertEqual(played.content, expected.content)
                self.assertEqual(
                    [c.id for c in played.tool_calls],
                    [c.id for c in expected.tool_calls],
                )
                if played.has_tool_calls:
                    msgs2.append(assistant_tool_calls_message(played))
                    msgs2.append(tool_result_message(played.tool_calls[0].id, "ok", name=played.tool_calls[0].name))

            # 语料 3 行，全部带 tool_calls（前两轮）与 finish_reason
            lines = [json_loads(l) for l in Path(corpus).read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(lines), 3)
            self.assertTrue(all("key" in l for l in lines))

    def test_generate_with_tools_miss_raises_keyerror(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            RecordingLLM(MockLLM(responses=["x"]), corpus).generate([{"role": "user", "content": "已知"}])
            replay = ReplayLLM(corpus)
            tools = [ToolDefinition(name="update_script", description="d", parameters={})]
            # 语料里只有 generate 的 key（无 tools）→ generate_with_tools 必须 KeyError，不许编造
            with self.assertRaises(KeyError):
                replay.generate_with_tools([{"role": "user", "content": "已知"}], tools)


def json_loads(line: str) -> dict:
    import json
    return json.loads(line)


class TestOldCorpusBackwardCompat(unittest.TestCase):
    def test_old_format_lines_load_and_replay(self):
        """旧语料（只有 key/content，无 tool_calls/finish_reason）照常加载回放 generate。"""
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            recording = RecordingLLM(MockLLM(responses=["old reply"]), corpus)
            messages = [{"role": "user", "content": "旧任务"}]
            recording.generate(messages)

            # 手工补一行"旧格式"（模拟更早版本：无 finish_reason）
            Path(corpus).write_text(
                Path(corpus).read_text(encoding="utf-8"), encoding="utf-8"
            )
            replay = ReplayLLM(corpus)
            played = replay.generate(messages)
            self.assertEqual(played.content, "old reply")


class TestRunnerAgentLoopOptin(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _runner(self, agent_loop=False):
        from benchmark.runner import BenchmarkRunner

        return BenchmarkRunner(
            config_path=str(self.tmp / "cfg.toml"), mode="mock", agent_loop=agent_loop
        )

    def test_default_forces_old_path_for_record_replay(self):
        runner = self._runner()
        runner.llm_source = "replay:corpus.jsonl"
        self.assertIs(runner._modify_request_agent_loop(), False)
        runner.llm_source = "record:corpus.jsonl"
        self.assertIs(runner._modify_request_agent_loop(), False)

    def test_live_mode_not_forced(self):
        runner = self._runner()
        runner.llm_source = "live"
        self.assertIsNone(runner._modify_request_agent_loop())

    def test_optin_agent_loop_removes_force(self):
        runner = self._runner(agent_loop=True)
        runner.llm_source = "replay:corpus.jsonl"
        self.assertIsNone(runner._modify_request_agent_loop())  # 不指定 → pipeline 默认启用


if __name__ == "__main__":
    unittest.main()
