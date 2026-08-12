"""benchmark CREATE harness 的合同测试：CREATE 走 TaskPipeline 生产路径。

离线运行：runner 的 LLM 替换为 MockLLM，编译器为 MockHSFCompiler。
CREATE 不再经过 GDLAgent.run 遗留路径（无知识注入，结果不代表生产质量）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmark.runner import BenchmarkRunner
from openbrep.llm import MockLLM

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREATE_SUITE = PROJECT_ROOT / "benchmark" / "tasks" / "create"

_GDL_REPLY = (
    "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
    "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
)


class TestCreateRunnerBranch(unittest.TestCase):
    """run_task 遇到 CREATE 任务（无 fixture）时走 TaskPipeline CREATE 生产路径。"""

    # 同 test_benchmark_modify.py：LLMAdapter._setup() 会写进程级环境变量，逐测试还原
    _ENV_NAMES = (
        "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    )

    def setUp(self):
        import os
        self._env_snapshot = {name: os.environ.get(name) for name in self._ENV_NAMES}

    def tearDown(self):
        import os
        for name, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _make_runner(self, tmp_path) -> BenchmarkRunner:
        runner = BenchmarkRunner(config_path=str(tmp_path / "config.toml"), mode="mock")
        # 让 pipeline 的 CREATE 编译段真正执行（由注入的 MockHSFCompiler 通过），
        # 不依赖 config.example.toml 的默认 compiler.path
        runner.config.compiler.path = "/fake/LP_XMLConverter"
        runner.llm = MockLLM(responses=[_GDL_REPLY])
        runner.results_dir = tmp_path / "results"
        runner.work_dir = tmp_path / "workdir"
        return runner

    def test_run_create_task_produces_record_via_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            runner = self._make_runner(tmp_path)

            record = runner.run_task(str(CREATE_SUITE / "C01_simple_box.yaml"))

            self.assertEqual(record["task_id"], "C01")
            self.assertFalse(record["skipped"])
            self.assertTrue(record["compile_pass"], record.get("compile_stderr"))
            for key in ["static_pass", "contract_pass", "criteria_pass", "attempts", "elapsed_sec", "environment"]:
                self.assertIn(key, record)

            # 生成结果经 pipeline apply 落盘到独立 workdir
            script_3d = tmp_path / "workdir" / "C01" / "scripts" / "3d.gdl"
            self.assertTrue(script_3d.exists())
            self.assertIn("BLOCK", script_3d.read_text(encoding="utf-8-sig"))

            # 指令经过了生产路径的 LLM 调用（而非遗留 agent.run 的无知识注入路径）
            self.assertTrue(runner.llm.call_history, "CREATE 未发生 LLM 调用")
            self.assertTrue(
                any("长方体柜子" in str(messages) for messages in runner.llm.call_history),
                "任务指令未进入 LLM 请求",
            )

    def test_create_task_does_not_touch_legacy_agent_run(self):
        """CREATE 分支不再调用 GDLAgent.run（遗留路径）。"""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            runner = self._make_runner(tmp_path)

            def _forbidden(*_args, **_kwargs):
                raise AssertionError("CREATE 走到了 GDLAgent.run 遗留路径")

            runner.agent.run = _forbidden
            record = runner.run_task(str(CREATE_SUITE / "C01_simple_box.yaml"))
            self.assertEqual(record["task_id"], "C01")


    def test_vision_task_passes_image_through_pipeline(self):
        """P5b vision 套件：IMAGE 任务经 runner → TaskRequest.images → harness 提取。"""
        import json

        vision_suite = PROJECT_ROOT / "benchmark" / "tasks" / "vision"
        fixture = PROJECT_ROOT / "benchmark" / "fixtures" / "vision" / "begonia_lattice.jpg"
        self.assertTrue(fixture.exists(), "vision fixture 必须入库")

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            runner = self._make_runner(tmp_path)
            # 响应序列：analyze(1) → object_plan(2) → generate_only(3)
            runner.llm = MockLLM(responses=[
                json.dumps({
                    "component_type": "海棠纹漏窗", "main_form": "lattice_grid",
                    "layers": [], "symmetry": ["x", "y"], "key_features": ["海棠瓣"],
                    "dimension_hints": {}, "parametrize": ["A", "ZZYZX"],
                    "fix_as_ratio": [], "raw_description": "井字格底四瓣海棠",
                }, ensure_ascii=False),
                '{"object_type": "lattice_window", "params": [], "scripts": {}, "knowledge_sources": []}',
                _GDL_REPLY,
            ])

            record = runner.run_task(str(vision_suite / "V01_begonia_lattice.yaml"))

            self.assertEqual(record["task_id"], "V01")
            # 请求携带图片（intent=IMAGE 走多图通道，fixture 被读取进 request.images）
            self.assertTrue(
                any("海棠" in str(messages) for messages in runner.llm.call_history),
                "vision 提取调用未携带图/描述",
            )
            self.assertFalse(record["skipped"])

    def test_vision_task_yaml_images_parsed(self):
        """任务 YAML 的 images 字段被解析进 BenchmarkTask。"""
        from benchmark.schema import load_benchmark_task

        task = load_benchmark_task(
            PROJECT_ROOT / "benchmark" / "tasks" / "vision" / "V02_ice_crack_lattice.yaml"
        )
        self.assertEqual(task.images, ["benchmark/fixtures/vision/ice_crack_lattice.jpg"])


if __name__ == "__main__":
    unittest.main()
