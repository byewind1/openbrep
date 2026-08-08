"""benchmark/check_baseline.py 对比逻辑的合同测试。"""

from __future__ import annotations

import unittest

from benchmark.check_baseline import compare_suite


def _baseline(tasks: dict, pass_count: int | None = None) -> dict:
    return {
        "pass": pass_count if pass_count is not None else sum(1 for t in tasks.values() if t["success"]),
        "tasks": tasks,
    }


def _result(tid: str, success: bool, n_failures: int = 0) -> dict:
    return {
        "task_id": tid,
        "success": success,
        "criteria_failures": ["x"] * n_failures,
    }


class TestCompareSuite(unittest.TestCase):
    def test_pass_to_fail_is_regression(self):
        base = _baseline({"C01": {"success": True, "criteria_failures": 0}})
        regressions, _ = compare_suite("create", base, [_result("C01", False, 3)])
        self.assertTrue(any("PASS → FAIL" in r for r in regressions))

    def test_failures_increase_is_regression(self):
        base = _baseline({"C01": {"success": False, "criteria_failures": 2}})
        regressions, _ = compare_suite("create", base, [_result("C01", False, 5)])
        self.assertTrue(any("criteria_failures 2 → 5" in r for r in regressions))

    def test_pass_count_drop_is_regression(self):
        base = _baseline({
            "C01": {"success": True, "criteria_failures": 0},
            "C02": {"success": True, "criteria_failures": 0},
        })
        # C02 从结果中消失（套件被删题/崩溃未产出）→ pass 数 2 → 1
        regressions, _ = compare_suite("create", base, [_result("C01", True)])
        self.assertTrue(any("pass 数 2 → 1" in r for r in regressions))

    def test_fail_to_pass_is_improvement_not_regression(self):
        base = _baseline({"C01": {"success": False, "criteria_failures": 4}})
        regressions, improvements = compare_suite("create", base, [_result("C01", True, 0)])
        self.assertEqual(regressions, [])
        self.assertTrue(any("FAIL → PASS" in i for i in improvements))

    def test_failures_decrease_is_improvement(self):
        base = _baseline({"C01": {"success": False, "criteria_failures": 6}})
        regressions, improvements = compare_suite("create", base, [_result("C01", False, 2)])
        self.assertEqual(regressions, [])
        self.assertTrue(any("criteria_failures 6 → 2" in i for i in improvements))

    def test_new_task_is_improvement(self):
        base = _baseline({"C01": {"success": True, "criteria_failures": 0}})
        regressions, improvements = compare_suite(
            "create", base, [_result("C01", True), _result("C99", False, 3)]
        )
        self.assertEqual(regressions, [])
        self.assertTrue(any("新任务" in i for i in improvements))

    def test_identical_is_clean(self):
        base = _baseline({"C01": {"success": True, "criteria_failures": 0}})
        regressions, improvements = compare_suite("create", base, [_result("C01", True, 0)])
        self.assertEqual(regressions, [])
        self.assertEqual(improvements, [])


class TestSuiteAgentLoopFlag(unittest.TestCase):
    """锁定 check_baseline 的套件回放配置：modify 开 agent_loop=True（防回归成
    旧路径强制造假的"绿"），create 保持 False（--agent-loop 不碰 CREATE）。"""

    def test_modify_agent_loop_true_create_false(self):
        from unittest.mock import patch

        from benchmark.check_baseline import PROJECT_ROOT, run_suites

        captured: dict[str, bool | None] = {}

        class _FakeRunner:
            def __init__(self, **kwargs):
                captured[str(kwargs.get("llm_replay"))] = kwargs.get("agent_loop")

            def run_suite(self, suite_dir, jobs=4):
                return []

        with patch("benchmark.check_baseline.BenchmarkRunner", _FakeRunner):
            run_suites()

        self.assertIs(captured[str(PROJECT_ROOT / "benchmark/fixtures/llm_corpus/modify.jsonl")], True)
        self.assertIs(captured[str(PROJECT_ROOT / "benchmark/fixtures/llm_corpus/create.jsonl")], False)

    def test_modify_agent_loop_false_is_a_regression_guard(self):
        """如果 modify 套件被改成 agent_loop=False，直接红灯（旧路径强制造假绿）。"""
        from benchmark.check_baseline import SUITES

        self.assertTrue(SUITES["modify"][2], "modify 套件必须开 agent_loop=True")


if __name__ == "__main__":
    unittest.main()
