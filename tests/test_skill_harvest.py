"""模式级 skill 提案提炼（openbrep/runtime/skill_harvest.py）测试。

覆盖（任务要求）：
- harvest 门禁各分支：intent 限定 / success / 有实际变更 / 编译成功 / 语义无 blocking
- 去重：同名（任何状态）与同 pattern_type（active/verified）不提议
- 坏 JSON / LLM 异常 / 校验不过（含项目名、[FILE: 块、缺触发词小节、非法
  pattern_type / name、content 过短）→ None 静默
- 提案生命周期：pending 存取、epoch 失效、approve 双闸（propose→verify）、
  reject 丢弃、两种结局的 skill_proposal_outcome 反馈事件
- assistant_service 集成：generate 响应带 skill_proposal；stream done 带提案
- workbench_api 路由薄转发（POST /api/skill/confirm）
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openbrep.compiler import CompileResult
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime import skill_harvest
from openbrep.runtime.pipeline import TaskResult
from openbrep.workbench.assistant_service import WorkbenchAssistantService

# ── 公共构造 ──────────────────────────────────────────────

def _make_project(tmp_path: Path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters.append(
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4")
    )
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.save_to_disk()
    return proj


def _ok_compile() -> CompileResult:
    return CompileResult(
        success=True, stdout="ok", stderr="", mode="mock", output_path="/tmp/t.gsm", exit_code=0
    )


def _verified_result(project: HSFProject, intent: str = "MODIFY") -> TaskResult:
    return TaskResult(
        success=True,
        intent=intent,
        scripts={"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\n"},
        plain_text="ok",
        project=project,
        compile_result=_ok_compile(),
        verification={
            "passed": True,
            "checks": [
                {"name": "编译验证", "check_type": "compile", "status": "pass", "detail": "ok"},
                {"name": "语义验证", "check_type": "semantic", "status": "pass",
                 "detail": "无问题"},
            ],
        },
    )


def _proposal_json(**overrides) -> str:
    proposal = {
        "name": "shelf_loop_pattern",
        "pattern_type": "shelf_loop",
        "content": (
            "## 适用场景 / When to Use\n层板或搁架类对象需要按数量均匀分布时。\n\n"
            "## 写法要点\n- 用 FOR 循环遍历层板，间距由总高推出；\n"
            "- 保持 ADD/DEL 变换栈配对；\n- 层板数量作为 Integer 参数暴露。"
        ),
    }
    proposal.update(overrides)
    return json.dumps(proposal, ensure_ascii=False)


class FakeLLM:
    def __init__(self, content: str, error: bool = False):
        self.content = content
        self.error = error
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise RuntimeError("llm exploded")
        return LLMResponse(content=self.content, model="fake", usage={}, finish_reason="stop")


def _read_feedback(project: HSFProject) -> list[dict]:
    path = project.root / ".openbrep" / "feedback.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _make_session(project: HSFProject, **overrides) -> SimpleNamespace:
    kwargs = dict(
        config=None,
        llm_model="fake-model",
        llm_api_key="",
        llm_api_base="",
        project_epoch=1,
        source_path=project.root,
        skill_harvest_enabled=True,
        pending_skill_proposal=None,
    )
    kwargs.update(overrides)
    return SimpleNamespace(**kwargs)


# ── 1. harvest 门禁各分支 ────────────────────────────────

class TestHarvestGate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = str(self.tmp / "skills")

    def tearDown(self):
        self._td.cleanup()

    def harvest(self, result, llm_content=None, instruction="给书架加三层板"):
        llm = FakeLLM(llm_content or _proposal_json())
        proposal = skill_harvest.maybe_harvest(
            result, instruction, self.project, llm, self.skills_dir
        )
        return proposal, llm

    def test_harvests_when_all_gates_pass(self):
        proposal, llm = self.harvest(_verified_result(self.project))
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["name"], "shelf_loop_pattern")
        self.assertEqual(proposal["pattern_type"], "shelf_loop")
        self.assertEqual(llm.calls, 1)
        self.assertIn("When to Use", proposal["content"])

    def test_intent_restricted_to_create_modify(self):
        for intent in ("DEBUG", "REPAIR", "CHAT"):
            result = _verified_result(self.project, intent=intent)
            proposal, llm = self.harvest(result)
            self.assertIsNone(proposal)
            self.assertEqual(llm.calls, 0)

    def test_create_intent_harvests(self):
        proposal, llm = self.harvest(_verified_result(self.project, intent="CREATE"))
        self.assertIsNotNone(proposal)

    def test_unsuccessful_result_skipped(self):
        result = _verified_result(self.project)
        result.success = False
        proposal, llm = self.harvest(result)
        self.assertIsNone(proposal)
        self.assertEqual(llm.calls, 0)

    def test_no_changes_skipped(self):
        result = _verified_result(self.project)
        result.scripts = {}
        proposal, llm = self.harvest(result)
        self.assertIsNone(proposal)
        self.assertEqual(llm.calls, 0)

    def test_compile_not_run_or_failed_skipped(self):
        result = _verified_result(self.project)
        result.compile_result = None
        proposal, llm = self.harvest(result)
        self.assertIsNone(proposal)
        self.assertEqual(llm.calls, 0)

        result.compile_result = CompileResult(
            success=False, stdout="", stderr="bad", mode="mock", output_path=None, exit_code=1
        )
        proposal, llm = self.harvest(result)
        self.assertIsNone(proposal)

    def test_semantic_blocking_skipped(self):
        result = _verified_result(self.project)
        result.verification["checks"].append(
            {"name": "语义验证", "check_type": "semantic", "status": "fail", "detail": "几何为空"}
        )
        proposal, llm = self.harvest(result)
        self.assertIsNone(proposal)
        self.assertEqual(llm.calls, 0)

    def test_harvest_never_raises(self):
        with patch.object(skill_harvest, "_passes_harvest_gate", side_effect=RuntimeError("boom")):
            proposal, _ = self.harvest(_verified_result(self.project))
        self.assertIsNone(proposal)


# ── 2. 去重 ─────────────────────────────────────────────

class TestHarvestDedup(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = Path(self.tmp) / "skills"
        self.skills_dir.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def write_skill(self, name: str, status: str, pattern_type: str = "") -> None:
        (self.skills_dir / f"{name}.md").write_text(
            f"---\nstatus: {status}\nskill_version: 1\npattern_type: {pattern_type}\n---\n\nbody",
            encoding="utf-8",
        )

    def test_same_name_any_status_blocks_proposal(self):
        self.write_skill("shelf_loop_pattern", "proposed", "shelf_loop")
        proposal, llm = self._harvest()
        self.assertIsNone(proposal)
        self.assertEqual(llm.calls, 1)  # 提炼调用已发生，但去重拦截

    def test_same_pattern_type_active_blocks_proposal(self):
        self.write_skill("other_name", "active", "shelf_loop")
        proposal, _ = self._harvest()
        self.assertIsNone(proposal)

    def test_same_pattern_type_proposed_does_not_block(self):
        self.write_skill("other_name", "proposed", "shelf_loop")
        proposal, _ = self._harvest()
        self.assertIsNotNone(proposal)

    def test_no_collision_proposes(self):
        self.write_skill("unrelated", "active", "panel")
        proposal, _ = self._harvest()
        self.assertIsNotNone(proposal)

    def _harvest(self):
        llm = FakeLLM(_proposal_json())
        proposal = skill_harvest.maybe_harvest(
            _verified_result(self.project), "给书架加三层板",
            self.project, llm, str(self.skills_dir)
        )
        return proposal, llm


# ── 3. 提炼解析 / 校验失败静默 ──────────────────────────

class TestHarvestValidation(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = str(self.tmp / "skills")

    def tearDown(self):
        self._td.cleanup()

    def harvest_content(self, content: str):
        llm = FakeLLM(content)
        proposal = skill_harvest.maybe_harvest(
            _verified_result(self.project), "给书架加三层板", self.project, llm, self.skills_dir
        )
        return proposal, llm

    def test_bad_json_silent(self):
        for content in ("", "not json at all", "[1,2]", "``` {broken", '{"name": 1}'):
            proposal, _ = self.harvest_content(content)
            self.assertIsNone(proposal)

    def test_llm_error_silent(self):
        llm = FakeLLM("", error=True)
        proposal = skill_harvest.maybe_harvest(
            _verified_result(self.project), "x", self.project, llm, self.skills_dir
        )
        self.assertIsNone(proposal)

    def test_project_name_in_content_rejected(self):
        content = _proposal_json(content="## 适用场景 / When to Use\nShelf 项目的层板循环做法……")
        proposal, _ = self.harvest_content(content)
        self.assertIsNone(proposal)

    def test_instance_file_block_rejected(self):
        content = _proposal_json(
            content="## 适用场景 / When to Use\n做法如下\n[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX"
        )
        proposal, _ = self.harvest_content(content)
        self.assertIsNone(proposal)

    def test_missing_trigger_section_rejected(self):
        content = _proposal_json(content="没有触发词小节的正文内容，长度也不够……")
        proposal, _ = self.harvest_content(content)
        self.assertIsNone(proposal)

    def test_bad_pattern_type_rejected(self):
        proposal, _ = self.harvest_content(_proposal_json(pattern_type="nonsense"))
        self.assertIsNone(proposal)

    def test_bad_name_rejected(self):
        proposal, _ = self.harvest_content(_proposal_json(name="bad/name"))
        self.assertIsNone(proposal)

    def test_short_content_rejected(self):
        proposal, _ = self.harvest_content(_proposal_json(content="## 适用场景 / When to Use\n短"))
        self.assertIsNone(proposal)

    def test_valid_slice_passed_through(self):
        content = _proposal_json(
            slice={
                "params": {"shelf_count": 4},
                "scripts": {"3d": "FOR i = 1 TO shelf_count\nBLOCK A, B, 0.02\nNEXT i"},
            }
        )
        proposal, _ = self.harvest_content(content)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["slice"]["params"]["shelf_count"], 4)

    def test_bad_slice_shape_rejected(self):
        proposal, _ = self.harvest_content(_proposal_json(slice={"params": {"x": {"value": 1}}}))
        self.assertIsNone(proposal)


# ── 4. 提案生命周期：pending / approve 双闸 / reject / 反馈 ──

class TestProposalLifecycle(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = str(self.tmp / "skills")

    def tearDown(self):
        self._td.cleanup()

    def _harvest_into(self, session, llm_content=None) -> dict | None:
        llm = FakeLLM(llm_content or _proposal_json())
        with patch.object(skill_harvest, "_build_session_llm", return_value=llm):
            return skill_harvest.harvest_for_session(
                session, _verified_result(self.project), "给书架加三层板"
            )

    def test_pending_stored_with_epoch_and_source(self):
        session = _make_session(self.project)
        proposal = self._harvest_into(session)
        self.assertIsNotNone(proposal)
        self.assertEqual(session.pending_skill_proposal["proposal"]["name"], "shelf_loop_pattern")
        self.assertEqual(session.pending_skill_proposal["project_epoch"], 1)
        self.assertEqual(session.pending_skill_proposal["source_path"], self.project.root)

    def test_disabled_session_skips_harvest(self):
        session = _make_session(self.project, skill_harvest_enabled=False)
        proposal = self._harvest_into(session)
        self.assertIsNone(proposal)
        self.assertIsNone(session.pending_skill_proposal)

    def test_approve_proposes_and_runs_double_gate(self):
        """无 slice → structural 门禁：content 含触发词小节即晋升 verified。"""
        session = _make_session(self.project)
        self._harvest_into(session)
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["skill"], "shelf_loop_pattern")
        self.assertEqual(resp["gate"], "structural")
        self.assertTrue(resp["verified"])
        self.assertEqual(resp["status"], "verified")
        self.assertIsNone(session.pending_skill_proposal)
        # propose 落盘并晋升（verified 即可注入）
        target = Path(self.skills_dir) / "shelf_loop_pattern.md"
        self.assertTrue(target.exists())
        self.assertIn("status: verified", target.read_text(encoding="utf-8"))
        # 反馈事件
        events = _read_feedback(self.project)
        so = [
            e for e in events
            if e["kind"] == "skill_proposal_outcome" and e["detail"]["decision"] == "approved"
        ]
        self.assertEqual(len(so), 1)
        self.assertEqual(so[0]["detail"]["name"], "shelf_loop_pattern")
        self.assertTrue(so[0]["detail"]["verified"])

    def test_approve_with_simple_slice_verifies(self):
        session = _make_session(self.project)
        content = _proposal_json(
            name="simple_block_skill",
            pattern_type="panel",
            content=(
                "## 适用场景 / When to Use\n简单箱体对象。\n\n## 写法要点\n"
                "- 用 BLOCK 表达整体边界，保持 A/B/ZZYZX 与声明一致；\n"
                "- 需要中空时用 SUB 减掉内部体；\n- 各向尺寸通过参数暴露。"
            ),
            slice={"params": {}, "scripts": {"3d": "BLOCK A, B, ZZYZX\n"}},
        )
        self._harvest_into(session, llm_content=content)
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["verified"])
        self.assertEqual(resp["status"], "verified")
        # 晋升后 loader 应注入（active/verified）
        from openbrep.skills_loader import SkillsLoader
        loader = SkillsLoader(self.skills_dir)
        loader.load()
        self.assertIn("simple_block_skill", loader.skill_names)
        self.assertEqual(loader.skill_meta("simple_block_skill")["status"], "verified")

    def test_approve_with_failing_slice_stays_proposed(self):
        """带 slice → full 门禁：slice 编译/语义不过时 status 保持 proposed（双闸如实）。"""
        session = _make_session(self.project)
        content = _proposal_json(
            name="failing_slice_skill",
            pattern_type="shelf_loop",
            content=(
                "## 适用场景 / When to Use\n层板循环对象。\n\n## 写法要点\n"
                "- FOR 循环遍历层板，间距由总高推出；\n"
                "- 每次迭代 ADDZ 上移并保持 ADD/DEL 配对；\n"
                "- 层板数量与厚度都作为参数暴露。"
            ),
            slice={
                "params": {"shelf_count": 4},
                "scripts": {
                    "3d": (
                        "FOR i = 1 TO shelf_count\nADDZ shelf_h\n"
                        "BLOCK A, B, 0.02\nDEL 1\nNEXT i\n"
                    )
                },
            },
        )
        self._harvest_into(session, llm_content=content)
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["gate"], "full")
        self.assertFalse(resp["verified"])
        self.assertEqual(resp["status"], "proposed")
        target = Path(self.skills_dir) / "failing_slice_skill.md"
        self.assertTrue(target.exists())
        self.assertIn("status: proposed", target.read_text(encoding="utf-8"))

    def test_reject_discards_and_writes_feedback(self):
        session = _make_session(self.project)
        self._harvest_into(session)
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": False})
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["discarded"])
        self.assertIsNone(session.pending_skill_proposal)
        self.assertFalse((Path(self.skills_dir) / "shelf_loop_pattern.md").exists())
        events = _read_feedback(self.project)
        so = [
            e for e in events
            if e["kind"] == "skill_proposal_outcome" and e["detail"]["decision"] == "rejected"
        ]
        self.assertEqual(len(so), 1)
        self.assertEqual(so[0]["detail"]["name"], "shelf_loop_pattern")

    def test_no_pending_returns_error_code(self):
        session = _make_session(self.project)
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], "NO_PENDING_SKILL_PROPOSAL")

    def test_epoch_mismatch_invalidates(self):
        session = _make_session(self.project, project_epoch=2)
        self._harvest_into(session)
        session.project_epoch = 3  # 项目切换
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], "NO_PENDING_SKILL_PROPOSAL")
        self.assertIsNone(session.pending_skill_proposal)

    def test_propose_failure_reported_and_feedback_written(self):
        session = _make_session(self.project)
        self._harvest_into(session)
        # 预置同名文件 → propose_skill 返回 skill_exists
        (Path(self.skills_dir) / "shelf_loop_pattern.md").parent.mkdir(parents=True, exist_ok=True)
        (Path(self.skills_dir) / "shelf_loop_pattern.md").write_text("x", encoding="utf-8")
        with patch.object(skill_harvest, "resolve_skills_dir", return_value=self.skills_dir):
            resp = skill_harvest.confirm_skill_proposal(session, {"approve": True})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], "SKILL_PROPOSE_FAILED")
        events = _read_feedback(self.project)
        so = [e for e in events if e["kind"] == "skill_proposal_outcome"]
        self.assertTrue(any("propose_error" in e["detail"] for e in so))


# ── 5. assistant_service 集成 ───────────────────────────

class TestAssistantServiceIntegration(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def _session(self, pipeline_result: TaskResult):
        class _FakePipeline:
            def __init__(self, *a, **k):
                pass

            def execute(self, request):
                return pipeline_result

        return SimpleNamespace(
            source_path=self.project.root,
            project=self.project,
            project_epoch=1,
            pipeline_class=_FakePipeline,
            llm_model="mock",
            llm_api_key="",
            llm_api_base="",
            assistant_settings="",
            max_retries=5,
            pending_plan=None,
            pending_skill_proposal=None,
            skill_harvest_enabled=True,
        )

    def test_generate_response_carries_skill_proposal(self):
        result = _verified_result(self.project)
        session = self._session(result)
        proposal = json.loads(_proposal_json())
        with patch(
            "openbrep.workbench.assistant_service.WorkbenchAssistantService._harvest_skill_proposal",
            return_value=proposal,
        ):
            service = WorkbenchAssistantService(session)
            response = service.generate_with_assistant({"message": "给书架加三层板"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["skill_proposal"]["name"], "shelf_loop_pattern")

    def test_generate_response_omits_skill_proposal_when_none(self):
        session = self._session(_verified_result(self.project))
        with patch(
            "openbrep.workbench.assistant_service.WorkbenchAssistantService._harvest_skill_proposal",
            return_value=None,
        ):
            service = WorkbenchAssistantService(session)
            response = service.generate_with_assistant({"message": "给书架加三层板"})
        self.assertTrue(response["ok"])
        self.assertNotIn("skill_proposal", response)

    def test_harvest_failure_does_not_break_delivery(self):
        """提炼抛异常（极端）也要照常交付修改结果。"""
        session = self._session(_verified_result(self.project))
        with patch(
            "openbrep.workbench.assistant_service.WorkbenchAssistantService._harvest_skill_proposal",
            side_effect=RuntimeError("harvest exploded"),
        ):
            service = WorkbenchAssistantService(session)
            response = service.generate_with_assistant({"message": "给书架加三层板"})
        self.assertTrue(response["ok"])
        self.assertNotIn("skill_proposal", response)
        self.assertEqual(response["assistant"]["intent"], "MODIFY")

    def test_stream_done_carries_skill_proposal(self):
        result = _verified_result(self.project)
        session = self._session(result)
        proposal = json.loads(_proposal_json())
        with patch(
            "openbrep.workbench.assistant_service.WorkbenchAssistantService._harvest_skill_proposal",
            return_value=proposal,
        ):
            service = WorkbenchAssistantService(session)
            events = list(
                service.generate_with_assistant_stream({"message": "修改", "stream": True})
            )
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["data"]["skill_proposal"]["name"], "shelf_loop_pattern")

    def test_confirm_route_thin_forward(self):
        from openbrep.workbench_api import WorkbenchSession

        session = WorkbenchSession()
        session.skill_harvest_enabled = False
        session.pending_skill_proposal = {
            "proposal": {"name": "x", "pattern_type": "panel", "content": "c"},
            "project_epoch": 0,
            "source_path": self.project.root,
        }
        session.project_epoch = 1  # 与 pending 代次不同 → 失效
        resp = session.route("POST", "/api/skill/confirm", {"approve": True})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], "NO_PENDING_SKILL_PROPOSAL")


if __name__ == "__main__":
    unittest.main()
