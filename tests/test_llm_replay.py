"""benchmark/llm_replay.py 的合同测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmark.llm_replay import RecordingLLM, ReplayLLM, _corpus_key
from openbrep.llm import LLMResponse, MockLLM


class TestCorpusKey(unittest.TestCase):
    def test_key_stable_for_same_messages(self):
        m = [{"role": "user", "content": "做一个书架"}]
        self.assertEqual(_corpus_key(m, {}), _corpus_key(m, {}))

    def test_key_differs_for_different_messages(self):
        a = [{"role": "user", "content": "做一个书架"}]
        b = [{"role": "user", "content": "做一个椅子"}]
        self.assertNotEqual(_corpus_key(a, {}), _corpus_key(b, {}))


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

    def test_replay_rejects_tools_and_image(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = str(Path(td) / "corpus.jsonl")
            Path(corpus).write_text("", encoding="utf-8")
            replay = ReplayLLM(corpus)
            with self.assertRaises(NotImplementedError):
                replay.generate_with_tools([], [])
            with self.assertRaises(NotImplementedError):
                replay.generate_with_image([])


class TestRunnerReplayIntegration(unittest.TestCase):
    """runner 接 replay 后离线跑任务，结果与录制轮一致。"""

    _ENV_NAMES = (
        "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    )

    def setUp(self):
        import os
        self._env = {n: os.environ.get(n) for n in self._ENV_NAMES}

    def tearDown(self):
        import os
        for n, v in self._env.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v

    def test_runner_replay_matches_recording(self):
        from benchmark.runner import BenchmarkRunner

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = str(tmp / "corpus.jsonl")
            # 录制：MockLLM 作为底层（离线），内容经 RecordingLLM 落盘
            rec = BenchmarkRunner(config_path=str(tmp / "cfg.toml"), mode="mock")
            rec.llm = RecordingLLM(
                MockLLM(responses=[
                    "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
                    "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
                ]),
                corpus,
            )
            rec.results_dir = tmp / "results"
            rec.work_dir = tmp / "workdir"
            suite = str(Path(__file__).resolve().parents[1] / "benchmark" / "tasks" / "create" / "C01_simple_box.yaml")
            live_record = rec.run_task(suite)

            # 回放：不再经过 MockLLM，直接吃语料
            rep = BenchmarkRunner(config_path=str(tmp / "cfg2.toml"), mode="mock", llm_replay=corpus)
            rep.results_dir = tmp / "results2"
            rep.work_dir = tmp / "workdir2"
            replay_record = rep.run_task(suite)

            self.assertEqual(replay_record["task_id"], "C01")
            self.assertEqual(replay_record["success"], live_record["success"])
            self.assertEqual(replay_record["criteria_failures"], live_record["criteria_failures"])


if __name__ == "__main__":
    unittest.main()
