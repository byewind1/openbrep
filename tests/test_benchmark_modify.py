"""benchmark MODIFY harness 的合同测试：fixture 解析与 runner MODIFY 分支。

离线运行：runner 的 LLM 替换为 MockLLM，编译器为 MockHSFCompiler。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from benchmark.runner import BenchmarkRunner
from benchmark.schema import benchmark_task_from_dict, load_benchmark_task
from openbrep.llm import MockLLM

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODIFY_SUITE = PROJECT_ROOT / "benchmark" / "tasks" / "modify"
M01_FIXTURE = PROJECT_ROOT / "benchmark" / "fixtures" / "modify" / "M01"


class TestModifySchema(unittest.TestCase):
    def test_fixture_field_parsed(self):
        task = benchmark_task_from_dict({
            "id": "MX",
            "description": "x",
            "fixture": "benchmark/fixtures/modify/MX",
        })
        self.assertEqual(task.fixture, "benchmark/fixtures/modify/MX")

    def test_fixture_defaults_empty_for_create_tasks(self):
        task = benchmark_task_from_dict({"id": "CX"})
        self.assertEqual(task.fixture, "")

    def test_modify_suite_yaml_all_carry_existing_fixture(self):
        yaml_files = sorted(MODIFY_SUITE.glob("*.yaml"))
        self.assertGreaterEqual(len(yaml_files), 4)
        for path in yaml_files:
            task = load_benchmark_task(path)
            self.assertTrue(task.fixture, f"{path.name} 缺 fixture 字段")
            self.assertTrue(
                (PROJECT_ROOT / task.fixture / "paramlist.xml").exists(),
                f"{path.name} 的 fixture 目录不存在或缺 paramlist.xml：{task.fixture}",
            )


class TestModifyRunnerBranch(unittest.TestCase):
    """run_task 遇到 fixture 任务时走 TaskPipeline MODIFY 路径。"""

    # LLMAdapter._setup() 会把 key 写进进程级环境变量（ZAI/DEEPSEEK/ANTHROPIC/
    # GEMINI_API_KEY），必须逐测试还原，否则污染依赖"无 key"前提的其他测试
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
        # 离线：替换真实 LLMAdapter 为 MockLLM（_run_modify_task 会注入 pipeline）
        runner.llm = MockLLM(responses=[
            "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n",
        ])
        return runner

    def test_run_modify_task_produces_record_and_keeps_fixture_intact(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            fixture_before = (M01_FIXTURE / "scripts" / "3d.gdl").read_text(encoding="utf-8-sig")
            runner = self._make_runner(tmp_path)
            # 结果与工作目录收进 tmp，避免污染仓库 benchmark/workdir
            runner.results_dir = tmp_path / "results"
            runner.work_dir = tmp_path / "workdir"

            record = runner.run_task(str(MODIFY_SUITE / "M01_add_shelf_layer.yaml"))

            self.assertEqual(record["task_id"], "M01")
            self.assertFalse(record["skipped"])
            self.assertTrue(record["compile_pass"], record.get("compile_stderr"))
            self.assertEqual(record["fixture"], "benchmark/fixtures/modify/M01")
            for key in ["static_pass", "contract_pass", "criteria_pass", "attempts", "elapsed_sec", "environment"]:
                self.assertIn(key, record)

            # 签入的 fixture 原件不被 pipeline 的 save_to_disk 污染
            fixture_after = (M01_FIXTURE / "scripts" / "3d.gdl").read_text(encoding="utf-8-sig")
            self.assertEqual(fixture_before, fixture_after)

    def test_broken_fixture_reports_compile_failure_without_repair(self):
        """M02 fixture 缺 ENDIF：MockLLM 不做修复时，记录应如实显示编译失败。"""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            runner = BenchmarkRunner(config_path=str(tmp_path / "config.toml"), mode="mock")
            runner.llm = MockLLM(responses=["我看不出问题。"])  # 不输出 [FILE:]，不修复
            runner.results_dir = tmp_path / "results"
            runner.work_dir = tmp_path / "workdir"

            record = runner.run_task(str(MODIFY_SUITE / "M02_fix_missing_endif.yaml"))

            self.assertFalse(record["compile_pass"])
            self.assertIn("IF/ENDIF", record["compile_stderr"])
            self.assertFalse(record["success"])


if __name__ == "__main__":
    unittest.main()
