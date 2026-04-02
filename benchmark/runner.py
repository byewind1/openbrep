from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

import yaml

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent, Status
from openbrep.hsf_project import HSFProject
from openbrep.llm import LLMAdapter


class BenchmarkRunner:
    def __init__(self, config_path: str = "config.toml"):
        self.config = GDLAgentConfig.load(config_path)
        self.llm = LLMAdapter(self.config.llm)
        compiler_path = self.config.compiler.path
        if compiler_path:
            self.compiler = HSFCompiler(converter_path=compiler_path, timeout=self.config.compiler.timeout)
        else:
            self.compiler = MockHSFCompiler()
        self.agent = GDLAgent(
            self.llm,
            compiler=self.compiler,
            max_iterations=self.config.agent.max_iterations,
            assistant_settings=self.config.llm.assistant_settings,
        )
        self.results_dir = Path("benchmark/results")
        self.work_dir = Path("benchmark/workdir")

    def run_task(self, task_path: str) -> dict:
        task = yaml.safe_load(Path(task_path).read_text(encoding="utf-8"))
        start = time.time()

        task_id = task["id"]
        project = HSFProject.create_new(task_id, work_dir=str(self.work_dir))
        output_gsm = str(self.results_dir / f"{task_id}.gsm")
        result = self.agent.run(
            instruction=task["description"],
            project=project,
            output_gsm=output_gsm,
        )

        elapsed = time.time() - start
        compile_pass = result.status == Status.SUCCESS

        return {
            "task_id": task_id,
            "success": compile_pass,
            "compile_pass": compile_pass,
            "attempts": result.attempts,
            "elapsed_sec": round(elapsed, 1),
            "error_summary": result.error_summary,
            "trace": result.history,
        }

    def run_suite(self, suite_dir: str) -> list:
        results = []
        for task_file in sorted(Path(suite_dir).glob("*.yaml")):
            print(f"Running {task_file.name}...")
            result = self.run_task(str(task_file))
            results.append(result)
            status = "✅" if result["compile_pass"] else "❌"
            print(f"  {status} {result['task_id']}: {result['elapsed_sec']}s")
        return results

    def report(self, results: list) -> str:
        passed = sum(1 for r in results if r["compile_pass"])
        total = len(results)
        lines = [f"Results: {passed}/{total} passed\n"]
        for r in results:
            status = "✅" if r["compile_pass"] else "❌"
            lines.append(f"{status} {r['task_id']} | {r['elapsed_sec']}s | attempts={r['attempts']}")
            if not r["compile_pass"]:
                lines.append(f"   error: {r['error_summary']}")
        return "\n".join(lines)


if __name__ == "__main__":
    runner = BenchmarkRunner()
    results = runner.run_suite("benchmark/tasks/create/")
    print(runner.report(results))

    runner.results_dir.mkdir(exist_ok=True)
    out = runner.results_dir / f"{datetime.date.today()}_create.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nTrace saved to {out}")
