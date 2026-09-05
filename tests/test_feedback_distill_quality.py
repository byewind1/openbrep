"""G4 Curator（证据驱动蒸馏，openbrep/feedback_distill.py）测试。

覆盖（G4 派单 AC-2 + 测试要求）：
- 全流程：候选 → LLM 提炼 → 落盘（quality: 指纹 / evidence_refs 带
  before/after revision / 一律 proposed / raw_excerpt 只含证据 detail）
- parse 硬校验：无 evidence_refs / ref 元素缺 run_id|check_type / ref
  语义不匹配（run_id、check_type 不在案例证据内）→ 丢弃并计数 rejected；
  item=null 静默跳过
- 脚本不落盘（红队 #1）：含整段 GDL 脚本的 LLM 响应 → 落盘 lessons 无脚本
- raw_excerpt 500 封顶（parse/merge 双侧）
- 同指纹确定性合并：count 累加 / evidence_refs 并集 / status 不动
- watermark：批成功才推进；二次运行零候选不建 LLM
- 旧格式 lesson（无 evidence_refs）兼容：可读、可渲染、视图字段缺省 None
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openbrep import feedback_distill as fd


def _record_data(
    run_id: str,
    *,
    intent: str = "create",
    outcome: str = "gate_fail",
    ts: str = "2026-09-05T10:00:00+00:00",
    name: str = "Shelf",
    path_hash: str = "abc123",
    instruction: str = "做一个书柜",
    delivery: dict | None = None,
    revisions: dict | None = None,
) -> dict:
    """最小 QualityRecord dict（store.load_records 只做 json.load，不校验）。"""
    if delivery is None:
        delivery = {
            "status": "fail",
            "compile": {"status": "fail", "mode": "mock"},
            "static": {"status": "not_run"},
            "semantic": {"status": "not_run"},
        }
    if revisions is None:
        revisions = {"before_revision": "rev_1", "after_revision": "rev_2"}
    return {
        "schema_version": 1,
        "run_id": run_id,
        "ts": ts,
        "project_ref": {"path_hash": path_hash, "name": name},
        "intent": intent,
        "instruction_summary": instruction,
        "outcome": outcome,
        "delivery": delivery,
        "artifact_quality": {},
        "execution_cost": {"llm_calls": 3, "tool_calls": 4},
        "provenance": revisions,
    }


def _write_record(project_root: Path, data: dict) -> Path:
    runs = project_root / ".openbrep" / "quality" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{data['run_id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _lesson_file(work_dir: Path) -> Path:
    return work_dir / ".openbrep" / "memory" / "learnings" / "distilled_lessons.jsonl"


def _read_lessons(work_dir: Path) -> list[dict]:
    path = _lesson_file(work_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FakeLLM:
    def __init__(self, content: str, error: bool = False):
        self.content = content
        self.error = error
        self.calls = 0
        self.last_messages = None

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.error:
            raise RuntimeError("llm down")
        return type("Resp", (), {"content": self.content})()


def _quality_item(
    pattern: str = "书架指令描述不清导致编译失败",
    guidance: str = "生成前核对参数脚本与指令摘要",
    refs: list[dict] | None = None,
    run_id: str = "r_gate_1",
    check_type: str = "compile",
) -> dict:
    if refs is None:
        refs = [{"run_id": run_id, "check_type": check_type}]
    return {"pattern": pattern, "guidance": guidance, "evidence_refs": refs}


class QualityCuratorBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g4-curator-"))
        self.project = self.tmp / "Shelf"
        (self.project / ".openbrep" / "memory" / "learnings").mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _candidate(self, run_id: str, **overrides) -> dict:
        """Reflector 输出形状的候选（与 distill_quality_records 输入同构）。"""
        record = _record_data(run_id)
        candidate = {
            "run_id": run_id,
            "project_name": "Shelf",
            "path_hash": "abc123",
            "intent": record["intent"],
            "outcome": record["outcome"],
            "instruction_summary": record["instruction_summary"],
            "ts": record["ts"],
            "evidence": {
                "check_failures": [{"check_type": "compile", "detail": "编译失败（mode=mock）"}],
                "issues": [],
                "issue_count": 0,
            },
            "revisions": {"before_revision": "rev_1", "after_revision": "rev_2"},
            "contrast_run_id": None,
        }
        candidate.update(overrides)
        return candidate

    def _seed_fail_run(self, run_id: str = "r_gate_1") -> None:
        _write_record(self.project, _record_data(run_id))


class TestQualityFingerprint(QualityCuratorBase):
    def test_fingerprint_stable_across_runs_and_pre_llm(self):
        a = self._candidate("r_1")
        b = self._candidate("r_2")  # 仅 run_id 不同 → 同一指纹
        self.assertEqual(fd.quality_lesson_fingerprint(a), fd.quality_lesson_fingerprint(b))
        self.assertTrue(fd.quality_lesson_fingerprint(a).startswith("quality:"))

    def test_fingerprint_changes_with_evidence(self):
        a = self._candidate("r_1")
        b = self._candidate("r_2")
        b["evidence"]["check_failures"].append(
            {"check_type": "static", "detail": "2 个静态错误"}
        )
        self.assertNotEqual(fd.quality_lesson_fingerprint(a), fd.quality_lesson_fingerprint(b))


class TestQualityDistillEndToEnd(QualityCuratorBase):
    def test_full_flow_writes_proposed_lesson_with_refs(self):
        self._seed_fail_run()
        llm = FakeLLM(json.dumps([_quality_item()], ensure_ascii=False))
        result = fd.distill_quality_records(self.project, self.project, llm=llm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 1)
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(result["total_lessons"], 1)
        lessons = _read_lessons(self.project)
        self.assertEqual(len(lessons), 1)
        lesson = lessons[0]
        self.assertTrue(lesson["fingerprint"].startswith("quality:"))
        self.assertEqual(lesson["status"], "proposed")  # 自动产物绝不 active
        self.assertEqual(lesson["count"], 1)
        self.assertEqual(lesson["evidence_refs"], [{
            "run_id": "r_gate_1",
            "check_type": "compile",
            "before_revision": "rev_1",
            "after_revision": "rev_2",
        }])
        self.assertEqual(lesson["raw_excerpt"], "编译失败（mode=mock）")

    def test_auto_products_are_proposed_even_with_contrast(self):
        self._seed_fail_run()
        item = _quality_item()
        llm = FakeLLM(json.dumps([item], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        self.assertEqual(_read_lessons(self.project)[0]["status"], "proposed")

    def test_second_run_zero_candidates_skips_llm_and_watermark_persisted(self):
        self._seed_fail_run()
        llm = FakeLLM(json.dumps([_quality_item()], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        watermark_path = (
            self.project / ".openbrep" / "memory" / "learnings" / "reflector_watermark.json"
        )
        self.assertTrue(watermark_path.exists())
        second = fd.distill_quality_records(self.project, self.project, llm=llm)
        self.assertEqual(second["new_lessons"], 0)
        self.assertEqual(llm.calls, 1)  # 二次运行不建 LLM
        self.assertEqual(_read_lessons(self.project)[0]["count"], 1)  # 不重复计数

    def test_llm_failure_keeps_watermark_unadvanced(self):
        self._seed_fail_run()
        llm = FakeLLM("", error=True)
        result = fd.distill_quality_records(self.project, self.project, llm=llm)
        self.assertEqual(result["note"], "llm_failed")
        wm = self.project / ".openbrep" / "memory" / "learnings" / "reflector_watermark.json"
        self.assertFalse(wm.exists())
        retry = FakeLLM(json.dumps([_quality_item()], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=retry)
        self.assertEqual(retry.calls, 1)  # 重试能再次选中同候选
        self.assertEqual(_read_lessons(self.project)[0]["count"], 1)

    def test_no_candidates_returns_empty_ok(self):
        result = fd.distill_quality_records(self.project, self.project, llm=FakeLLM("[]"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 0)
        self.assertEqual(_read_lessons(self.project), [])

    def test_message_card_shows_evidence_tags_and_run(self):
        self._seed_fail_run()
        llm = FakeLLM(json.dumps([_quality_item()], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        user_content = llm.last_messages[-1]["content"]
        self.assertIn("run_id: r_gate_1", user_content)
        self.assertIn("- [compile] 编译失败（mode=mock）", user_content)
        self.assertIn("对照: 无", user_content)
        self.assertIn("质量账本记录", llm.last_messages[0]["content"])


class TestQualityParseHardValidation(QualityCuratorBase):
    def _parse_one(self, item, candidate=None):
        candidate = candidate or self._candidate("r_gate_1")
        return fd.parse_quality_distill_response(
            json.dumps([item], ensure_ascii=False), [candidate]
        )

    def test_missing_evidence_refs_rejected(self):
        parsed = self._parse_one({"pattern": "p", "guidance": "g"})
        self.assertIsNotNone(parsed)
        lessons, rejected = parsed
        self.assertEqual(lessons, [])
        self.assertEqual(rejected, 1)

    def test_empty_ref_list_rejected(self):
        parsed = self._parse_one(_quality_item(refs=[]))
        self.assertEqual(parsed, ([], 1))

    def test_ref_element_missing_run_id_or_check_type_rejected(self):
        for ref in ({"check_type": "compile"}, {"run_id": "r_gate_1"}, {"run_id": 1}):
            parsed = self._parse_one(_quality_item(refs=[ref]))
            lessons, rejected = parsed
            self.assertEqual(lessons, [], f"ref={ref}")
            self.assertEqual(rejected, 1, f"ref={ref}")

    def test_ref_wrong_run_id_rejected(self):
        ref = {"run_id": "r_other", "check_type": "compile"}
        parsed = self._parse_one(_quality_item(refs=[ref]))
        self.assertEqual(parsed[1], 1)

    def test_ref_unknown_check_type_rejected(self):
        ref = {"run_id": "r_gate_1", "check_type": "invented"}
        parsed = self._parse_one(_quality_item(refs=[ref]))
        self.assertEqual(parsed[1], 1)

    def test_null_item_skipped_silently(self):
        parsed = self._parse_one(None)
        self.assertEqual(parsed, ([], 0))

    def test_malformed_response_whole_batch_none(self):
        two_items = json.dumps([_quality_item(), _quality_item()], ensure_ascii=False)
        for text in ("not json", "[]", two_items):
            parsed = fd.parse_quality_distill_response(text, [self._candidate("r_1")])
            self.assertIsNone(parsed)

    def test_issue_kind_can_be_referenced(self):
        candidate = self._candidate("r_ok_1", outcome="completed")
        candidate["evidence"]["issues"] = [
            {"check_type": "enum_missing_branch", "detail": "switch 少一支"},
        ]
        parsed = fd.parse_quality_distill_response(
            json.dumps(
                [_quality_item(run_id="r_ok_1", check_type="enum_missing_branch")],
                ensure_ascii=False,
            ),
            [candidate],
        )
        lessons, rejected = parsed
        self.assertEqual(rejected, 0)
        self.assertEqual(lessons[0]["evidence_refs"][0]["check_type"], "enum_missing_branch")
        self.assertEqual(lessons[0]["raw_excerpt"], "switch 少一支")

    def test_clean_text_caps_pattern_and_guidance(self):
        parsed = self._parse_one({
            "pattern": "长" * 200,
            "guidance": "详" * 400,
            "evidence_refs": [{"run_id": "r_gate_1", "check_type": "compile"}],
        })
        lesson = parsed[0][0]
        self.assertLessEqual(len(lesson["pattern"]), 100)
        self.assertLessEqual(len(lesson["guidance"]), 300)

    def test_refs_injected_with_revisions_and_deduped(self):
        # 重复引用同一 check → 去重为一条；revisions 由解释器注入
        refs = [
            {"run_id": "r_gate_1", "check_type": "compile"},
            {"run_id": "r_gate_1", "check_type": "compile"},
        ]
        parsed = self._parse_one(_quality_item(refs=refs))
        lessons, rejected = parsed
        self.assertEqual(rejected, 0)
        self.assertEqual(lessons[0]["evidence_refs"], [{
            "run_id": "r_gate_1",
            "check_type": "compile",
            "before_revision": "rev_1",
            "after_revision": "rev_2",
        }])


class TestQualityScriptDiscipline(QualityCuratorBase):
    SCRIPT = (
        "BLOCK A, B, ZZYZX\n"
        "ADDZ 10\n"
        "FOR i = 1 TO 5\n"
        "  ADDX i * 2\n"
        "  BLOCK A, B, 3\n"
        "NEXT i\n"
        "DEL 1\n"
        "END\n"
    )

    def test_lesson_on_disk_never_contains_script_lines(self):
        """红队 #1：LLM 响应含整段脚本 → 落盘无脚本原文。"""
        self._seed_fail_run()
        guidance = (
            "修复建议：\n" + self.SCRIPT + "再编译验证\n"
            "i = 99\nx = 1\n"  # 纯 ASCII 赋值行也会被删除
        )
        llm = FakeLLM(json.dumps([_quality_item(guidance=guidance)], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        blob = _lesson_file(self.project).read_text(encoding="utf-8")
        for line in self.SCRIPT.splitlines():
            self.assertNotIn(line, blob)
        self.assertNotIn("i = 99", blob)
        # 清洗只删脚本行，prose 仍在
        self.assertIn("修复建议", blob)
        self.assertIn("再编译验证", blob)

    def test_pattern_script_lines_also_filtered(self):
        self._seed_fail_run()
        item = _quality_item(pattern="模式：\n" + self.SCRIPT)
        llm = FakeLLM(json.dumps([item], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        blob = _lesson_file(self.project).read_text(encoding="utf-8")
        for line in self.SCRIPT.splitlines():
            self.assertNotIn(line, blob)
        self.assertIn("模式", blob)

    def test_raw_excerpt_capped_at_500(self):
        self._seed_fail_run()
        long_detail = "编译失败，" + "错误详情" * 200  # >500 字
        candidate = self._candidate("r_gate_1")
        candidate["evidence"]["check_failures"] = [
            {"check_type": "compile", "detail": long_detail}
        ]
        parsed = fd.parse_quality_distill_response(
            json.dumps([_quality_item()], ensure_ascii=False), [candidate]
        )
        lesson = parsed[0][0]
        self.assertLessEqual(len(lesson["raw_excerpt"]), 500)

    def test_merge_side_recaps_raw_excerpt(self):
        """merge 侧强制：直接构造脏 raw_excerpt 也能被清洗封顶。"""
        existing = [{
            "fingerprint": "quality:same",
            "pattern": "p",
            "guidance": "g",
            "evidence_refs": [{"run_id": "r_1", "check_type": "compile"}],
            "raw_excerpt": "x",
            "count": 1,
            "first_seen": "",
            "last_seen": "",
            "status": "proposed",
        }]
        dirty = {
            "fingerprint": "quality:same",
            "pattern": "p2",
            "guidance": "g2",
            "count": 1,
            "first_seen": "",
            "last_seen": "",
            "status": "proposed",
            "raw_excerpt": "BLOCK A, B, ZZYZX\n" + "很" * 600,
        }
        merged = fd.merge_lessons(existing, [dirty])
        excerpt = merged[0]["raw_excerpt"]
        self.assertNotIn("BLOCK", excerpt)
        self.assertLessEqual(len(excerpt), 500)


class TestQualityMerge(QualityCuratorBase):
    def test_same_fingerprint_merges_refs_and_count(self):
        """同批两个同指纹候选（同失败模式不同 run）→ 合并、refs 并集。"""
        self._seed_fail_run("r_gate_1")
        self._seed_fail_run("r_gate_2")
        items = [_quality_item(run_id="r_gate_1"), _quality_item(run_id="r_gate_2")]
        llm = FakeLLM(json.dumps(items, ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        lessons = _read_lessons(self.project)
        self.assertEqual(len(lessons), 1)  # 同指纹合并为一条
        lesson = lessons[0]
        self.assertEqual(lesson["count"], 2)
        run_ids = {ref["run_id"] for ref in lesson["evidence_refs"]}
        self.assertEqual(run_ids, {"r_gate_1", "r_gate_2"})

    def test_merge_never_touches_status(self):
        existing = [{
            "fingerprint": "quality:same",
            "pattern": "旧",
            "guidance": "旧指引",
            "count": 1,
            "first_seen": "",
            "last_seen": "",
            "status": "rejected",
        }]
        new = {
            "fingerprint": "quality:same",
            "pattern": "新",
            "guidance": "新指引",
            "count": 1,
            "first_seen": "",
            "last_seen": "",
            "status": "proposed",
        }
        merged = fd.merge_lessons(existing, [new])
        self.assertEqual(merged[0]["status"], "rejected")  # 终态不动（防复活）
        self.assertEqual(merged[0]["pattern"], "新")

    def test_times_use_min_max_semantics(self):
        existing = [{
            "fingerprint": "quality:same",
            "pattern": "p",
            "guidance": "g",
            "count": 1,
            "first_seen": "2026-09-05T10:00:00+00:00",
            "last_seen": "2026-09-05T12:00:00+00:00",
            "status": "proposed",
        }]
        earlier = {
            "fingerprint": "quality:same",
            "pattern": "p2",
            "guidance": "g2",
            "count": 1,
            "first_seen": "2026-09-05T09:00:00+00:00",
            "last_seen": "2026-09-05T11:00:00+00:00",
            "status": "proposed",
        }
        merged = fd.merge_lessons(existing, [earlier])
        self.assertEqual(merged[0]["first_seen"], "2026-09-05T09:00:00+00:00")
        self.assertEqual(merged[0]["last_seen"], "2026-09-05T12:00:00+00:00")

    def test_evidence_refs_union_dedup_capped(self):
        refs_a = [{"run_id": f"r_{i:02d}", "check_type": "compile"} for i in range(25)]
        refs_b = [
            {"run_id": "r_01", "check_type": "compile"},
            {"run_id": "r_50", "check_type": "static"},
        ]
        merged = fd._merge_evidence_refs(refs_a, refs_b)
        run_ids = [ref["run_id"] for ref in merged]
        self.assertEqual(len(merged), 20)
        self.assertEqual(run_ids, sorted(run_ids))
        self.assertEqual(run_ids.count("r_01"), 1)  # 并集去重（refs_a 与 refs_b 重叠）


class TestQualityBackwardCompat(QualityCuratorBase):
    def test_legacy_lesson_without_evidence_refs_readable(self):
        legacy = {
            "fingerprint": "distill:compile:abc123",
            "pattern": "旧教训",
            "guidance": "旧指引",
            "evidence_kinds": ["compile_error"],
            "count": 3,
            "first_seen": "2026-08-01T00:00:00+00:00",
            "last_seen": "2026-08-02T00:00:00+00:00",
            "status": "active",
        }
        (self.project / ".openbrep" / "memory" / "learnings").mkdir(parents=True, exist_ok=True)
        _lesson_file(self.project).write_text(
            json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        lessons = fd.load_lessons(self.project)
        self.assertEqual(len(lessons), 1)
        cards = fd.lesson_cards_view(self.project)
        self.assertEqual(len(cards), 1)
        row = cards[0]
        self.assertEqual(row["fingerprint"], "distill:compile:abc123")
        self.assertIsNone(row["evidence_refs"])  # 旧格式字段缺省 None
        prompt = fd.build_distilled_lessons_prompt(self.project)
        self.assertIn("旧教训", prompt)  # active 旧教训仍可注入渲染

    def test_legacy_merge_without_new_fields_survives(self):
        legacy = {
            "fingerprint": "distill:compile:abc123",
            "pattern": "旧教训",
            "guidance": "旧指引",
            "evidence_kinds": ["compile_error"],
            "count": 3,
            "first_seen": "2026-08-01T00:00:00+00:00",
            "last_seen": "2026-08-02T00:00:00+00:00",
            "status": "proposed",
        }
        new_legacy = dict(legacy)
        new_legacy["count"] = 2
        new_legacy["pattern"] = "新教训"
        merged = fd.merge_lessons([legacy], [new_legacy])
        self.assertEqual(merged[0]["count"], 5)
        self.assertEqual(merged[0]["pattern"], "新教训")
        self.assertEqual(merged[0]["evidence_kinds"], ["compile_error"])
        self.assertNotIn("evidence_refs", merged[0])

    def test_status_machine_works_on_quality_lessons(self):
        self._seed_fail_run()
        llm = FakeLLM(json.dumps([_quality_item()], ensure_ascii=False))
        fd.distill_quality_records(self.project, self.project, llm=llm)
        fingerprint = _read_lessons(self.project)[0]["fingerprint"]
        result = fd.set_lesson_status(self.project, fingerprint, "promote")
        self.assertTrue(result["ok"])
        prompt = fd.build_distilled_lessons_prompt(self.project)
        self.assertIn("书架指令描述不清导致编译失败", prompt)
        fd.set_lesson_status(self.project, fingerprint, "demote")
        after = fd.build_distilled_lessons_prompt(self.project)
        self.assertNotIn("书架指令描述不清导致编译失败", after)


if __name__ == "__main__":
    unittest.main()
