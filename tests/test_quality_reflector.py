"""Reflector（openbrep/quality/reflector.py）选择器测试（G4，AC-1）。

覆盖：
- outcome 失败三态（gate_fail / budget_exhausted / timeout）进候选；
- completed + measured 轴 issues 进候选；unavailable / not_applicable 不计 issues；
- completed 零 issues 只作对照（同 intent / 同 path_hash 优先 / 最近）；
- 无对照 contrast_run_id=null 候选仍成立；
- watermark 增量：二次运行零新候选；
- corrupt / 畸形记录逐条跳过不崩；
- 隐私纪律：instruction_summary ≤120、候选只引用不复制。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openbrep.quality import reflector as rr


def _write_record(project_root: Path, data: dict) -> None:
    runs = project_root / ".openbrep" / "quality" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{data['run_id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _fail_delivery() -> dict:
    """gate_fail 常见的 delivery 形状：compile fail + semantic fail。"""
    return {
        "status": "fail",
        "compile": {"status": "fail", "mode": "mock"},
        "static": {"status": "pass", "errors": 0, "warnings": 1},
        "semantic": {"status": "fail", "blocking": 2},
    }


def _pass_delivery() -> dict:
    return {
        "status": "pass",
        "compile": {"status": "pass", "mode": "mock"},
        "static": {"status": "pass", "errors": 0, "warnings": 0},
        "semantic": {"status": "pass", "blocking": 0},
    }


def _record(
    run_id: str,
    *,
    intent: str = "CREATE",
    outcome: str = "completed",
    ts: str = "2026-09-05T10:00:00+00:00",
    name: str = "Shelf",
    path_hash: str = "aaaa11111111",
    instruction: str = "做一个三层书架",
    delivery: dict | None = None,
    artifact: dict | None = None,
    execution: dict | None = None,
    provenance: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "ts": ts,
        "project_ref": {"path_hash": path_hash, "name": name},
        "intent": intent,
        "instruction_summary": instruction,
        "outcome": outcome,
        "delivery": _pass_delivery() if delivery is None else delivery,
        "artifact_quality": artifact
        if artifact is not None
        else {
            "requirements": {"status": "not_applicable"},
            "parametricity": {"status": "unavailable", "reason": "semantic_not_run"},
            "dimension_contract": {"status": "unavailable", "reason": "no_contract"},
            "cross_script": {"status": "unavailable", "reason": "no_project"},
            "topology": {"status": "unavailable", "reason": "no_geometry_metrics"},
        },
        "execution_cost": execution
        if execution is not None
        else {
            "llm_calls": 1,
            "tool_calls": 0,
            "repair_rounds": 0,
            "elapsed_sec": 1.0,
            "timeout": False,
            "budget_exhausted": False,
        },
        "provenance": provenance
        if provenance is not None
        else {"before_revision": None, "after_revision": None},
    }


def _artifact_with_issues(**overrides: dict) -> dict:
    base = {
        "requirements": {"status": "not_applicable"},
        "parametricity": {"status": "unavailable", "reason": "semantic_not_run"},
        "dimension_contract": {"status": "unavailable", "reason": "no_contract"},
        "cross_script": {"status": "unavailable", "reason": "no_project"},
        "topology": {"status": "unavailable", "reason": "no_geometry_metrics"},
    }
    base.update(overrides)
    return base


def _clean_cross_script() -> dict:
    return {
        "status": "measured",
        "issues": [],
        "unknown_edges": 0,
        "coverage": {"parameters": 2, "classified": 2, "ratio": 1.0},
    }


class ReflectorTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _select(self, *projects: Path, watermark=None):
        # 项目目录都建在 self.tmp 下，scan_root 用 self.tmp（递归扫全部项目）
        return rr.select_reflection_candidates(self.tmp, watermark=watermark)


class TestReflectorOutcomeCandidates(ReflectorTestBase):
    def test_gate_fail_enters_candidate(self):
        _write_record(self.tmp / "Shelf", _record("r_gate1", outcome="gate_fail",
                                                  delivery=_fail_delivery()))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand["run_id"], "r_gate1")
        self.assertEqual(cand["project_name"], "Shelf")
        self.assertEqual(cand["intent"], "CREATE")
        types = [c["check_type"] for c in cand["evidence"]["check_failures"]]
        self.assertIn("compile", types)
        self.assertIn("semantic", types)
        self.assertIsNone(cand["contrast_run_id"])

    def test_budget_exhausted_and_timeout_enter_candidates(self):
        _write_record(self.tmp / "Shelf", _record(
            "r_budget1", outcome="budget_exhausted",
            execution={"llm_calls": 4, "tool_calls": 18, "repair_rounds": 0,
                       "elapsed_sec": 42.0, "timeout": False, "budget_exhausted": True},
        ))
        _write_record(self.tmp / "Shelf", _record(
            "r_timeout1", outcome="timeout", ts="2026-09-05T10:01:00+00:00",
            execution={"llm_calls": 2, "tool_calls": 5, "repair_rounds": 1,
                       "elapsed_sec": 120.0, "timeout": True, "budget_exhausted": False},
        ))
        candidates, _ = self._select()
        self.assertEqual([c["run_id"] for c in candidates], ["r_budget1", "r_timeout1"])
        budget = next(c for c in candidates if c["outcome"] == "budget_exhausted")
        self.assertTrue(any(c["check_type"] == "budget_exhausted"
                            for c in budget["evidence"]["check_failures"]))
        timeout = next(c for c in candidates if c["outcome"] == "timeout")
        self.assertTrue(any(c["check_type"] == "timeout"
                            for c in timeout["evidence"]["check_failures"]))

    def test_completed_cancelled_not_evaluable_not_candidates(self):
        for run_id, outcome in (("r_cx", "cancelled"), ("r_nx", "not_evaluable"),
                                ("r_ix", "infrastructure_error"), ("r_ok", "completed")):
            _write_record(self.tmp / "Shelf", _record(run_id, outcome=outcome))
        candidates, _ = self._select()
        self.assertEqual(candidates, [])


class TestReflectorMeasuredIssues(ReflectorTestBase):
    def test_completed_with_measured_cross_script_issues_enters(self):
        _write_record(self.tmp / "Shelf", _record(
            "r_issues1", outcome="completed",
            artifact=_artifact_with_issues(cross_script={
                "status": "measured",
                "issues": [
                    {"kind": "enum_missing_branch",
                     "detail": "VALUES item '方形' for 'shape' has no 2D/3D branch",
                     "file": "scripts/vl.gdl", "line": None},
                    {"kind": "unknown_target",
                     "detail": "PARAMETERS targets unknown parameter 'Xx'",
                     "file": "scripts/3d.gdl", "line": 7},
                ],
                "unknown_edges": 0,
                "coverage": {"parameters": 2, "classified": 2, "ratio": 1.0},
            }),
        ))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        issues = candidates[0]["evidence"]["issues"]
        self.assertEqual([i["check_type"] for i in issues],
                         ["enum_missing_branch", "unknown_target"])
        self.assertEqual(candidates[0]["evidence"]["issue_count"], 2)
        # completed 零 issues 记录不存在 → 对照 null 但候选仍成立
        self.assertIsNone(candidates[0]["contrast_run_id"])

    def test_completed_with_sweep_issues_enters(self):
        _write_record(self.tmp / "Shelf", _record(
            "r_sweep1", outcome="completed",
            artifact=_artifact_with_issues(parametricity={
                "status": "measured", "score": None, "coverage": None,
                "sweep_issues": {"sweep_unresponsive": 1, "sweep_mesh_vanished": 0},
                "eligibility": {"role_counts": {"geometry_driver": 1},
                                "geometry_driver_count": 1,
                                "test_value_coverage": 1.0},
            }),
        ))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["evidence"]["issue_count"], 1)
        self.assertEqual(candidates[0]["evidence"]["issues"][0]["check_type"],
                         "sweep_unresponsive")

    def test_unavailable_and_not_applicable_axes_not_issues(self):
        # measured 但零 sweep issues；其余轴 unavailable / not_applicable → 零 issues
        _write_record(self.tmp / "Shelf", _record(
            "r_zero1", outcome="completed",
            artifact=_artifact_with_issues(
                requirements={"status": "not_applicable", "score": None, "coverage": None},
                parametricity={"status": "measured", "score": None, "coverage": None,
                               "sweep_issues": {"sweep_unresponsive": 0,
                                                "sweep_mesh_vanished": 0},
                               "eligibility": {"role_counts": {},
                                               "geometry_driver_count": 0,
                                               "test_value_coverage": None}},
                cross_script=_clean_cross_script(),
            ),
        ))
        candidates, watermark = self._select()
        self.assertEqual(candidates, [])
        self.assertEqual(watermark["processed_run_ids"], [])  # 零候选不推进 watermark


class TestReflectorContrast(ReflectorTestBase):
    def test_same_intent_recent_completed_clean_contrast(self):
        _write_record(self.tmp / "Shelf", _record("r_clean1", ts="2026-09-05T09:00:00+00:00"))
        _write_record(self.tmp / "Shelf", _record("r_clean2", ts="2026-09-05T09:30:00+00:00"))
        _write_record(self.tmp / "Shelf", _record("r_fail1", outcome="gate_fail",
                                                  ts="2026-09-05T10:00:00+00:00",
                                                  delivery=_fail_delivery()))
        # 其它 intent 的 clean 记录不参与对照
        _write_record(self.tmp / "Shelf", _record("r_clean_modify", intent="MODIFY",
                                                  ts="2026-09-05T10:30:00+00:00"))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["contrast_run_id"], "r_clean2")

    def test_same_project_clean_preferred_over_newer_other_project(self):
        _write_record(self.tmp / "ShelfA", _record(
            "r_fail_a", outcome="gate_fail", ts="2026-09-05T11:00:00+00:00",
            name="ShelfA", path_hash="aaa111111111", delivery=_fail_delivery(),
        ))
        # 同项目 clean（更旧）vs 其它项目 clean（更新）→ 取同项目
        _write_record(self.tmp / "ShelfA", _record(
            "r_clean_a_old", ts="2026-09-05T08:00:00+00:00",
            name="ShelfA", path_hash="aaa111111111",
        ))
        _write_record(self.tmp / "ShelfB", _record(
            "r_clean_b_new", ts="2026-09-05T12:00:00+00:00",
            name="ShelfB", path_hash="bbb222222222",
        ))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["contrast_run_id"], "r_clean_a_old")

    def test_intent_grouping_case_insensitive(self):
        _write_record(self.tmp / "Shelf", _record(
            "r_fail_lower", intent="modify", outcome="gate_fail", delivery=_fail_delivery(),
        ))
        _write_record(self.tmp / "Shelf", _record("r_clean_upper", intent="MODIFY",
                                                  ts="2026-09-05T09:00:00+00:00"))
        candidates, _ = self._select()
        self.assertEqual(candidates[0]["contrast_run_id"], "r_clean_upper")

    def test_no_contrast_null_and_candidate_still_valid(self):
        _write_record(self.tmp / "Shelf", _record("r_fail1", outcome="budget_exhausted"))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0]["contrast_run_id"])


class TestReflectorWatermark(ReflectorTestBase):
    def test_second_run_zero_new_candidates(self):
        _write_record(self.tmp / "Shelf", _record("r_fail1", outcome="gate_fail",
                                                  delivery=_fail_delivery()))
        _write_record(self.tmp / "Shelf", _record("r_fail2", outcome="timeout",
                                                  ts="2026-09-05T10:01:00+00:00"))
        candidates, new_watermark = self._select()
        self.assertEqual(len(candidates), 2)
        again, again_watermark = self._select(watermark=new_watermark)
        self.assertEqual(again, [])
        # 新 run 出现 → 只选新 run（增量语义）
        _write_record(self.tmp / "Shelf", _record("r_fail3", outcome="gate_fail",
                                                  ts="2026-09-05T10:02:00+00:00",
                                                  delivery=_fail_delivery()))
        third, third_watermark = self._select(watermark=again_watermark)
        self.assertEqual([c["run_id"] for c in third], ["r_fail3"])
        self.assertEqual(third_watermark["processed_run_ids"],
                         ["r_fail1", "r_fail2", "r_fail3"])


class TestReflectorRobustness(ReflectorTestBase):
    def test_corrupt_records_skipped_without_crash(self):
        runs = self.tmp / "Shelf" / ".openbrep" / "quality" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "bad.json").write_text("{not valid json", encoding="utf-8")
        (runs / "list.json").write_text("[1,2,3]", encoding="utf-8")
        (runs / "empty.json").write_text("", encoding="utf-8")
        _write_record(self.tmp / "Shelf", _record("r_fail1", outcome="gate_fail",
                                                  delivery=_fail_delivery()))
        # 畸形轴形状（字符串而非 dict）也不崩
        _write_record(self.tmp / "Shelf", _record(
            "r_weird_axis", outcome="completed",
            artifact=_artifact_with_issues(cross_script="boom", topology=None),
        ))
        candidates, _ = self._select()
        self.assertEqual([c["run_id"] for c in candidates], ["r_fail1"])

    def test_missing_required_fields_skipped(self):
        runs = self.tmp / "Shelf" / ".openbrep" / "quality" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "no_run_id.json").write_text(json.dumps({"outcome": "gate_fail"}),
                                             encoding="utf-8")
        (runs / "not_dict.json").write_text(json.dumps("hello"), encoding="utf-8")
        candidates, _ = self._select()
        self.assertEqual(candidates, [])

    def test_scan_root_missing_or_unreadable_never_raises(self):
        candidates, watermark = rr.select_reflection_candidates(self.tmp / "no-such-dir")
        self.assertEqual(candidates, [])
        self.assertEqual(watermark["processed_run_ids"], [])
        candidates, _ = rr.select_reflection_candidates(12345)  # type: ignore[arg-type]
        self.assertEqual(candidates, [])

    def test_instruction_summary_privacy_truncated_to_120(self):
        long_instruction = "请帮我做一个非常复杂的书架" * 20
        _write_record(self.tmp / "Shelf", _record("r_fail1", outcome="gate_fail",
                                                  instruction=long_instruction,
                                                  delivery=_fail_delivery()))
        candidates, _ = self._select()
        self.assertEqual(len(candidates), 1)
        self.assertLessEqual(len(candidates[0]["instruction_summary"]), 120)
        self.assertNotEqual(candidates[0]["instruction_summary"], long_instruction)

    def test_candidate_evidence_never_copies_script_text(self):
        script_snippet = (
            "BLOCK A, B, ZZYZX\n"
            "ADDZ 10\n"
            "FOR i = 1 TO 5\n"
            "  ADDX i * 2\n"
            "  BLOCK A, B, 3\n"
            "NEXT i\n"
            "DEL 1\n"
            "END\n"
        )
        # 前缀（详细说明×40 + 引导语）远超 120 字符截断线，脚本正文必然落在
        # instruction_summary 截断之后——候选 blob 里不可能出现脚本行。
        long_instruction = "按照参考图生成构件：" + "详细说明" * 40 + script_snippet
        _write_record(self.tmp / "Shelf", _record(
            "r_fail1", outcome="gate_fail", instruction=long_instruction,
            delivery=_fail_delivery(),
            provenance={"before_revision": "rev_11", "after_revision": "rev_12"},
        ))
        candidates, _ = self._select()
        cand = candidates[0]
        self.assertEqual(cand["revisions"],
                         {"before_revision": "rev_11", "after_revision": "rev_12"})
        blob = json.dumps(cand, ensure_ascii=False)
        # 只含 run/check/revision 引用；脚本正文任何一行都不进候选（隐私纪律）
        for line in script_snippet.splitlines():
            self.assertNotIn(line, blob)
        self.assertIn("r_fail1", blob)
        self.assertIn("rev_11", blob)
