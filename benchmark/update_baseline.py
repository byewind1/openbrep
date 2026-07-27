"""从最新回放结果生成/更新 benchmark/baseline.json。

规则：
- 只能往好的方向更新：pass 数增加、FAIL→PASS、criteria_failures 减少。
  出现任何退化（PASS→FAIL、failures 增加、pass 数下降）则拒绝写入并列出。
- 必须显式 --confirm 才写盘；不加 --confirm 只打印将要发生的变化（dry-run）。
- 首次创建用 --init --confirm（不校验退化）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.check_baseline import (  # noqa: E402
    BASELINE_PATH,
    SUITES,
    compare_suite,
    load_baseline,
    run_suites,
)
from benchmark.runner import _git_commit  # noqa: E402


def build_baseline(results_by_suite: dict[str, list]) -> dict:
    suites = {}
    for name, results in results_by_suite.items():
        suites[name] = {
            "corpus": SUITES[name][1],
            "pass": sum(1 for r in results if r["success"]),
            "tasks": {
                r["task_id"]: {
                    "success": bool(r["success"]),
                    "criteria_failures": len(r.get("criteria_failures", []) or []),
                }
                for r in results
            },
        }
    return {
        "version": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "mode": "mock",
        "suites": suites,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 benchmark 回放基线")
    parser.add_argument("--init", action="store_true", help="首次创建基线（不校验退化）")
    parser.add_argument("--confirm", action="store_true", help="确认写盘；缺省只 dry-run")
    args = parser.parse_args()

    results_by_suite = run_suites()

    if BASELINE_PATH.exists() and not args.init:
        baseline = load_baseline()
        regressions: list[str] = []
        improvements: list[str] = []
        for suite, results in results_by_suite.items():
            base_suite = baseline.get("suites", {}).get(suite, {"tasks": {}, "pass": 0})
            r, i = compare_suite(suite, base_suite, results)
            regressions.extend(r)
            improvements.extend(i)
        if regressions:
            print("拒绝更新基线：检测到退化（基线只能往好的方向更新）：")
            for line in regressions:
                print(f"  ✗ {line}")
            return 1
        for line in improvements:
            print(f"  ↑ {line}")
        if not improvements:
            print("与当前基线一致，无变化。")
    else:
        print(f"初始化基线（{BASELINE_PATH}）")

    new_baseline = build_baseline(results_by_suite)
    total_pass = sum(s["pass"] for s in new_baseline["suites"].values())
    total = sum(len(s["tasks"]) for s in new_baseline["suites"].values())
    print(f"新基线：pass {total_pass}/{total}（commit {new_baseline['commit'][:8]}）")

    if not args.confirm:
        print("dry-run：未写盘。确认无误后加 --confirm 重跑。")
        return 0
    BASELINE_PATH.write_text(
        json.dumps(new_baseline, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
