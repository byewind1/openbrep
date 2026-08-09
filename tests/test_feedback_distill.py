"""反馈语义提炼（openbrep/feedback_distill.py + MCP distill_feedback）测试。

覆盖（任务要求）：
- collect：单项目 / 工作区聚合 / 坏行跳过 / 无反馈文件
- 预聚类：签名规范化（路径/数字/引号内容归一）与计数、样本 ≤3、指纹稳定
- watermark 增量：二次运行零增量不建 LLM；LLM 异常不前进 watermark（下次重试）
- LLM 提炼校验：坏 JSON / 形态非法 / 空候选静默；字段限长截断
- 同 fingerprint 合并：count 累加、last_seen 刷新、单行一条
- 红线：proposed 教训不进 build_skill_prompt（防回归）
- MCP 工具契约：ok/trace_id/字段、非法 path 错误形态、可注入 llm
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openbrep import feedback_distill as fd
from openbrep.learning import ErrorLearningStore
from openbrep.llm import LLMResponse
from openbrep.mcp_tools import distill_feedback


def _event(kind: str, summary: str, detail: dict | None = None, ts: str = "2026-08-09T10:00:00+00:00") -> dict:
    event = {"ts": ts, "kind": kind, "project": "Shelf", "summary": summary}
    if detail:
        event["detail"] = detail
    return event


def _write_feedback(project_dir: Path, events: list[dict]) -> Path:
    """追加写入反馈事件（追加模式：多次调用模拟增量采集）。"""
    path = project_dir / ".openbrep" / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _make_project(tmp: Path, name: str = "Shelf") -> Path:
    """构造最小 HSF 项目目录（feedback 采集只认目录，无需完整 HSF）。"""
    project = tmp / name
    (project / ".openbrep").mkdir(parents=True, exist_ok=True)
    return project


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
            raise RuntimeError("llm exploded")
        return LLMResponse(content=self.content, model="fake", usage={}, finish_reason="stop")


def _valid_item(pattern: str = "IF/ENDIF 配对缺失", guidance: str = "逐段核对 IF/ENDIF 配对", count: int = 1) -> dict:
    return {
        "pattern": pattern,
        "guidance": guidance,
        "evidence_kinds": ["compile_failure"],
        "evidence_count": count,
    }


def _items_json(*items: dict) -> str:
    return json.dumps(list(items), ensure_ascii=False)


# ── 1. collect_feedback_events ────────────────────────────

class TestCollectFeedbackEvents(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_single_project_collection(self):
        project = _make_project(self.tmp)
        _write_feedback(project, [
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"}),
            _event("semantic_blocking", "几何为空", {"checks": ["mesh_empty"]}),
        ])
        result = fd.collect_feedback_events(project)
        self.assertEqual(len(result), 1)
        (path, events) = next(iter(result.items()))
        self.assertTrue(path.endswith(".openbrep/feedback.jsonl"))
        self.assertEqual([e["kind"] for _, e in events], ["compile_failure", "semantic_blocking"])
        self.assertEqual([ln for ln, _ in events], [1, 2])

    def test_workspace_aggregation(self):
        ws = self.tmp / "ws"
        p1 = _make_project(ws / "hsf", "P1")
        p2 = _make_project(ws / "hsf", "P2")
        _write_feedback(p1, [_event("compile_failure", "编译失败")])
        _write_feedback(p2, [_event("patch_failure", "匹配 0 次")])
        result = fd.collect_feedback_events(ws)
        self.assertEqual(len(result), 2)
        kinds = sorted(e["kind"] for events in result.values() for _, e in events)
        self.assertEqual(kinds, ["compile_failure", "patch_failure"])

    def test_bad_lines_skipped_with_lineno_preserved(self):
        project = _make_project(self.tmp)
        path = _write_feedback(project, [_event("compile_failure", "编译失败")])
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json}\n")
            fh.write(json.dumps(_event("semantic_blocking", "几何为空"), ensure_ascii=False) + "\n")
            fh.write("plain garbage\n")
            fh.write(json.dumps({"no_kind": True}, ensure_ascii=False) + "\n")
        result = fd.collect_feedback_events(project)
        events = next(iter(result.values()))
        # 坏行跳过不计入；行号保留（watermark 按行数推进时坏行不重复触发）
        self.assertEqual([ln for ln, _ in events], [1, 3])
        self.assertEqual([e["kind"] for _, e in events], ["compile_failure", "semantic_blocking"])

    def test_no_feedback_file_returns_empty(self):
        project = _make_project(self.tmp)
        self.assertEqual(fd.collect_feedback_events(project), {})
        self.assertEqual(fd.collect_feedback_events(self.tmp / "hsf"), {})


# ── 2. 预聚类 ─────────────────────────────────────────────

class TestPrecluster(unittest.TestCase):
    def test_signature_normalization_merges_instances(self):
        """路径/数字/行号/引号内容不同 → 同一签名；kind 不同 → 不同簇。"""
        events = {
            "/a/f.jsonl": [
                (1, _event("compile_failure", "编译失败", {
                    "error": "Error in /tmp/x/3d.gdl at line 12: IF/ENDIF mismatch (IF: 1, ENDIF: 0)",
                }, ts="2026-08-09T10:00:00+00:00")),
                (2, _event("compile_failure", "编译失败", {
                    "error": "Error in /other/y/2d.gdl at line 34: IF/ENDIF mismatch (IF: 2, ENDIF: 1)",
                }, ts="2026-08-09T11:00:00+00:00")),
                (3, _event("semantic_blocking", "几何为空", {
                    "checks": ["mesh_empty"],
                }, ts="2026-08-09T12:00:00+00:00")),
            ],
        }
        clusters = fd.precluster(events)
        self.assertEqual(len(clusters), 2)  # compile_failure 合并为一簇；semantic_blocking 独立
        by_kind = {c["kind"]: c for c in clusters}
        self.assertEqual(by_kind["compile_failure"]["count"], 2)
        self.assertEqual(by_kind["semantic_blocking"]["count"], 1)
        self.assertEqual(by_kind["compile_failure"]["first_seen"], "2026-08-09T10:00:00+00:00")
        self.assertEqual(by_kind["compile_failure"]["last_seen"], "2026-08-09T11:00:00+00:00")

    def test_samples_capped_at_three(self):
        events = {
            "/a/f.jsonl": [
                (i, _event("compile_failure", f"编译失败 {i}", {"error": "IF/ENDIF mismatch"}))
                for i in range(1, 6)
            ],
        }
        clusters = fd.precluster(events)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 5)
        self.assertEqual(len(clusters[0]["samples"]), 3)  # ≤3 条截断样本

    def test_fingerprint_stable_across_runs(self):
        ev1 = {"/a/f.jsonl": [(1, _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"}))]}
        ev2 = {"/b/f.jsonl": [(7, _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"}))]}
        fp1 = fd.precluster(ev1)[0]["fingerprint"]
        fp2 = fd.precluster(ev2)[0]["fingerprint"]
        self.assertEqual(fp1, fp2)
        self.assertTrue(fp1.startswith("distill:compile_failure:"))


# ── 3. watermark 增量 ─────────────────────────────────────

class TestIncrementalDistill(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.ws = self.tmp / "ws"
        self.work_dir = self.ws  # 教训库所在工作区

    def tearDown(self):
        self._td.cleanup()

    def test_zero_increment_does_not_build_llm(self):
        """无反馈事件 / 零增量 → 不建 LLM（传入的 llm 也不被调用）。"""
        llm = FakeLLM(_items_json(_valid_item()))
        result = fd.distill(self.project, work_dir=self.work_dir, llm=llm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 0)
        self.assertEqual(llm.calls, 0)  # 零事件不建 LLM

    def test_second_run_zero_increment_skips_llm(self):
        _write_feedback(self.project, [_event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"})])
        llm = FakeLLM(_items_json(_valid_item()))
        first = fd.distill(self.project, work_dir=self.work_dir, llm=llm)
        self.assertEqual(first["new_lessons"], 1)
        self.assertEqual(llm.calls, 1)

        # 二次运行：无新事件 → 不建 LLM
        second = fd.distill(self.project, work_dir=self.work_dir, llm=llm)
        self.assertEqual(second["new_lessons"], 0)
        self.assertEqual(llm.calls, 1)  # 没有第二次调用
        self.assertEqual(second["total_lessons"], 1)

    def test_incremental_only_processes_new_events(self):
        path = _write_feedback(self.project, [
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"}),
        ])
        llm = FakeLLM(_items_json(_valid_item()))
        fd.distill(self.project, work_dir=self.work_dir, llm=llm)

        # 追加一条新事件（不同 kind）→ 只提炼新事件
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_event("semantic_blocking", "几何为空", {"checks": ["mesh_empty"]}), ensure_ascii=False) + "\n")
        llm2 = FakeLLM(_items_json(_valid_item(pattern="几何为空模式", count=1)))
        result = fd.distill(self.project, work_dir=self.work_dir, llm=llm2)
        self.assertEqual(result["new_lessons"], 1)
        self.assertEqual(llm2.calls, 1)
        # 只收到 1 个聚类（新事件）
        self.assertEqual(len(llm2.last_messages[1]["content"].split("[聚类")), 2)

    def test_llm_exception_does_not_advance_watermark(self):
        _write_feedback(self.project, [_event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"})])
        bad_llm = FakeLLM("", error=True)
        first = fd.distill(self.project, work_dir=self.work_dir, llm=bad_llm)
        self.assertEqual(first["new_lessons"], 0)
        self.assertEqual(first.get("note"), "llm_failed")

        # 下次重试：watermark 未前进 → LLM 再次被调用
        good_llm = FakeLLM(_items_json(_valid_item()))
        second = fd.distill(self.project, work_dir=self.work_dir, llm=good_llm)
        self.assertEqual(second["new_lessons"], 1)
        self.assertEqual(good_llm.calls, 1)


# ── 4. LLM 提炼校验 ───────────────────────────────────────

class TestDistillValidation(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.work_dir = self.tmp / "ws"

    def tearDown(self):
        self._td.cleanup()

    def _seed_event(self):
        _write_feedback(self.project, [_event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"})])

    def _distill_with(self, llm_content: str):
        llm = FakeLLM(llm_content)
        return fd.distill(self.project, work_dir=self.work_dir, llm=llm), llm

    def test_bad_json_silent_empty(self):
        self._seed_event()
        result, llm = self._distill_with("这根本不是 JSON")
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 0)
        self.assertEqual(llm.calls, 1)
        # 事件已处理（watermark 前进）：同一事件不再重试 → 零增量不建 LLM
        result2, llm2 = self._distill_with(_items_json(_valid_item()))
        self.assertEqual(result2["new_lessons"], 0)
        self.assertEqual(llm2.calls, 0)

    def test_shape_violation_silent_empty(self):
        for bad in ("[]", '{"pattern": "x"}', _items_json(_valid_item(), _valid_item())):
            self._seed_event()
            result, _ = self._distill_with(bad)
            self.assertTrue(result["ok"])
            self.assertEqual(result["new_lessons"], 0, f"shape {bad!r} 应静默空")

    def test_empty_candidates_silent_empty_and_watermark_advances(self):
        self._seed_event()
        result, llm = self._distill_with("[]")
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 0)
        self.assertEqual(llm.calls, 1)
        # watermark 已前进：同一事件不再重试 → 零增量不建 LLM
        result2, llm2 = self._distill_with(_items_json(_valid_item()))
        self.assertEqual(result2["new_lessons"], 0)
        self.assertEqual(llm2.calls, 0)

    def test_field_length_limits_truncated(self):
        self._seed_event()
        long_pattern = "长" * 150
        long_guidance = "指" * 400
        result, _ = self._distill_with(_items_json(_valid_item(pattern=long_pattern, guidance=long_guidance)))
        self.assertEqual(result["new_lessons"], 1)
        lessons = fd.load_lessons(self.work_dir)
        self.assertEqual(len(lessons[0]["pattern"]), fd.PATTERN_MAX_CHARS)
        self.assertEqual(len(lessons[0]["guidance"]), fd.GUIDANCE_MAX_CHARS)

    def test_llm_unavailable_silent(self):
        _write_feedback(self.project, [_event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"})])
        with mock.patch.object(fd, "_build_distill_llm", return_value=None):
            result = fd.distill(self.project, work_dir=self.work_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 0)
        self.assertEqual(result.get("note"), "llm_unavailable")
        # watermark 未前进（LLM 未构建成功）→ 下次可重试
        with mock.patch.object(fd, "_build_distill_llm", return_value=None):
            result2 = fd.distill(self.project, work_dir=self.work_dir)
        self.assertEqual(result2.get("note"), "llm_unavailable")
        self.assertEqual(result2["events_seen"], 1)  # 事件仍在等提炼


# ── 5. 同 fingerprint 合并 ────────────────────────────────

class TestMergeLessons(unittest.TestCase):
    def test_same_fingerprint_merges_count_and_last_seen(self):
        new_lessons = [
            {
                "fingerprint": "distill:compile_failure:abc",
                "pattern": "p1", "guidance": "g1",
                "evidence_kinds": ["compile_failure"],
                "count": 2,
                "first_seen": "2026-08-01T00:00:00+00:00",
                "last_seen": "2026-08-02T00:00:00+00:00",
                "status": "proposed",
            },
        ]
        merged = fd.merge_lessons([], new_lessons)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["count"], 2)

        # 同 fingerprint 再来一条：count 累加、last_seen 刷新、first_seen 保留
        more = [
            {
                "fingerprint": "distill:compile_failure:abc",
                "pattern": "p2", "guidance": "g2",
                "evidence_kinds": ["compile_failure"],
                "count": 3,
                "first_seen": "2026-08-03T00:00:00+00:00",
                "last_seen": "2026-08-04T00:00:00+00:00",
                "status": "proposed",
            },
        ]
        merged2 = fd.merge_lessons(merged, more)
        self.assertEqual(len(merged2), 1)  # 单行一条
        self.assertEqual(merged2[0]["count"], 5)
        self.assertEqual(merged2[0]["last_seen"], "2026-08-04T00:00:00+00:00")
        self.assertEqual(merged2[0]["first_seen"], "2026-08-01T00:00:00+00:00")
        self.assertEqual(merged2[0]["pattern"], "p2")  # 新提炼覆盖旧的

    def test_distill_run_merges_into_existing_file(self):
        _write_feedback(self.project_dir(), [
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch (IF: 1, ENDIF: 0)"}),
        ])
        llm1 = FakeLLM(_items_json(_valid_item()))
        fd.distill(self.project_dir(), work_dir=self.work_dir(), llm=llm1)

        # 追加同模式事件（数字不同，签名归一）→ 同 fingerprint 合并而非新增
        _write_feedback(self.project_dir(), [
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch (IF: 3, ENDIF: 2)"}),
        ])
        llm2 = FakeLLM(_items_json(_valid_item()))
        result = fd.distill(self.project_dir(), work_dir=self.work_dir(), llm=llm2)
        self.assertEqual(result["new_lessons"], 1)
        self.assertEqual(result["total_lessons"], 1)  # 合并，不新增行
        lessons = fd.load_lessons(self.work_dir())
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["count"], 2)

    def project_dir(self):
        return self.tmp if not hasattr(self, "_proj") else self._proj

    def work_dir(self):
        return self.tmp / "ws"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self._proj = _make_project(self.tmp)

    def tearDown(self):
        self._td.cleanup()


# ── 6. 红线：proposed 不进 build_skill_prompt ─────────────

class TestNotInjectedIntoPrompt(unittest.TestCase):
    def test_distilled_lessons_never_in_build_skill_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td) / "ws"
            lessons_path = work_dir / ".openbrep" / "memory" / "learnings" / fd.DISTILLED_LESSONS_FILE
            lessons_path.parent.mkdir(parents=True, exist_ok=True)
            canary = "NEVER_INJECT_THIS_PATTERN_7f3a9c"
            canary_guidance = "NEVER_INJECT_THIS_GUIDANCE_9b1e"
            lessons_path.write_text(
                json.dumps({
                    "fingerprint": "distill:compile_failure:abc",
                    "pattern": canary,
                    "guidance": canary_guidance,
                    "evidence_kinds": ["compile_failure"],
                    "count": 3,
                    "first_seen": "2026-08-01T00:00:00+00:00",
                    "last_seen": "2026-08-02T00:00:00+00:00",
                    "status": "proposed",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            store = ErrorLearningStore(work_dir)
            prompt = store.build_skill_prompt()
            self.assertNotIn(canary, prompt)
            self.assertNotIn(canary_guidance, prompt)
            self.assertNotIn(fd.DISTILLED_LESSONS_FILE, prompt)


# ── 7. MCP 工具契约 ───────────────────────────────────────

class TestMcpDistillFeedback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_ok_contract_with_injected_llm(self):
        project = _make_project(self.tmp)
        _write_feedback(project, [
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch (IF: 1, ENDIF: 0)"}),
            _event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch (IF: 2, ENDIF: 1)"}),
        ])
        llm = FakeLLM(_items_json(_valid_item(count=2)))
        result = distill_feedback(str(project), work_dir=str(self.tmp / "ws"), llm=llm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 1)
        self.assertEqual(result["total_lessons"], 1)
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual(result["clusters"][0]["count"], 2)
        self.assertRegex(result["trace_id"], r"^mcp-\d{8}-\d{4}$")

        # 教训落盘在 <work_dir>/.openbrep/memory/learnings/distilled_lessons.jsonl
        lessons = fd.load_lessons(self.tmp / "ws")
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["status"], "proposed")

    def test_invalid_path_returns_unified_error(self):
        result = distill_feedback(str(self.tmp / "ghost"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "project_not_found")
        self.assertRegex(result["trace_id"], r"^mcp-\d{8}-\d{4}$")

    def test_plain_dir_without_feedback_or_workspace_is_invalid(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        result = distill_feedback(str(plain))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "project_not_found")

    def test_workspace_root_accepted_with_default_work_dir(self):
        ws = self.tmp / "ws"
        project = _make_project(ws / "hsf", "P1")
        _write_feedback(project, [_event("compile_failure", "编译失败", {"error": "IF/ENDIF mismatch"})])
        llm = FakeLLM(_items_json(_valid_item()))
        result = distill_feedback(str(ws), llm=llm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_lessons"], 1)
        # work_dir 缺省 = 工作区根：教训库在 ws/.openbrep/memory/learnings/
        lessons = fd.load_lessons(ws)
        self.assertEqual(len(lessons), 1)


if __name__ == "__main__":
    unittest.main()


# ── F3：教训晋升入闸（状态机 + active 注入 + pipeline 闸 + 循环 import 防线） ──

def _seed_lessons(work_dir: Path, lessons: list[dict]) -> Path:
    """写入教训库（用 sort_keys 与 save_lessons 同构；直接写便于构造任意状态）。"""
    path = work_dir / ".openbrep" / "memory" / "learnings" / fd.DISTILLED_LESSONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for lesson in lessons:
            fh.write(json.dumps(lesson, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _lesson(fingerprint: str, status: str = "proposed", count: int = 1, pattern: str = "P") -> dict:
    return {
        "fingerprint": fingerprint,
        "pattern": pattern,
        "guidance": "G",
        "evidence_kinds": ["compile_failure"],
        "count": count,
        "first_seen": "2026-08-01T00:00:00+00:00",
        "last_seen": "2026-08-02T00:00:00+00:00",
        "status": status,
    }


class TestLessonStateMachine(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.work_dir = self.tmp / "ws"

    def tearDown(self):
        self._td.cleanup()

    def test_promote_proposed_to_active_persists(self):
        _seed_lessons(self.work_dir, [_lesson("distill:compile_failure:aa")])
        result = fd.set_lesson_status(self.work_dir, "distill:compile_failure:aa", "promote")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "active")
        self.assertTrue(result["changed"])
        saved = fd.load_lessons(self.work_dir)[0]
        self.assertEqual(saved["status"], "active")
        self.assertIn("status_changed_at", saved)
        self.assertRegex(saved["status_changed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_reject_and_demote_legal(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:aa"),
            _lesson("distill:compile_failure:bb", status="active"),
        ])
        r1 = fd.set_lesson_status(self.work_dir, "distill:compile_failure:aa", "reject")
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["status"], "rejected")
        r2 = fd.set_lesson_status(self.work_dir, "distill:compile_failure:bb", "demote")
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["status"], "proposed")
        by_fp = {lesson["fingerprint"]: lesson for lesson in fd.load_lessons(self.work_dir)}
        self.assertEqual(by_fp["distill:compile_failure:aa"]["status"], "rejected")
        self.assertEqual(by_fp["distill:compile_failure:bb"]["status"], "proposed")

    def test_illegal_transitions_error(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:rej", status="rejected"),
            _lesson("distill:compile_failure:prop"),
            _lesson("distill:compile_failure:act", status="active"),
        ])
        # rejected → active 非法；demote 一个 proposed 非法；reject 一个 active 非法
        bad = [
            ("distill:compile_failure:rej", "promote"),
            ("distill:compile_failure:prop", "demote"),
            ("distill:compile_failure:act", "reject"),
        ]
        for fp, decision in bad:
            result = fd.set_lesson_status(self.work_dir, fp, decision)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_transition")

    def test_unknown_fingerprint_and_bad_decision_error(self):
        result = fd.set_lesson_status(self.work_dir, "ghost", "promote")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "lesson_not_found")
        _seed_lessons(self.work_dir, [_lesson("distill:compile_failure:aa")])
        result2 = fd.set_lesson_status(self.work_dir, "distill:compile_failure:aa", "fly")
        self.assertFalse(result2["ok"])
        self.assertEqual(result2["error"]["code"], "invalid_decision")

    def test_idempotent_promote_active_and_reject_rejected(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:aa", status="active"),
            _lesson("distill:compile_failure:bb", status="rejected"),
        ])
        r1 = fd.set_lesson_status(self.work_dir, "distill:compile_failure:aa", "promote")
        self.assertTrue(r1["ok"])
        self.assertFalse(r1["changed"])
        self.assertEqual(r1["status"], "active")
        r2 = fd.set_lesson_status(self.work_dir, "distill:compile_failure:bb", "reject")
        self.assertTrue(r2["ok"])
        self.assertFalse(r2["changed"])
        self.assertEqual(r2["status"], "rejected")
        # 幂等不写 status_changed_at（保持首次迁移时间）
        saved = fd.load_lessons(self.work_dir)
        self.assertEqual(len(saved), 2)
        self.assertEqual([lesson["status"] for lesson in saved], ["active", "rejected"])

    def test_list_lessons_view_sort_and_filter(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:aa", status="active", count=1),
            _lesson("distill:compile_failure:bb", status="active", count=5),
            _lesson("distill:compile_failure:cc", status="proposed", count=9),
            _lesson("distill:compile_failure:dd", status="rejected", count=2),
        ])
        view = fd.list_lessons_view(self.work_dir)
        # 按 (status, -count, last_seen) 稳定排序
        self.assertEqual([v["status"] for v in view], ["active", "active", "proposed", "rejected"])
        self.assertEqual([v["count"] for v in view[:2]], [5, 1])
        filtered = fd.list_lessons_view(self.work_dir, status="active")
        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(v["status"] == "active" for v in filtered))
        # 视图字段固定（不含 samples/status_changed_at 等内部字段）
        self.assertEqual(
            set(filtered[0].keys()),
            {
                "fingerprint", "pattern", "guidance", "evidence_kinds",
                "count", "status", "first_seen", "last_seen",
            },
        )


class TestRejectedNotRevivedByMerge(unittest.TestCase):
    def test_merge_keeps_rejected_status(self):
        existing = [_lesson("distill:compile_failure:aa", status="rejected", count=2)]
        new_lessons = [_lesson("distill:compile_failure:aa", status="proposed", count=3)]
        merged = fd.merge_lessons(existing, new_lessons)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "rejected")  # 不被复活
        self.assertEqual(merged[0]["count"], 5)            # count 仍累加
        self.assertEqual(merged[0]["pattern"], "P")

    def test_distill_does_not_revive_rejected(self):
        """完整链路：同 fingerprint 被拒绝后再提炼 → 仍是 rejected。"""
        wd = self.tmp / "ws"
        _seed_lessons(wd, [_lesson("distill:compile_failure:aa", status="rejected")])
        new_lessons = [{
            "fingerprint": "distill:compile_failure:aa",
            "pattern": "新提炼",
            "guidance": "新指引",
            "evidence_kinds": ["compile_failure"],
            "count": 2,
            "first_seen": "2026-08-03T00:00:00+00:00",
            "last_seen": "2026-08-04T00:00:00+00:00",
            "status": fd.PROPOSED_STATUS,
        }]
        lessons = fd.merge_lessons(fd.load_lessons(wd), new_lessons)
        fd.save_lessons(wd, lessons)
        saved = fd.load_lessons(wd)[0]
        self.assertEqual(saved["status"], "rejected")
        self.assertEqual(saved["count"], 3)
        self.assertEqual(saved["pattern"], "新提炼")

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


class TestDistilledInjection(unittest.TestCase):
    """active 进 build_skill_prompt；proposed/rejected 不进；层消失/limit/坏文件。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.work_dir = self.tmp / "ws"

    def tearDown(self):
        self._td.cleanup()

    def test_active_injected_proposed_rejected_excluded(self):
        _seed_lessons(self.work_dir, [
            _lesson(
                "distill:compile_failure:aa", status="active",
                pattern="ACTIVE_CANARY_7f", count=3,
            ),
            _lesson("distill:compile_failure:bb", status="proposed", pattern="PROPOSED_CANARY_9a"),
            _lesson("distill:compile_failure:cc", status="rejected", pattern="REJECTED_CANARY_2c"),
        ])
        prompt = ErrorLearningStore(self.work_dir).build_skill_prompt()
        self.assertIn("ACTIVE_CANARY_7f", prompt)
        self.assertIn("workspace_distilled_lessons", prompt)
        self.assertNotIn("PROPOSED_CANARY_9a", prompt)
        self.assertNotIn("REJECTED_CANARY_2c", prompt)

    def test_layer_disappears_when_no_active(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:bb", status="proposed", pattern="PROPOSED_CANARY_9a"),
        ])
        prompt = ErrorLearningStore(self.work_dir).build_skill_prompt()
        self.assertNotIn("workspace_distilled_lessons", prompt)
        self.assertNotIn("PROPOSED_CANARY_9a", prompt)

    def test_build_distilled_prompt_limit_truncation_and_order(self):
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:aa", status="active", pattern="LOW_COUNT", count=1),
            _lesson("distill:compile_failure:bb", status="active", pattern="HIGH_COUNT", count=9),
        ])
        text = fd.build_distilled_lessons_prompt(self.work_dir, limit=1)
        self.assertIn("HIGH_COUNT", text)
        self.assertNotIn("LOW_COUNT", text)
        # 排序：-count → HIGH_COUNT 在前
        text_all = fd.build_distilled_lessons_prompt(self.work_dir, limit=8)
        self.assertLess(text_all.index("HIGH_COUNT"), text_all.index("LOW_COUNT"))

    def test_bad_file_silent_empty(self):
        _seed_lessons(self.work_dir, [_lesson("distill:compile_failure:aa", status="active")])
        path = self.work_dir / ".openbrep" / "memory" / "learnings" / fd.DISTILLED_LESSONS_FILE
        path.write_text("{broken json\n", encoding="utf-8")
        self.assertEqual(fd.build_distilled_lessons_prompt(self.work_dir), "")
        self.assertEqual(fd.list_lessons_view(self.work_dir), [])
        # build_skill_prompt 也不抛（best-effort）
        prompt = ErrorLearningStore(self.work_dir).build_skill_prompt()
        self.assertNotIn("workspace_distilled_lessons", prompt)


class TestPipelineLearnedGate(unittest.TestCase):
    """include_learned_skills=False（benchmark 复现性闸门）时 active 教训不注入。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.work_dir = self.tmp / "ws"
        _seed_lessons(self.work_dir, [
            _lesson(
                "distill:compile_failure:aa", status="active",
                pattern="GATE_CANARY_3f9a", count=5,
            ),
        ])

    def tearDown(self):
        self._td.cleanup()

    def _assemble(self, include_learned_skills: bool):
        from openbrep.config import GDLAgentConfig
        from openbrep.hsf_project import HSFProject
        from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

        pipeline = TaskPipeline(
            config=GDLAgentConfig(),
            trace_dir=str(self.tmp / "traces"),
            include_learned_skills=include_learned_skills,
        )
        project = HSFProject.create_new("Shelf", work_dir=str(self.tmp / "proj"))
        request = TaskRequest(user_input="改一下书架", work_dir=str(self.work_dir), project=project)
        assembled = pipeline._assemble_context(request, project, instruction="改一下书架")
        return assembled.skills_text

    def test_gate_off_blocks_active_lesson(self):
        skills_text = self._assemble(include_learned_skills=False)
        self.assertNotIn("GATE_CANARY_3f9a", skills_text)

    def test_gate_on_injects_active_lesson(self):
        skills_text = self._assemble(include_learned_skills=True)
        self.assertIn("GATE_CANARY_3f9a", skills_text)


class TestNoCircularImport(unittest.TestCase):
    def test_learning_import_does_not_load_feedback_distill(self):
        """防循环 import：learning.py 只在函数内局部 import feedback_distill。"""
        import sys

        saved_fd = sys.modules.pop("openbrep.feedback_distill", None)
        saved_learning = sys.modules.pop("openbrep.learning", None)
        try:
            import importlib
            importlib.import_module("openbrep.learning")
            self.assertNotIn("openbrep.feedback_distill", sys.modules)
        finally:
            if saved_fd is not None:
                sys.modules["openbrep.feedback_distill"] = saved_fd
            if saved_learning is not None:
                sys.modules["openbrep.learning"] = saved_learning


class TestMcpLessonTools(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.work_dir = self.tmp / "ws"
        _seed_lessons(self.work_dir, [
            _lesson("distill:compile_failure:aa", status="proposed", count=3),
            _lesson("distill:compile_failure:bb", status="active", count=1),
            _lesson("distill:compile_failure:cc", status="proposed", count=1),
        ])

    def tearDown(self):
        self._td.cleanup()

    def test_list_lessons_contract(self):
        from openbrep.mcp_tools import list_lessons

        result = list_lessons(work_dir=str(self.work_dir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 3)
        self.assertRegex(result["trace_id"], r"^mcp-\d{8}-\d{4}$")
        by_fp = {lesson["fingerprint"]: lesson for lesson in result["lessons"]}
        self.assertEqual(by_fp["distill:compile_failure:aa"]["status"], "proposed")
        self.assertEqual(by_fp["distill:compile_failure:bb"]["status"], "active")

        filtered = list_lessons(work_dir=str(self.work_dir), status="active")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["lessons"][0]["fingerprint"], "distill:compile_failure:bb")

        bad = list_lessons(work_dir=str(self.work_dir), status="nope")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "invalid_mode")

    def test_promote_lesson_contract(self):
        from openbrep.mcp_tools import promote_lesson

        ok = promote_lesson(
            work_dir=str(self.work_dir),
            fingerprint="distill:compile_failure:aa",
            decision="promote",
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["status"], "active")
        self.assertEqual(ok["decision"], "promote")
        self.assertRegex(ok["trace_id"], r"^mcp-\d{8}-\d{4}$")
        saved = fd.load_lessons(self.work_dir)
        self.assertEqual([lesson["status"] for lesson in saved], ["active", "active", "proposed"])

        missing = promote_lesson(
            work_dir=str(self.work_dir), fingerprint="ghost", decision="promote",
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "lesson_not_found")

        # demote 一个 still-proposed 的教训 = 非法迁移
        illegal = promote_lesson(
            work_dir=str(self.work_dir),
            fingerprint="distill:compile_failure:cc",
            decision="demote",
        )
        self.assertFalse(illegal["ok"])
        self.assertEqual(illegal["error"]["code"], "invalid_transition")

        # active → proposed 撤回是合法迁移
        demote_ok = promote_lesson(
            work_dir=str(self.work_dir),
            fingerprint="distill:compile_failure:bb",
            decision="demote",
        )
        self.assertTrue(demote_ok["ok"])
        self.assertEqual(demote_ok["status"], "proposed")
