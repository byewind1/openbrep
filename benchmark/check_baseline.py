"""benchmark 回放基线检查（CI 门禁）。

用黄金语料回放 create + modify 套件，与 benchmark/baseline.json 比较：

红灯（退出码 1）：
- 任何题 PASS → FAIL
- 任何题 criteria_failures 数量增加
- 任一套件 pass 数下降

允许通过（但应跑 update_baseline 更新基线）：
- FAIL → PASS、criteria_failures 减少、基线外新任务

环境一致性：回放统一使用 mock 编译器，保证本地（装了 LP_XMLConverter）
与 CI（没装）产出同一份基线语义。真机编译验证走本地/发布流程，不在这里。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.runner import BenchmarkRunner  # noqa: E402

BASELINE_PATH = PROJECT_ROOT / "benchmark" / "baseline.json"

SUITES = {
    # (task_dir, corpus, replay_config, use_agent_loop)
    # modify 自 S4 起在 agent loop 路径（patch_script 工具链）录制/回放：
    # 回放必须开 agent_loop=True，否则 runner 默认强制旧路径 → 每个 agent loop
    # 条目都 miss。create 套件不受 --agent-loop 影响，保持 False。
    "create": (
        "benchmark/tasks/create/",
        "benchmark/fixtures/llm_corpus/create.jsonl",
        "benchmark/fixtures/replay_config_create.toml",
        False,
    ),
    "modify": (
        "benchmark/tasks/modify/",
        "benchmark/fixtures/llm_corpus/modify.jsonl",
        "benchmark/fixtures/replay_config_modify.toml",
        True,
    ),
}


def run_suites(suites: dict | None = None) -> dict[str, list]:
    """回放全部套件（mock 编译器 + 黄金语料 + temp workdir），返回 {suite: results}。"""
    suites = suites or SUITES
    out: dict[str, list] = {}
    for name, (suite_dir, corpus, config_path, use_agent_loop) in suites.items():
        with tempfile.TemporaryDirectory() as td:
            runner = BenchmarkRunner(
                config_path=str(PROJECT_ROOT / config_path),
                mode="mock",
                temperature=0.0,
                llm_replay=str(PROJECT_ROOT / corpus),
                agent_loop=use_agent_loop,
            )
            runner.results_dir = Path(td) / "results"
            runner.work_dir = Path(td) / "workdir"
            out[name] = runner.run_suite(str(PROJECT_ROOT / suite_dir), jobs=4)
    return out


def compare_suite(suite: str, baseline_suite: dict, results: list) -> tuple[list[str], list[str]]:
    """返回 (退化列表, 改进列表)。任一退化即整体红灯。"""
    regressions: list[str] = []
    improvements: list[str] = []
    base_tasks: dict = baseline_suite.get("tasks", {})

    pass_now = sum(1 for r in results if r["success"])
    pass_base = baseline_suite.get("pass", 0)
    if pass_now < pass_base:
        regressions.append(f"{suite}: pass 数 {pass_base} → {pass_now}")
    elif pass_now > pass_base:
        improvements.append(f"{suite}: pass 数 {pass_base} → {pass_now}（应更新基线）")

    for r in results:
        tid = r["task_id"]
        base = base_tasks.get(tid)
        if base is None:
            improvements.append(f"{suite}/{tid}: 新任务（基线无记录）")
            continue
        if base["success"] and not r["success"]:
            regressions.append(f"{suite}/{tid}: PASS → FAIL")
        elif not base["success"] and r["success"]:
            improvements.append(f"{suite}/{tid}: FAIL → PASS（应更新基线）")
        b_fails = base["criteria_failures"]
        n_fails = len(r.get("criteria_failures", []) or [])
        if n_fails > b_fails:
            regressions.append(f"{suite}/{tid}: criteria_failures {b_fails} → {n_fails}")
        elif n_fails < b_fails:
            improvements.append(
                f"{suite}/{tid}: criteria_failures {b_fails} → {n_fails}（应更新基线）"
            )
    return regressions, improvements


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if not BASELINE_PATH.exists():
        print(f"基线文件不存在：{BASELINE_PATH}（先跑 python -m benchmark.update_baseline --init --confirm）")
        return 2
    baseline = load_baseline()
    results_by_suite = run_suites()

    all_regressions: list[str] = []
    all_improvements: list[str] = []
    for suite, results in results_by_suite.items():
        base_suite = baseline.get("suites", {}).get(suite)
        if base_suite is None:
            all_improvements.append(f"{suite}: 基线中不存在该套件（应更新基线）")
            continue
        regressions, improvements = compare_suite(suite, base_suite, results)
        all_regressions.extend(regressions)
        all_improvements.extend(improvements)

    for line in all_improvements:
        print(f"  ↑ {line}")
    if all_regressions:
        print("\nbenchmark 回放出现退化：")
        for line in all_regressions:
            print(f"  ✗ {line}")
        return 1
    print("benchmark 回放无退化 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
