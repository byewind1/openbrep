from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
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
from openbrep.core import GDLAgent
from openbrep.gdl_contract_checker import GDLContractChecker
from openbrep.hsf_project import HSFProject
from openbrep.static_checker import StaticChecker
from openbrep.llm import LLMAdapter
from openbrep.naming_alignment import align_parameter_names


def _apply_naming_alignment(task, final_project: HSFProject) -> dict:
    """命名规范对齐：以 task yaml 的 required_params 为规范，重命名对齐
    paramlist + 全脚本符号替换（保留名规则见 openbrep/naming_alignment.py）。

    在断言（static/contract/criteria）之前执行，对齐结果写回磁盘并随
    record 返回，供失败归因与后续规范库建设使用。任何异常都不阻断任务。
    """
    required = list(getattr(task.success_criteria, "required_params", None) or [])
    if not required:
        return {}
    try:
        convention = {name: None for name in required}
        result = align_parameter_names(final_project, convention)
        if result.renamed:
            final_project.save_to_disk()
        return {
            "renamed": [f"{r.from_name}→{r.to_name}" for r in result.renamed],
            "reserved_conflicts": [
                f"{c.expected_name}↔{c.reserved_name}({c.severity})"
                for c in result.reserved_conflicts
            ],
            "skipped": list(result.skipped),
            "missing_concepts": list(result.missing_concepts),
        }
    except Exception as exc:  # 对齐层永远不阻断 benchmark
        return {"error": str(exc)}


class BenchmarkRunner:
    def __init__(
        self,
        config_path: str = "config.toml",
        mode: str = "auto",
        temperature: float = 0.0,
        llm_record: str | None = None,
        llm_replay: str | None = None,
        agent_loop: bool = False,
    ):
        if mode not in {"mock", "real", "auto"}:
            raise ValueError("mode must be one of: mock, real, auto")
        self.config = GDLAgentConfig.load(config_path)
        # benchmark 需要确定性判断"改动有没有效果"：默认强制 temperature=0
        # （--temperature 可显式覆盖，供分布测试用），随结果元数据记录可追溯。
        # 注意：实测 deepseek 在 temperature=0 下仍非确定（服务端因素），
        # 真确定性用 --llm-record / --llm-replay 黄金语料。
        self.temperature = temperature
        self.config.llm.temperature = temperature
        # 录制黄金语料时的 thinking 控制由 GDL_BENCH_THINKING 环境变量开关：
        #   disabled —— 默认，录制时强制设置 extra_body 关闭 thinking（不依赖
        #               用户 config，避免不同环境录出的语料不一致，现状行为）；
        #   bare     —— 录制时不设 extra_body（OpenRouter 等不接受该参数的端点）。
        # 取值在构造时校验，非法值报错退出，避免静默用错模式录出不可回放的语料。
        bench_thinking = os.environ.get("GDL_BENCH_THINKING") or "disabled"
        if bench_thinking not in {"disabled", "bare"}:
            raise ValueError(
                f"GDL_BENCH_THINKING 取值非法：{bench_thinking!r}；"
                "合法取值：disabled（默认，强制关闭 thinking）/ bare（不设 extra_body）"
            )
        if llm_record and bench_thinking == "disabled":
            self.config.llm.extra_body = {"thinking": {"type": "disabled"}}
        self.llm = LLMAdapter(self.config.llm)
        self.llm_source = "live"
        if llm_replay:
            from benchmark.llm_replay import ReplayLLM
            self.llm = ReplayLLM(llm_replay)
            self.llm_source = f"replay:{llm_replay}"
        elif llm_record:
            from benchmark.llm_replay import RecordingLLM
            self.llm = RecordingLLM(self.llm, llm_record)
            self.llm_source = f"record:{llm_record}"
        # 黄金语料 record/replay 默认强制 MODIFY 走旧路径（非 agent_loop）：
        # 历史语料只覆盖 generate()，agent loop 的 generate_with_tools 会录出空
        # 语料/回放崩。--agent-loop 开启后解除强制（agent loop 工具调用链现在
        # 可录制/回放），默认关闭，现有语料/baseline 行为零变化。
        self.allow_agent_loop = agent_loop
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
        # pipeline 的 CREATE 编译开关看 config.compiler.path 是否非空；
        # benchmark 无论 mock/real 都注入了 compiler，编译流程必须真正执行。
        # mock 回退时给路径一个哨兵值，否则无 converter 的环境（CI）会
        # SKIPPED_NO_COMPILER → compile_pass 全灭，与本地结果不一致。
        if self.effective_mode == "mock" and not self.config.compiler.path:
            self.config.compiler.path = "/benchmark/mock-converter"
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
                "contract_pass": False,
                "contract_failures": [],
                "criteria_pass": False,
                "criteria_failures": [],
                "attempts": 0,
                "elapsed_sec": 0.0,
                "error_summary": self.compiler_skip_reason,
                "trace": [],
                "environment": self._environment_metadata(),
            }

        # fixture 非空 = MODIFY 类任务：走 TaskPipeline 生产 MODIFY 路径（旧路径）
        if task.fixture:
            return self._run_modify_task(task, start)
        return self._run_create_task(task, start)

    def _make_pipeline(self):
        """构造走生产路径的 TaskPipeline。

        TaskPipeline 内部按 config 自建 LLM/compiler，这里覆盖为 runner 实例，
        保证 mock/real 语义与现有 benchmark 完全一致。抽成独立方法是为了让
        测试可以用假 pipeline 替换（见 tests/test_benchmark_assertions.py）。
        """
        from openbrep.runtime.pipeline import TaskPipeline

        # benchmark 关闭学习记忆注入：prompt 只取决于代码与静态知识，
        # 保证黄金语料可复现（详见 benchmark/llm_replay.py 的设计说明）
        pipeline = TaskPipeline(config=self.config, trace_dir="./traces", include_learned_skills=False)
        pipeline._make_llm = lambda _req: self.llm
        pipeline._make_compiler = lambda: self.compiler
        return pipeline

    def _run_create_task(self, task, start: float) -> dict:
        """CREATE 类任务：走 TaskPipeline 的 CREATE 生产路径。

        与生产一致：知识注入 / linter / StaticChecker / 编译自动修复 / 语义验证
        全部生效（旧 GDLAgent.run 路径无知识注入，benchmark 结果不代表生产质量）。
        """
        from openbrep.runtime.pipeline import TaskRequest

        task_id = task.id
        project = HSFProject.create_new(task_id, work_dir=str(self.work_dir))

        pipeline = self._make_pipeline()

        request = TaskRequest(
            user_input=task.description,
            # IMAGE 任务（task.images 非空）与生产语义一致：意图 IMAGE 走多图通道
            intent="IMAGE" if task.images else "CREATE",
            project=project,
            work_dir=str(self.work_dir),
            output_dir=str(self.results_dir),
            gsm_name=task_id,
        )
        if task.images:
            from openbrep.runtime.pipeline import ImageRef

            request.images = [
                ImageRef(token=f"图{index}", path=str(PROJECT_ROOT / rel))
                for index, rel in enumerate(task.images, start=1)
            ]
        result = pipeline.execute(request)

        elapsed = time.time() - start
        compile_pass = bool(result.compile_result and result.compile_result.success)
        final_project = result.project or project
        naming_alignment = _apply_naming_alignment(task, final_project)
        static_result = StaticChecker().check(final_project)
        contract_result = GDLContractChecker().check(final_project)
        criteria_result = assert_success_criteria(final_project, task.success_criteria)
        success = compile_pass and static_result.passed and contract_result.passed and criteria_result.passed
        compile_stderr = ""
        if result.compile_result is not None and not result.compile_result.success:
            compile_stderr = result.compile_result.stderr or ""

        return {
            "task_id": task_id,
            "success": success,
            "skipped": False,
            "mode": self.mode,
            "effective_mode": self.effective_mode,
            "compile_pass": compile_pass,
            "compile_mode": self.effective_mode,
            "compile_exit_code": getattr(result.compile_result, "exit_code", None),
            "compile_stderr": compile_stderr,
            "static_pass": static_result.passed,
            "contract_pass": contract_result.passed,
            "contract_failures": [
                {
                    "type": issue.check_type,
                    "file": issue.file,
                    "severity": issue.severity,
                    "detail": issue.detail,
                }
                for issue in contract_result.issues
            ],
            "criteria_pass": criteria_result.passed,
            "criteria_failures": criteria_result.failures,
            "naming_alignment": naming_alignment,
            "semantic_repair": result.semantic_repair or {},
            # TaskResult 不暴露内部修复轮次，记 1
            "attempts": 1,
            "elapsed_sec": round(elapsed, 1),
            "error_summary": "" if success else (result.error or compile_stderr),
            "trace": [],
            "environment": self._environment_metadata(),
        }

    def _modify_request_agent_loop(self) -> bool | None:
        """MODIFY 请求的 agent_loop 值：黄金语料默认强制旧路径；--agent-loop 解除。

        返回 None = 不指定（pipeline 按 intent 默认策略启用 agent loop）。
        """
        if self.llm_source.startswith(("replay:", "record:")) and not self.allow_agent_loop:
            return False
        return None

    def _run_modify_task(self, task, start: float) -> dict:
        """MODIFY 类任务：加载 fixture"改动前"工程，走 TaskPipeline 的 MODIFY 生产路径。

        fixture 目录先复制到 benchmark/workdir/<task_id>/ 再加载，避免
        pipeline 编译时的 save_to_disk 污染仓库里签入的 fixture 原件。
        """
        from openbrep.runtime.pipeline import TaskRequest

        task_id = task.id
        fixture_src = PROJECT_ROOT / task.fixture
        work_copy = self.work_dir / task_id
        if work_copy.exists():
            shutil.rmtree(work_copy)
        shutil.copytree(fixture_src, work_copy)
        project = HSFProject.load_from_disk(str(work_copy))

        # 与 CREATE 分支相同：注入 runner 的 llm/compiler，mock/real 语义一致
        pipeline = self._make_pipeline()

        request = TaskRequest(
            user_input=task.description,
            intent="MODIFY",
            project=project,
            work_dir=str(self.work_dir),
            output_dir=str(self.results_dir),
            gsm_name=task_id,
        )
        # 黄金语料默认强制 MODIFY 走旧路径（非 agent_loop）：入库语料在旧路径
        # 下录制，回放必须与录制同路径才能命中（2026-08-06 实测踩坑：路径不一致
        # 会录出空语料/回放 miss）。
        # --agent-loop 开启后解除（generate_with_tools 已可录制/回放，S4 前置）。
        request.agent_loop = self._modify_request_agent_loop()
        result = pipeline.execute(request)

        elapsed = time.time() - start
        compile_pass = bool(result.compile_result and result.compile_result.success)
        final_project = result.project or project
        naming_alignment = _apply_naming_alignment(task, final_project)
        static_result = StaticChecker().check(final_project)
        contract_result = GDLContractChecker().check(final_project)
        criteria_result = assert_success_criteria(final_project, task.success_criteria)
        success = compile_pass and static_result.passed and contract_result.passed and criteria_result.passed
        compile_stderr = ""
        if result.compile_result is not None and not result.compile_result.success:
            compile_stderr = result.compile_result.stderr or ""

        return {
            "task_id": task_id,
            "success": success,
            "skipped": False,
            "mode": self.mode,
            "effective_mode": self.effective_mode,
            "fixture": task.fixture,
            "compile_pass": compile_pass,
            "compile_mode": self.effective_mode,
            "compile_exit_code": getattr(result.compile_result, "exit_code", None),
            "compile_stderr": compile_stderr,
            "static_pass": static_result.passed,
            "contract_pass": contract_result.passed,
            "contract_failures": [
                {
                    "type": issue.check_type,
                    "file": issue.file,
                    "severity": issue.severity,
                    "detail": issue.detail,
                }
                for issue in contract_result.issues
            ],
            "criteria_pass": criteria_result.passed,
            "criteria_failures": criteria_result.failures,
            "naming_alignment": naming_alignment,
            "semantic_repair": result.semantic_repair or {},
            # 旧路径是"一次性生成 + 内部最多 2 轮修复"，TaskResult 不暴露轮次，记 1
            "attempts": 1,
            "elapsed_sec": round(elapsed, 1),
            "error_summary": "" if success else (result.error or compile_stderr),
            "trace": [],
            "environment": self._environment_metadata(),
        }

    def _run_task_guarded(self, task_file: str) -> dict:
        """run_task 的异常兜底：单个任务崩溃只记为失败，不拖垮整批。"""
        try:
            return self.run_task(task_file)
        except Exception as exc:
            try:
                task_id = load_benchmark_task(task_file).id
            except Exception:
                task_id = Path(task_file).stem
            return {
                "task_id": task_id,
                "success": False,
                "skipped": False,
                "mode": self.mode,
                "effective_mode": self.effective_mode,
                "compile_pass": False,
                "compile_mode": self.effective_mode,
                "compile_exit_code": None,
                "compile_stderr": "",
                "static_pass": False,
                "contract_pass": False,
                "contract_failures": [],
                "criteria_pass": False,
                "criteria_failures": [],
                "attempts": 0,
                "elapsed_sec": 0.0,
                "error_summary": f"task crashed: {exc}",
                "trace": [],
                "environment": self._environment_metadata(),
            }

    @staticmethod
    def _print_task_status(result: dict) -> None:
        status = "⏭" if result.get("skipped") else ("✅" if result["success"] else "❌")
        print(f"  {status} {result['task_id']}: {result['elapsed_sec']}s")

    def run_suite(self, suite_dir: str, jobs: int = 4) -> list:
        """跑整个 suite。jobs=1 完全串行（调试/回归兜底）；jobs>1 线程池并行。

        隔离性：每个任务有独立 workdir/<task_id>、独立产物 <task_id>.gsm、
        独立 TaskPipeline（_make_pipeline per task）；共享的 llm/compiler 是
        无状态包装（HTTP 调用 / 子进程），线程安全。瓶颈是等 LLM 响应，
        I/O-bound，线程池不受 GIL 影响。
        """
        task_files = sorted(Path(suite_dir).glob("*.yaml"))
        if jobs <= 1:
            results = []
            for task_file in task_files:
                print(f"Running {task_file.name}...")
                result = self._run_task_guarded(str(task_file))
                results.append(result)
                self._print_task_status(result)
            return results

        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 暖场：首题先在主线程串行跑。知识/检索等子系统的"首次使用"可能写共享
        # 缓存文件，直接并行会在全新环境（如 CI 首跑）竞争，导致首题 prompt
        # 与后续运行不一致（黄金语料未命中）。实测过一次，根因未完全定位，
        # 串行暖场可确定性规避。
        print(f"Running {task_files[0].name}... (warm-up)")
        warmup_result = self._run_task_guarded(str(task_files[0]))
        self._print_task_status(warmup_result)

        print(f"Running {len(task_files) - 1} tasks with {jobs} workers...")
        records: dict[str, dict] = {warmup_result["task_id"]: warmup_result}
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(self._run_task_guarded, str(task_file)): task_file
                for task_file in task_files[1:]
            }
            for future in as_completed(futures):
                result = future.result()
                records[result["task_id"]] = result
                self._print_task_status(result)

        # 输出顺序按任务文件名排序，保证报告可 diff
        return [records[load_benchmark_task(str(f)).id] for f in task_files]

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
                if r.get("contract_failures"):
                    lines.append(f"   contract: {r['contract_failures']}")
        return "\n".join(lines)

    def _environment_metadata(self) -> dict:
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "converter_path": getattr(self.compiler, "converter_path", None),
            "temperature": getattr(self, "temperature", None),
        }

    def write_results(self, results: list, *, suite_name: str = "create") -> dict[str, str]:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        summary = build_summary(results, self._run_metadata(suite_name))
        date_path = self.results_dir / f"{datetime.date.today()}_{suite_name}.json"
        history_path = self.results_dir / f"{suite_name}.jsonl"
        summary_path = self.results_dir / f"{suite_name}_summary.md"
        date_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        with history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
        summary_path.write_text(render_markdown_summary(summary, results), encoding="utf-8")
        return {
            "results_json": str(date_path),
            "history_jsonl": str(history_path),
            "summary_md": str(summary_path),
        }

    def _run_metadata(self, suite_name: str) -> dict:
        return {
            "suite": suite_name,
            "commit": _git_commit(),
            "mode": self.mode,
            "effective_mode": self.effective_mode,
            "model": getattr(self.config.llm, "model", ""),
            "temperature": getattr(self.config.llm, "temperature", None),
            "llm_source": getattr(self, "llm_source", "live"),
            "max_iterations": getattr(self.config.agent, "max_iterations", None),
            "environment": self._environment_metadata(),
        }


def build_summary(results: list, metadata: dict) -> dict:
    total = len(results)
    success = sum(1 for item in results if item.get("success"))
    skipped = sum(1 for item in results if item.get("skipped"))
    elapsed_sec = round(sum(float(item.get("elapsed_sec") or 0) for item in results), 1)
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **metadata,
        "total": total,
        "success": success,
        "failed": total - success - skipped,
        "skipped": skipped,
        "pass_rate": round(success / total, 4) if total else 0.0,
        "elapsed_sec": elapsed_sec,
        "tasks": [
            {
                "task_id": item.get("task_id"),
                "success": item.get("success"),
                "skipped": item.get("skipped", False),
                "compile_pass": item.get("compile_pass"),
                "static_pass": item.get("static_pass"),
                "contract_pass": item.get("contract_pass"),
                "criteria_pass": item.get("criteria_pass"),
                "attempts": item.get("attempts"),
                "elapsed_sec": item.get("elapsed_sec"),
            }
            for item in results
        ],
    }


def render_markdown_summary(summary: dict, results: list) -> str:
    lines = [
        "# OpenBrep Benchmark Summary",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- commit: {summary.get('commit') or 'unknown'}",
        f"- suite: {summary.get('suite')}",
        f"- mode: {summary.get('mode')} -> {summary.get('effective_mode')}",
        f"- model: {summary.get('model') or 'unknown'}",
        f"- result: {summary['success']}/{summary['total']} passed, {summary['skipped']} skipped",
        "",
        "| Task | Success | Compile | Static | Contract | Criteria | Attempts | Seconds |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for item in results:
        lines.append(
            "| {task} | {success} | {compile} | {static} | {contract} | {criteria} | {attempts} | {seconds} |".format(
                task=item.get("task_id"),
                success=_mark(bool(item.get("success"))),
                compile=_mark(bool(item.get("compile_pass"))),
                static=_mark(bool(item.get("static_pass"))),
                contract=_mark(bool(item.get("contract_pass"))),
                criteria=_mark(bool(item.get("criteria_pass"))),
                attempts=item.get("attempts", 0),
                seconds=item.get("elapsed_sec", 0),
            )
        )
    return "\n".join(lines) + "\n"


def _mark(value: bool) -> str:
    return "pass" if value else "fail"


def _git_commit() -> str:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run OpenBrep benchmark tasks")
    parser.add_argument("--suite", default="benchmark/tasks/create/", help="benchmark task directory")
    parser.add_argument("--mode", default="auto", choices=["mock", "real", "auto"], help="compiler mode")
    parser.add_argument("--config", default="config.toml", help="OpenBrep config path")
    parser.add_argument("--jobs", type=int, default=4, help="并发任务数；1 = 完全串行")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM 温度；默认 0 保证确定性，分布测试时显式覆盖")
    parser.add_argument("--llm-record", default=None, metavar="CORPUS.jsonl", help="真实 LLM 跑并把响应录制成黄金语料")
    parser.add_argument("--llm-replay", default=None, metavar="CORPUS.jsonl", help="离线回放黄金语料（完全确定，零 token）")
    parser.add_argument("--agent-loop", action="store_true", default=False,
                        help="黄金语料 record/replay 不再强制 MODIFY 走旧路径（agent_loop 工具链可录制/回放）；默认关，旧语料/baseline 零变化")
    args = parser.parse_args()

    runner = BenchmarkRunner(
        config_path=args.config, mode=args.mode,
        temperature=args.temperature,
        llm_record=args.llm_record, llm_replay=args.llm_replay,
        agent_loop=args.agent_loop,
    )
    results = runner.run_suite(args.suite, jobs=args.jobs)
    print(runner.report(results))

    paths = runner.write_results(results, suite_name=Path(args.suite).name or "create")
    print(f"\nTrace saved to {paths['results_json']}")
    print(f"History appended to {paths['history_jsonl']}")
    print(f"Summary saved to {paths['summary_md']}")
