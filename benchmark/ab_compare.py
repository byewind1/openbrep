"""MODIFY 基准任务的新旧路径 A/B 对照脚本。

对同一批 `benchmark/tasks/modify/*.yaml`（fixture 任务）分别跑：
- legacy    ：TaskPipeline 旧路径（`_handle_script_update`，硬编码最多两轮修复）
- agent_loop：TaskPipeline 实验新路径（预算制 agent loop，`TaskRequest.agent_loop=True`）

对比指标：编译通过率、整体成功率（compile+static+contract+criteria）、
几何断言通过率（semantic_verification / param_responsive 单独拆分）、
平均 LLM 调用次数、平均耗时。

用法（仓库根目录）：
    # 离线自测（MockLLM + MockHSFCompiler，零 API 消耗，结果仅验证流程）
    python benchmark/ab_compare.py --suite benchmark/tasks/modify/ --mode mock --llm mock
    # 真实 A/B（消耗 API 额度，由用户决定预算后手动跑）
    python benchmark/ab_compare.py --suite benchmark/tasks/modify/ --mode mock --llm real
    python benchmark/ab_compare.py --suite benchmark/tasks/modify/ --mode real --llm real

输出（benchmark/results/ 下，被 .gitignore 忽略，仅本地产出）：
    <date>_modify_ab.json   全量对比记录
    modify_ab_summary.md    人类可读对比报告
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.assertions import (
    GEOMETRIC_ASSERTION_TYPES,
    assert_success_criteria,
    evaluate_semantic_assertion,
)
from benchmark.runner import _git_commit
from benchmark.schema import load_benchmark_task
from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.gdl_contract_checker import GDLContractChecker
from openbrep.hsf_project import HSFProject
from openbrep.static_checker import StaticChecker

PATH_LABELS = ("legacy", "agent_loop")


class CountingLLM:
    """LLM 调用计数代理：包装真实 LLMAdapter 或 MockLLM，统计 generate* 调用数。"""

    def __init__(self, base):
        self._base = base
        self.call_count = 0

    def generate(self, messages, **kwargs):
        self.call_count += 1
        return self._base.generate(messages, **kwargs)

    def generate_with_tools(self, messages, tools, **kwargs):
        self.call_count += 1
        return self._base.generate_with_tools(messages, tools, **kwargs)

    def generate_with_image(self, *args, **kwargs):
        self.call_count += 1
        return self._base.generate_with_image(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._base, name)


class ModifyABRunner:
    def __init__(self, config_path: str, mode: str, llm_mode: str, budget: int):
        if mode not in {"mock", "real", "auto"}:
            raise ValueError("mode must be one of: mock, real, auto")
        self.config = GDLAgentConfig.load(config_path)
        self.mode = mode
        self.budget = budget
        # 编译器语义与 benchmark/runner.py 一致：mock 强制 MockHSFCompiler，
        # real/auto 优先 LP_XMLConverter，auto 缺失时回落 mock
        if mode == "mock":
            self.compiler = MockHSFCompiler()
        else:
            real_compiler = HSFCompiler(
                converter_path=self.config.compiler.path or None,
                timeout=self.config.compiler.timeout,
            )
            self.compiler = real_compiler if real_compiler.is_available else MockHSFCompiler()
        self.effective_mode = "real" if isinstance(self.compiler, HSFCompiler) else "mock"

        if llm_mode == "real":
            from openbrep.llm import LLMAdapter
            self._llm_factory = lambda: LLMAdapter(self.config.llm)
            self.llm_label = getattr(self.config.llm, "model", "real")
        else:
            from openbrep.llm import MockLLM
            self._llm_factory = lambda: MockLLM()
            self.llm_label = "MockLLM(offline)"

        self.results_dir = Path("benchmark/results")
        self.work_dir = Path("benchmark/workdir")

    # ── 单任务单路径 ──────────────────────────────────────

    def _run_one(self, task, path_label: str, task_file: str) -> dict:
        from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

        # 每个路径独立的 fixture 工作副本，互不影响，也不污染签入原件
        work_copy = self.work_dir / f"{task.id}__{path_label}"
        if work_copy.exists():
            shutil.rmtree(work_copy)
        shutil.copytree(PROJECT_ROOT / task.fixture, work_copy)
        project = HSFProject.load_from_disk(str(work_copy))

        llm = CountingLLM(self._llm_factory())
        pipeline = TaskPipeline(config=self.config, trace_dir="./traces")
        pipeline._make_llm = lambda _req: llm
        pipeline._make_compiler = lambda: self.compiler

        request = TaskRequest(
            user_input=task.description,
            intent="MODIFY",
            project=project,
            work_dir=str(self.work_dir),
            output_dir=str(self.results_dir),
            gsm_name=f"{task.id}_{path_label}",
            agent_loop=(path_label == "agent_loop"),
            agent_loop_budget=self.budget,
        )
        start = time.time()
        result = pipeline.execute(request)
        elapsed = time.time() - start

        compile_pass = bool(result.compile_result and result.compile_result.success)
        final_project = result.project or project
        static_result = StaticChecker().check(final_project)
        contract_result = GDLContractChecker().check(final_project)
        criteria_result = assert_success_criteria(final_project, task.success_criteria)
        success = compile_pass and static_result.passed and contract_result.passed and criteria_result.passed

        # 几何断言单独拆分评估（cache 共享，一次 verify_semantics 复用）
        semantic_cache: list = []
        geo_total = 0
        geo_passed = 0
        for assertion in task.success_criteria.semantic_assertions:
            if assertion.type not in GEOMETRIC_ASSERTION_TYPES:
                continue
            geo_total += 1
            if not evaluate_semantic_assertion(final_project, assertion, semantic_cache):
                geo_passed += 1

        return {
            "path": path_label,
            "success": success,
            "compile_pass": compile_pass,
            "static_pass": static_result.passed,
            "contract_pass": contract_result.passed,
            "criteria_pass": criteria_result.passed,
            "criteria_failures": criteria_result.failures,
            "geo_total": geo_total,
            "geo_passed": geo_passed,
            "llm_calls": llm.call_count,
            "elapsed_sec": round(elapsed, 1),
            "changed_files": sorted((result.scripts or {}).keys()),
            "error_summary": "" if success else (result.error or getattr(result.compile_result, "stderr", "") or ""),
        }

    # ── 单任务 A/B ────────────────────────────────────────

    def run_task_ab(self, task_file: str) -> dict:
        task = load_benchmark_task(task_file)
        record = {"task_id": task.id, "paths": {}}
        for label in PATH_LABELS:
            record["paths"][label] = self._run_one(task, label, task_file)
        legacy = record["paths"]["legacy"]
        agent = record["paths"]["agent_loop"]
        if agent["success"] and not legacy["success"]:
            record["winner"] = "agent_loop"
        elif legacy["success"] and not agent["success"]:
            record["winner"] = "legacy"
        else:
            record["winner"] = "tie"
        return record

    def run_suite(self, suite_dir: str) -> list:
        records = []
        for task_file in sorted(Path(suite_dir).glob("*.yaml")):
            task = load_benchmark_task(task_file)
            if not task.fixture:
                print(f"Skipping {task_file.name}: 非 fixture 任务（A/B 只覆盖 MODIFY 任务）")
                continue
            print(f"A/B running {task_file.name}...")
            record = self.run_task_ab(task_file)
            records.append(record)
            legacy = record["paths"]["legacy"]
            agent = record["paths"]["agent_loop"]
            print(
                f"  legacy: {'✅' if legacy['success'] else '❌'} llm={legacy['llm_calls']} | "
                f"agent_loop: {'✅' if agent['success'] else '❌'} llm={agent['llm_calls']} | winner={record['winner']}"
            )
        return records

    # ── 汇总与报告 ────────────────────────────────────────

    def _path_aggregate(self, records: list, label: str) -> dict:
        runs = [r["paths"][label] for r in records]
        total = len(runs)
        if not total:
            return {}
        geo_total = sum(r["geo_total"] for r in runs)
        return {
            "tasks": total,
            "success": sum(1 for r in runs if r["success"]),
            "compile_pass": sum(1 for r in runs if r["compile_pass"]),
            "compile_pass_rate": round(sum(1 for r in runs if r["compile_pass"]) / total, 4),
            "success_rate": round(sum(1 for r in runs if r["success"]) / total, 4),
            "geo_pass_rate": round(sum(r["geo_passed"] for r in runs) / geo_total, 4) if geo_total else None,
            "avg_llm_calls": round(sum(r["llm_calls"] for r in runs) / total, 2),
            "avg_elapsed_sec": round(sum(r["elapsed_sec"] for r in runs) / total, 1),
        }

    def build_summary(self, records: list, suite_name: str) -> dict:
        return {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "suite": suite_name,
            "commit": _git_commit(),
            "mode": self.mode,
            "effective_mode": self.effective_mode,
            "llm": self.llm_label,
            "agent_loop_budget": self.budget,
            "environment": {"platform": platform.platform(), "system": platform.system()},
            "total": len(records),
            "legacy": self._path_aggregate(records, "legacy"),
            "agent_loop": self._path_aggregate(records, "agent_loop"),
            "agent_loop_wins": sum(1 for r in records if r["winner"] == "agent_loop"),
            "legacy_wins": sum(1 for r in records if r["winner"] == "legacy"),
            "ties": sum(1 for r in records if r["winner"] == "tie"),
        }

    def write_results(self, records: list, *, suite_name: str) -> dict[str, str]:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        summary = self.build_summary(records, suite_name)
        date_path = self.results_dir / f"{datetime.date.today()}_{suite_name}_ab.json"
        summary_path = self.results_dir / f"{suite_name}_ab_summary.md"
        date_path.write_text(
            json.dumps({"summary": summary, "records": records}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        summary_path.write_text(render_ab_markdown(summary, records), encoding="utf-8")
        return {"results_json": str(date_path), "summary_md": str(summary_path)}


def render_ab_markdown(summary: dict, records: list) -> str:
    """A/B 对比报告，表头风格仿照 runner.render_markdown_summary。"""
    lines = [
        "# OpenBrep MODIFY A/B Summary（legacy vs agent_loop）",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- commit: {summary.get('commit') or 'unknown'}",
        f"- suite: {summary.get('suite')}",
        f"- compiler mode: {summary.get('mode')} -> {summary.get('effective_mode')}",
        f"- llm: {summary.get('llm')}",
        f"- agent_loop_budget: {summary.get('agent_loop_budget')}",
        "",
        "## 汇总",
        "",
        "| 路径 | 成功率 | 编译通过率 | 几何断言通过率 | 平均 LLM 调用 | 平均耗时(s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, name in (("legacy", "旧路径"), ("agent_loop", "新路径(agent loop)")):
        agg = summary.get(label) or {}
        geo = agg.get("geo_pass_rate")
        lines.append(
            "| {name} | {succ} | {comp} | {geo} | {llm} | {sec} |".format(
                name=name,
                succ=_rate(agg, "success_rate"),
                comp=_rate(agg, "compile_pass_rate"),
                geo="-" if geo is None else f"{geo:.0%}",
                llm=agg.get("avg_llm_calls", "-"),
                sec=agg.get("avg_elapsed_sec", "-"),
            )
        )
    lines += [
        "",
        f"- agent_loop 胜 {summary['agent_loop_wins']} / legacy 胜 {summary['legacy_wins']} / 平 {summary['ties']}（共 {summary['total']} 任务）",
        "",
        "## 逐任务对比",
        "",
        "| Task | legacy 成功 | legacy LLM | agent_loop 成功 | agent_loop LLM | 胜者 |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for record in records:
        legacy = record["paths"]["legacy"]
        agent = record["paths"]["agent_loop"]
        lines.append(
            "| {task} | {ls} | {ll} | {as_} | {al} | {w} |".format(
                task=record["task_id"],
                ls=_mark(legacy["success"]),
                ll=legacy["llm_calls"],
                as_=_mark(agent["success"]),
                al=agent["llm_calls"],
                w=record["winner"],
            )
        )
    return "\n".join(lines) + "\n"


def _rate(agg: dict, key: str) -> str:
    value = agg.get(key)
    return "-" if value is None else f"{value:.0%}"


def _mark(value: bool) -> str:
    return "pass" if value else "fail"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MODIFY 新旧路径 A/B 对照")
    parser.add_argument("--suite", default="benchmark/tasks/modify/", help="MODIFY 任务目录")
    parser.add_argument("--mode", default="mock", choices=["mock", "real", "auto"], help="编译器模式（同 benchmark/runner.py）")
    parser.add_argument("--llm", default="real", choices=["mock", "real"], help="LLM 来源：real=config.toml 真实模型（消耗额度），mock=离线 MockLLM（仅验证流程）")
    parser.add_argument("--budget", type=int, default=6, help="agent loop 工具调用预算（默认 6）")
    parser.add_argument("--config", default="config.toml", help="OpenBrep config path")
    args = parser.parse_args()

    runner = ModifyABRunner(config_path=args.config, mode=args.mode, llm_mode=args.llm, budget=args.budget)
    suite_name = Path(args.suite).name or "modify"
    ab_records = runner.run_suite(args.suite)
    paths = runner.write_results(ab_records, suite_name=suite_name)
    print(f"\nA/B results saved to {paths['results_json']}")
    print(f"A/B summary saved to {paths['summary_md']}")
