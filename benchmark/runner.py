from __future__ import annotations

import argparse
import datetime
import json
import platform
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.assertions import assert_success_criteria
from benchmark.schema import load_benchmark_task
from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent, Status
from openbrep.hsf_project import HSFProject
from openbrep.static_checker import StaticChecker
from openbrep.llm import LLMAdapter


class BenchmarkRunner:
    def __init__(self, config_path: str = "config.toml", mode: str = "auto"):
        if mode not in {"mock", "real", "auto"}:
            raise ValueError("mode must be one of: mock, real, auto")
        self.config = GDLAgentConfig.load(config_path)
        self.llm = LLMAdapter(self.config.llm)
        self.mode = mode
        self.compiler_skip_reason = ""
        if mode == "mock":
            self.compiler = MockHSFCompiler()
        else:
            real_compiler = HSFCompiler(
                converter_path=self.config.compiler.path or None,
                timeout=self.config.compiler.timeout,
            )
            if real_compiler.is_available:
                self.compiler = real_compiler
            elif mode == "real":
                self.compiler = real_compiler
                self.compiler_skip_reason = (
                    "LP_XMLConverter not found. Install Archicad or set CONVERTER_PATH/config compiler.path."
                )
            else:
                self.compiler = MockHSFCompiler()
        self.effective_mode = "real" if isinstance(self.compiler, HSFCompiler) else "mock"
        self.agent = GDLAgent(
            self.llm,
            compiler=self.compiler,
            max_iterations=self.config.agent.max_iterations,
            assistant_settings=self.config.llm.assistant_settings,
        )
        self.results_dir = Path("benchmark/results")
        self.work_dir = Path("benchmark/workdir")

    def run_task(self, task_path: str) -> dict:
        task = load_benchmark_task(task_path)
        start = time.time()

        task_id = task.id
        if self.compiler_skip_reason:
            return {
                "task_id": task_id,
                "success": False,
                "skipped": True,
                "skip_reason": self.compiler_skip_reason,
                "mode": self.mode,
                "effective_mode": self.effective_mode,
                "compile_pass": False,
                "compile_mode": self.effective_mode,
                "compile_exit_code": None,
                "compile_stderr": self.compiler_skip_reason,
                "static_pass": False,
                "criteria_pass": False,
                "criteria_failures": [],
                "attempts": 0,
                "elapsed_sec": 0.0,
                "error_summary": self.compiler_skip_reason,
                "trace": [],
                "environment": self._environment_metadata(),
            }

        project = HSFProject.create_new(task_id, work_dir=str(self.work_dir))
        output_gsm = str(self.results_dir / f"{task_id}.gsm")
        result = self.agent.run(
            instruction=task.description,
            project=project,
            output_gsm=output_gsm,
        )

        elapsed = time.time() - start
        compile_pass = result.status == Status.SUCCESS
        final_project = result.project or project
        static_result = StaticChecker().check(final_project)
        criteria_result = assert_success_criteria(final_project, task.success_criteria)
        success = compile_pass and static_result.passed and criteria_result.passed

        return {
            "task_id": task_id,
            "success": success,
            "skipped": False,
            "mode": self.mode,
            "effective_mode": self.effective_mode,
            "compile_pass": compile_pass,
            "compile_mode": self.effective_mode,
            "compile_exit_code": None,
            "compile_stderr": "" if compile_pass else result.error_summary,
            "static_pass": static_result.passed,
            "criteria_pass": criteria_result.passed,
            "criteria_failures": criteria_result.failures,
            "attempts": result.attempts,
            "elapsed_sec": round(elapsed, 1),
            "error_summary": result.error_summary,
            "trace": result.history,
            "environment": self._environment_metadata(),
        }

    def run_suite(self, suite_dir: str) -> list:
        results = []
        for task_file in sorted(Path(suite_dir).glob("*.yaml")):
            print(f"Running {task_file.name}...")
            result = self.run_task(str(task_file))
            results.append(result)
            status = "⏭" if result.get("skipped") else ("✅" if result["success"] else "❌")
            print(f"  {status} {result['task_id']}: {result['elapsed_sec']}s")
        return results

    def report(self, results: list) -> str:
        passed = sum(1 for r in results if r["success"])
        total = len(results)
        lines = [f"Results: {passed}/{total} passed\n"]
        for r in results:
            status = "⏭" if r.get("skipped") else ("✅" if r["success"] else "❌")
            lines.append(f"{status} {r['task_id']} | {r['elapsed_sec']}s | attempts={r['attempts']}")
            if r.get("skipped"):
                lines.append(f"   skipped: {r.get('skip_reason', '')}")
                continue
            if not r["success"]:
                lines.append(f"   error: {r['error_summary']}")
                if r.get("criteria_failures"):
                    lines.append(f"   criteria: {r['criteria_failures']}")
        return "\n".join(lines)

    def _environment_metadata(self) -> dict:
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "converter_path": getattr(self.compiler, "converter_path", None),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run OpenBrep benchmark tasks")
    parser.add_argument("--suite", default="benchmark/tasks/create/", help="benchmark task directory")
    parser.add_argument("--mode", default="auto", choices=["mock", "real", "auto"], help="compiler mode")
    parser.add_argument("--config", default="config.toml", help="OpenBrep config path")
    args = parser.parse_args()

    runner = BenchmarkRunner(config_path=args.config, mode=args.mode)
    results = runner.run_suite(args.suite)
    print(runner.report(results))

    runner.results_dir.mkdir(exist_ok=True)
    out = runner.results_dir / f"{datetime.date.today()}_create.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nTrace saved to {out}")
