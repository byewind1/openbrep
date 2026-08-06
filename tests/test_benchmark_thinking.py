"""benchmark runner 录制时 thinking 控制（GDL_BENCH_THINKING）的合同测试。

- 默认（不设置环境变量）：录制时 config.llm.extra_body 被强制设为关闭 thinking
- bare：录制时不设 extra_body（OpenRouter 等不需要该参数的端点）
- 非法取值：构造即报错退出，并提示合法取值
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from benchmark.runner import BenchmarkRunner

# 同 test_benchmark_create.py：LLMAdapter._setup() 会写进程级环境变量，逐测试还原
_ENV_NAMES = (
    "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
)


class TestBenchmarkThinkingControl(unittest.TestCase):
    """GDL_BENCH_THINKING 环境开关在录制（--llm-record）时的 extra_body 行为。"""

    def setUp(self):
        self._env_snapshot = {name: os.environ.get(name) for name in _ENV_NAMES}
        self._thinking_snapshot = os.environ.get("GDL_BENCH_THINKING")

    def tearDown(self):
        for name, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self._thinking_snapshot is None:
            os.environ.pop("GDL_BENCH_THINKING", None)
        else:
            os.environ["GDL_BENCH_THINKING"] = self._thinking_snapshot

    def _make_runner(self, tmp_path: Path) -> BenchmarkRunner:
        return BenchmarkRunner(
            config_path=str(tmp_path / "config.toml"),
            mode="mock",
            llm_record=str(tmp_path / "corpus.jsonl"),
        )

    def test_default_forces_thinking_disabled(self):
        os.environ.pop("GDL_BENCH_THINKING", None)
        with tempfile.TemporaryDirectory() as td:
            runner = self._make_runner(Path(td))
            self.assertEqual(runner.config.llm.extra_body, {"thinking": {"type": "disabled"}})

    def test_bare_leaves_extra_body_unset(self):
        os.environ["GDL_BENCH_THINKING"] = "bare"
        with tempfile.TemporaryDirectory() as td:
            runner = self._make_runner(Path(td))
            self.assertEqual(runner.config.llm.extra_body, {})

    def test_invalid_value_raises_and_hints_valid_values(self):
        os.environ["GDL_BENCH_THINKING"] = "nope"
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                self._make_runner(Path(td))
            self.assertIn("disabled", str(ctx.exception))
            self.assertIn("bare", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
