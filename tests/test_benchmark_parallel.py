"""benchmark run_suite 并发的合同测试。

离线：runner._make_pipeline 替换为假 pipeline，不碰 LLM/编译器。
验证：
- --jobs 1 完全串行，结果按任务文件名排序
- 并行（jobs=4）与串行结果逐任务一致
- 单任务异常只记为失败，不拖垮整批
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmark.runner import BenchmarkRunner
from openbrep.compiler import CompileResult
from openbrep.hsf_project import ScriptType
from openbrep.runtime.pipeline import TaskResult


def _write_suite(tmp_path: Path, ids=("T01", "T02", "T03", "T04")) -> Path:
    suite = tmp_path / "tasks"
    suite.mkdir()
    for tid in ids:
        (suite / f"{tid}.yaml").write_text(
            f"---\nid: {tid}\ncategory: create\ndescription: task {tid}\n",
            encoding="utf-8",
        )
    return suite


class _OkPipeline:
    def execute(self, request):
        # 布置一个能通过 static/contract 的最小工程（空工程会触发 contract 失败）
        request.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
        request.project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")
        return TaskResult(
            success=True,
            intent="CREATE",
            project=request.project,
            compile_result=CompileResult(
                success=True, stdout="", stderr="", mode="mock",
                output_path="", exit_code=0,
            ),
        )


class _BoomPipeline:
    """T02 任务直接抛异常。"""

    def execute(self, request):
        if request.project.name == "T02":
            raise RuntimeError("boom")
        return _OkPipeline().execute(request)


def _make_runner(tmp_path: Path, pipeline) -> BenchmarkRunner:
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.results_dir = tmp_path / "results"
    runner.work_dir = tmp_path / "workdir"
    runner.mode = "mock"
    runner.effective_mode = "mock"
    runner.compiler_skip_reason = ""
    runner.compiler = None
    runner._make_pipeline = lambda: pipeline
    return runner


class TestRunSuiteParallel(unittest.TestCase):
    def test_jobs_1_serial_and_ordered(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            suite = _write_suite(tmp_path)
            runner = _make_runner(tmp_path, _OkPipeline())

            results = runner.run_suite(str(suite), jobs=1)

            self.assertEqual([r["task_id"] for r in results], ["T01", "T02", "T03", "T04"])
            self.assertTrue(all(r["success"] for r in results))

    def test_parallel_matches_serial_per_task(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            suite = _write_suite(tmp_path)

            serial = _make_runner(tmp_path, _OkPipeline()).run_suite(str(suite), jobs=1)
            parallel = _make_runner(tmp_path, _OkPipeline()).run_suite(str(suite), jobs=4)

            self.assertEqual(
                {r["task_id"]: r["success"] for r in serial},
                {r["task_id"]: r["success"] for r in parallel},
            )
            self.assertEqual([r["task_id"] for r in parallel], ["T01", "T02", "T03", "T04"])

    def test_single_task_crash_does_not_kill_batch(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            suite = _write_suite(tmp_path)

            for jobs in (1, 4):
                results = _make_runner(tmp_path, _BoomPipeline()).run_suite(str(suite), jobs=jobs)
                by_id = {r["task_id"]: r for r in results}
                self.assertFalse(by_id["T02"]["success"], f"jobs={jobs}")
                self.assertIn("boom", by_id["T02"]["error_summary"])
                for tid in ("T01", "T03", "T04"):
                    self.assertTrue(by_id[tid]["success"], f"jobs={jobs} {tid}")
                self.assertEqual([r["task_id"] for r in results], ["T01", "T02", "T03", "T04"])

    def test_default_jobs_uses_parallel_and_matches(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            suite = _write_suite(tmp_path)
            results = _make_runner(tmp_path, _OkPipeline()).run_suite(str(suite))
            self.assertEqual([r["task_id"] for r in results], ["T01", "T02", "T03", "T04"])
            self.assertTrue(all(r["success"] for r in results))


if __name__ == "__main__":
    unittest.main()
