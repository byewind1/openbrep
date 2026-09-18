"""ST04 显式 skill 沉淀候选（openbrep/workbench/skill_proposal_service.py）测试。

覆盖：
- 显式请求路由判定（正例 + "解释这个 skill" / "不用保存 skill" / "修改楼梯" 负例）；
- K01：候选 ID/路径/draft 状态可见；源码指纹不变；不走修改/编译工具；
- K02：同内容两次 + 重启 session 后列出 = 单一持久候选，内容与证据一致；
- K03/K06：未审批/已拒绝候选绝不进 SkillsLoader；
- K04：批准 + verify 通过走既有晋升；verify 失败保留未激活产物与失败原因；
- K05：store 落盘失败、LLM 无效 JSON、skills_dir 不可写都是明确失败且可重试；
- K06：跨项目审批被拒；
- K07：无 proposal_id 的旧审批入口保持自动 harvest 行为；
- K08：旧资料无质量档案 → revision=null 且 evidence_complete=false。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.quality.schema import QualityRecord
from openbrep.quality.store import write_record
from openbrep.skills_loader import SkillsLoader
from openbrep.skill_proposals import (
    candidate_path,
    detect_explicit_skill_request,
    list_candidates,
    load_candidate,
    path_hash,
)
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench.skill_proposal_service import SkillProposalService
from openbrep.workbench.assistant_service import WorkbenchAssistantService

# ── 公共构造 ──────────────────────────────────────────────


def _make_project(tmp_path: Path, name: str = "SpiralStair") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters.append(
        GDLParameter(name="step_count", type_tag="Integer", description="总步数", value="16")
    )
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nFOR i = 1 TO step_count\nNEXT i\nEND\n"
    proj.save_to_disk()
    return proj


def _proposal_json(**overrides) -> str:
    proposal = {
        "name": "spiral_stair_stack",
        "pattern_type": "repeating_geometry",
        "content": (
            "## 适用场景 / When to Use\n螺旋楼梯踏步需要按总步数参数化堆叠时。\n\n"
            "## 写法要点\n- 用 FOR 循环按 riser_h 堆叠踏步；\n"
            "- 用 ROT 绕中心柱均分旋转角；\n- 总步数作为 Integer 参数暴露。"
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


def _write_quality_run(project: HSFProject, run_id: str = "r_2026_test", *, with_delivery: bool = True) -> str:
    """写一条带真实 after revision 的质量档案；返回 revision id（无交付时为空串）。

    ST04 返工后 source_ref 校验要求 revision 目录存在且实际指纹与记录一致，
    这里用 create_revision 造真快照 + compute_revision_fingerprint 取真指纹。
    """
    revision_id = ""
    delivery = None
    after = None
    fingerprint = None
    if with_delivery:
        from openbrep.revisions import create_revision
        from openbrep.source_fingerprint import compute_revision_fingerprint

        revision = create_revision(project.root, message="test delivery", trigger="manual")
        revision_id = revision.revision_id
        after = revision_id
        fingerprint = compute_revision_fingerprint(
            project.root / ".openbrep" / "revisions" / revision_id
        )
        delivery = {
            "schema_version": 1,
            "run_id": run_id,
            "state": "verified_change",
            "before_revision_id": None,
            "after_revision_id": after,
            "source_fingerprint": fingerprint,
            "changed_files": ["scripts/3d.gdl"],
            "snapshot_status": "saved",
        }
    write_record(
        project.root,
        QualityRecord(
            run_id=run_id,
            intent="MODIFY",
            outcome="completed",
            project_ref={"path_hash": path_hash(project.root), "name": project.name},
            instruction_summary="把螺旋楼梯改成参数化堆叠",
            ts="2026-09-18T00:00:00+00:00",
            provenance={
                "after_revision": after,
                "delivery_source": delivery,
                "source_fingerprint": fingerprint,
            },
        ),
    )
    return revision_id


def _make_session(project: HSFProject | None, **overrides) -> SimpleNamespace:
    kwargs = dict(
        config=None,
        llm_model="fake-model",
        llm_api_key="",
        llm_api_base="",
        project_epoch=1,
        source_path=project.root if project is not None else None,
        project=project,
        skill_harvest_enabled=True,
        pending_skill_proposal=None,
    )
    kwargs.update(overrides)
    return SimpleNamespace(**kwargs)


class _ServiceCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        self.session = _make_session(self.project)

    def tearDown(self):
        self._td.cleanup()

    def propose(self, llm_content=None, body=None, llm=None):
        fake = llm or FakeLLM(llm_content if llm_content is not None else _proposal_json())
        with patch("openbrep.runtime.skill_harvest._build_session_llm", return_value=fake), patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            service = SkillProposalService(self.session)
            result = service.propose(body or {"instruction": "把这轮修改沉淀成楼梯 skill"})
        return result, fake


# ── 1. 路由判定（item 1） ─────────────────────────────────


class TestExplicitSkillRouting(unittest.TestCase):
    def test_positive_requests(self):
        for message in (
            "把这轮修改沉淀成楼梯skill",
            "把这轮修改沉淀成楼梯 skill",
            "保存为技能",
            "把这个模式沉淀为 skill",
            "把刚才的写法提炼成技能",
            "save this as a skill",
        ):
            self.assertTrue(detect_explicit_skill_request(message), message)

    def test_negative_requests(self):
        for message in (
            "解释这个skill",
            "解释一下这个技能是怎么工作的",
            "不用保存skill",
            "不要保存为技能",
            "不用沉淀了",
            "修改楼梯",
            "把楼梯高度改成 3000",
            "don't save this as a skill",
            # 疑问句是询问，不是执行命令（返工 Minor）
            "为什么要保存为技能？",
            "如何保存为技能？",
            "怎么把这轮修改沉淀成技能？",
            "要不要保存为 skill",
            "why should i save this as a skill?",
            "how do i save this as a skill?",
        ):
            self.assertFalse(detect_explicit_skill_request(message), message)


# ── 2. 候选持久化（K01/K02/K08） ──────────────────────────


class TestProposePersistence(_ServiceCase):
    def test_k01_candidate_visible_and_source_unchanged(self):
        revision_id = _write_quality_run(self.project)
        before = compute_source_fingerprint(self.project.root)
        result, llm = self.propose()
        after = compute_source_fingerprint(self.project.root)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["proposal_id"].startswith("sp_"))
        self.assertEqual(result["status"], "draft")
        self.assertTrue(candidate_path(self.project.root, result["proposal_id"]).is_file())
        self.assertEqual(result["evidence"]["source_run_ids"], ["r_2026_test"])
        self.assertEqual(result["evidence"]["revisions"], [revision_id])
        self.assertTrue(result["evidence"]["evidence_complete"])
        self.assertEqual(llm.calls, 1)
        self.assertEqual(before, after)  # 源码哈希不变（未走修改/编译工具）

    def test_source_ref_with_mismatched_fingerprint_is_incomplete(self):
        """source_ref 严格校验：revision 实际指纹与记录不符 → evidence_complete=false。"""
        revision_id = _write_quality_run(self.project)
        record_file = (
            self.project.root / ".openbrep" / "quality" / "runs" / "r_2026_test.json"
        )
        data = json.loads(record_file.read_text(encoding="utf-8"))
        data["provenance"]["delivery_source"]["source_fingerprint"] = "sha256:" + "b" * 64
        record_file.write_text(json.dumps(data), encoding="utf-8")
        result, _ = self.propose()
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["evidence"]["evidence_complete"])
        self.assertIn("revision_fingerprint_mismatch", result["evidence"]["validation_reasons"])
        stored = load_candidate(self.project.root, result["proposal_id"])
        self.assertIn("revision_fingerprint_mismatch", stored["source_refs"][0]["validation"]["reasons"])
        self.assertTrue(revision_id)

    def test_source_ref_missing_revision_dir_is_incomplete(self):
        _write_quality_run(self.project)
        record_file = (
            self.project.root / ".openbrep" / "quality" / "runs" / "r_2026_test.json"
        )
        data = json.loads(record_file.read_text(encoding="utf-8"))
        data["provenance"]["delivery_source"]["after_revision_id"] = "r9999"
        data["provenance"]["after_revision"] = "r9999"
        record_file.write_text(json.dumps(data), encoding="utf-8")
        result, _ = self.propose()
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["evidence"]["evidence_complete"])
        self.assertIn("revision_dir_missing", result["evidence"]["validation_reasons"])

    def test_k02_same_content_twice_single_candidate_and_survives_restart(self):
        _write_quality_run(self.project)
        first, _ = self.propose()
        second, llm2 = self.propose()
        self.assertTrue(second["ok"])
        self.assertEqual(first["proposal_id"], second["proposal_id"])
        self.assertTrue(second.get("reused"))
        self.assertEqual(len(list_candidates(self.project.root)), 1)
        self.assertEqual(llm2.calls, 1)  # 第二次仍提炼（指纹比对在提炼之后），但不落新候选

        # 模拟重启：同一项目路径新 session + 新 service
        restarted = _make_session(HSFProject.load_from_disk(str(self.project.root)))
        listed = SkillProposalService(restarted).list_proposals()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["proposals"][0]["proposal_id"], first["proposal_id"])
        self.assertEqual(listed["proposals"][0]["content"], first["content"])

    def test_k08_old_material_revision_null_evidence_incomplete(self):
        result, _ = self.propose()  # 没有任何质量档案
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["evidence"]["source_run_ids"] or None)
        self.assertEqual(result["evidence"]["evidence_complete"], False)
        stored = load_candidate(self.project.root, result["proposal_id"])
        self.assertEqual(stored["source_refs"], [])
        self.assertFalse(stored["evidence_complete"])

    def test_k05_invalid_json_is_explicit_failure(self):
        result, _ = self.propose(llm_content="这不是 JSON")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "SKILL_PROPOSAL_INVALID_JSON")
        self.assertTrue(result.get("retryable"))
        self.assertEqual(list_candidates(self.project.root), [])

    def test_k05_store_write_failure_is_retryable(self):
        with patch(
            "openbrep.workbench.skill_proposal_service.save_candidate",
            side_effect=OSError("disk full"),
        ):
            result, _ = self.propose()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "SKILL_PROPOSAL_STORE_FAILED")
        self.assertTrue(result.get("retryable"))

    def test_no_project_rejected(self):
        self.session.source_path = None
        self.session.project = None
        result, _ = self.propose()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NO_PROJECT")


# ── 3. 审批（K03–K07） ────────────────────────────────────


class TestConfirm(_ServiceCase):
    def _proposal(self):
        _write_quality_run(self.project)
        result, _ = self.propose()
        self.assertTrue(result["ok"], result)
        return result

    def test_k03_candidate_not_injected_before_approval(self):
        proposal = self._proposal()
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertNotIn("spiral_stair_stack", loader.skill_names)
        self.assertEqual(list(self.skills_dir.glob("*.md")), [])
        self.assertEqual(proposal["status"], "draft")

    def test_k04_approve_with_verify_pass_promotes(self):
        proposal = self._proposal()
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            result = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": True}
            )
        self.assertTrue(result["ok"], result)
        self.assertIs(result["verified"], True)
        self.assertEqual(result["gate"], "structural")
        stored = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(stored["status"], "approved")
        self.assertEqual(stored["verification"]["state"], "verified")
        # 既有晋升：verified 文件可被 SkillsLoader 注入
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("spiral_stair_stack").get("status"), "verified")

    def test_k04_verify_failure_keeps_inactive_artifact_and_reason(self):
        proposal = self._proposal()
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ), patch(
            "openbrep.mcp_tools.verify_skill",
            return_value={"ok": True, "passed": False, "gate": "full", "status": "proposed"},
        ):
            result = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": True}
            )
        self.assertTrue(result["ok"], result)
        self.assertIs(result["verified"], False)
        self.assertIn("未激活", result["message"])
        stored = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(stored["status"], "approved")
        self.assertEqual(stored["verification"]["state"], "failed")
        # 产物保留但未激活：SkillsLoader 不注入 proposed
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("spiral_stair_stack").get("status"), "proposed")
        self.assertIsNone(loader.get_by_name("spiral_stair_stack"))

    def test_k04_confirm_is_idempotent_after_decision(self):
        proposal = self._proposal()
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            service = SkillProposalService(self.session)
            first = service.confirm({"proposal_id": proposal["proposal_id"], "approve": True})
            second = service.confirm({"proposal_id": proposal["proposal_id"], "approve": True})
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second.get("already_decided"))

    def test_k05_skills_dir_unwritable_keeps_draft_retryable(self):
        proposal = self._proposal()
        with patch(
            "openbrep.workbench.skill_proposal_service._skills_dir_writable",
            return_value=(False, "skills 目录不可写：/read-only"),
        ):
            result = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": True}
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "SKILL_PROPOSAL_SKILLS_DIR_UNWRITABLE")
        stored = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(stored["status"], "draft")
        self.assertIn("不可写", stored["error"])

    def test_k06_cross_project_confirm_rejected(self):
        proposal = self._proposal()
        other_tmp = self.tmp / "other"
        other_tmp.mkdir()
        other = _make_project(other_tmp, name="OtherProject")
        # 1) 切项目后按 ID 找不到候选（store 是项目级的）→ 拒绝
        self.session.source_path = other.root
        self.session.project = other
        absent = SkillProposalService(self.session).confirm(
            {"proposal_id": proposal["proposal_id"], "approve": True}
        )
        self.assertFalse(absent["ok"])
        self.assertEqual(absent["code"], "SKILL_PROPOSAL_NOT_FOUND")
        # 2) 候选中转/被复制到另一项目目录时，身份校验必须拒绝套用
        import shutil

        source_file = candidate_path(self.project.root, proposal["proposal_id"])
        target_file = candidate_path(other.root, proposal["proposal_id"])
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        mismatch = SkillProposalService(self.session).confirm(
            {"proposal_id": proposal["proposal_id"], "approve": True}
        )
        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["code"], "SKILL_PROPOSAL_PROJECT_MISMATCH")

    def test_k06_reject_marks_rejected_and_never_injects(self):
        proposal = self._proposal()
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            result = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": False}
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["discarded"])
        stored = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(list(self.skills_dir.glob("*.md")), [])
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_names, [])
        # 拒绝后重复审批不再执行
        again = SkillProposalService(self.session).confirm(
            {"proposal_id": proposal["proposal_id"], "approve": True}
        )
        self.assertFalse(again["ok"])
        self.assertEqual(again["code"], "SKILL_PROPOSAL_ALREADY_REJECTED")

    def test_k06_unknown_proposal_id(self):
        result = SkillProposalService(self.session).confirm(
            {"proposal_id": "sp_nope", "approve": True}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "SKILL_PROPOSAL_NOT_FOUND")

    def test_k07_legacy_confirm_without_proposal_id_still_works(self):
        self.session.pending_skill_proposal = {
            "proposal": {"name": "legacy_skill", "pattern_type": "shelf_loop", "content": "x"},
            "project_epoch": 1,
            "source_path": self.project.root,
        }
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            result = SkillProposalService(self.session).confirm({"approve": False})
        self.assertTrue(result["ok"])
        self.assertTrue(result["discarded"])
        self.assertIsNone(self.session.pending_skill_proposal)


# ── 4. assistant 文本路由（item 1/2：复用同一 service） ────


class TestAssistantExplicitRoute(_ServiceCase):
    def test_explicit_request_proposes_without_pipeline(self):
        with patch(
            "openbrep.runtime.skill_harvest._build_session_llm",
            return_value=FakeLLM(_proposal_json()),
        ), patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            service = WorkbenchAssistantService(self.session)
            result = service.generate_with_assistant({"message": "把这轮修改沉淀成楼梯skill"})
        self.assertTrue(result["ok"], result)
        proposal = result["skill_proposal"]
        self.assertEqual(proposal["status"], "draft")
        self.assertIn(proposal["proposal_id"], result["assistant"]["reply"])
        self.assertIn("待审", result["assistant"]["reply"])

    def test_negative_route_does_not_propose(self):
        calls = {"n": 0}

        def _boom(*args, **kwargs):
            calls["n"] += 1
            raise AssertionError("proposal path must not be used")

        self.session.source_path = None  # 无项目 → 正常修改路径早退，不构造 pipeline
        with patch.object(WorkbenchAssistantService, "propose_skill_candidate", side_effect=_boom):
            service = WorkbenchAssistantService(self.session)
            result = service.generate_with_assistant({"message": "修改楼梯"})
        self.assertEqual(calls["n"], 0)
        self.assertNotIn("skill_proposal", result)

    def test_explain_skill_is_not_proposal(self):
        with patch.object(WorkbenchAssistantService, "propose_skill_candidate") as mocked:
            service = WorkbenchAssistantService(self.session)
            result = service.assistant_reply({"message": "解释这个skill"})
        mocked.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["assistant"]["kind"], "skill_proposal")


if __name__ == "__main__":
    unittest.main()


# ── 5. 路由与 request_gate（item 2） ─────────────────────


class TestRouteAndGate(_ServiceCase):
    def test_proposal_routes_use_default_locked_gate(self):
        from openbrep.workbench.request_gate import is_lock_free_route

        # 新 POST 路由必须默认锁定（不加入 LOCK_FREE_POST_ROUTES）
        self.assertFalse(is_lock_free_route("POST", "/api/skill/proposals"))
        self.assertFalse(is_lock_free_route("POST", "/api/skill/confirm"))
        # GET 列表天然只读
        self.assertTrue(is_lock_free_route("GET", "/api/skill/proposals"))

    def test_service_route_dispatch(self):
        _write_quality_run(self.project)
        service = SkillProposalService(self.session)
        with patch(
            "openbrep.runtime.skill_harvest._build_session_llm",
            return_value=FakeLLM(_proposal_json()),
        ), patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            created = service.route("POST", "/api/skill/proposals", {"instruction": "沉淀成 skill"})
        self.assertTrue(created["ok"])

        listed = service.route("GET", "/api/skill/proposals", None)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["total"], 1)

        unknown = service.route("POST", "/api/nope", {})
        self.assertFalse(unknown["ok"])

        # legacy 无 proposal_id 走旧 pending 行为
        self.session.pending_skill_proposal = None
        legacy = service.route("POST", "/api/skill/confirm", {"approve": True})
        self.assertFalse(legacy["ok"])
        self.assertEqual(legacy["code"], "NO_PENDING_SKILL_PROPOSAL")


# ── 6. 审批状态机 / 所有权 / 故障注入（返工） ─────────────


class TestApprovalStateMachine(_ServiceCase):
    def _proposal(self, content_json: str | None = None):
        _write_quality_run(self.project)
        result, _ = self.propose(llm_content=content_json)
        self.assertTrue(result["ok"], result)
        return result

    def _approve(self, proposal_id, *, approve=True):
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            return SkillProposalService(self.session).confirm(
                {"proposal_id": proposal_id, "approve": approve}
            )

    def test_final_state_save_failure_is_retryable_and_converges(self):
        """K04/K05 返工：verify 后候选最终写盘失败 → retryable，重试同一 ID 收敛。"""
        import openbrep.workbench.skill_proposal_service as svc

        proposal = self._proposal()
        real_save = svc.save_candidate

        def flaky_save(root, candidate):
            if str(candidate.get("status")) == "approved":
                raise OSError("disk full")
            return real_save(root, candidate)

        with patch.object(svc, "save_candidate", side_effect=flaky_save):
            failed = self._approve(proposal["proposal_id"])
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["code"], "SKILL_PROPOSAL_STATE_SAVE_FAILED")
        self.assertTrue(failed["retryable"])
        self.assertIs(failed["verified"], True)

        # 磁盘候选停在 approving（没有伪成功），但 skill 已 verified（副作用真实存在）
        on_disk = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(on_disk["status"], "approving")
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("spiral_stair_stack").get("status"), "verified")

        # 重试收敛：同一 proposal_id，不重复创建，最终 approved/verified
        retried = self._approve(proposal["proposal_id"])
        self.assertTrue(retried["ok"], retried)
        self.assertIs(retried["verified"], True)
        converged = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(converged["status"], "approved")
        self.assertEqual(converged["verification"]["state"], "verified")
        self.assertEqual(len(list(self.skills_dir.glob("*.md"))), 1)

    def test_foreign_same_name_file_is_not_overwritten(self):
        proposal = self._proposal()
        foreign = self.skills_dir / "spiral_stair_stack.md"
        foreign.write_text(
            "---\nstatus: active\nsource_trace_id: someone-else\n---\n\n别人的内容\n",
            encoding="utf-8",
        )
        result = self._approve(proposal["proposal_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "SKILL_PROPOSAL_NAME_CONFLICT")
        self.assertIn("别人的内容", foreign.read_text(encoding="utf-8"))
        stored = load_candidate(self.project.root, proposal["proposal_id"])
        self.assertEqual(stored["status"], "draft")

    def test_reject_reclaims_owned_verified_artifact(self):
        """最终写盘失败留下 approving + verified 产物时，拒绝必须回收该产物。"""
        import openbrep.workbench.skill_proposal_service as svc

        proposal = self._proposal()
        real_save = svc.save_candidate

        def flaky_save(root, candidate):
            if str(candidate.get("status")) == "approved":
                raise OSError("disk full")
            return real_save(root, candidate)

        with patch.object(svc, "save_candidate", side_effect=flaky_save):
            self._approve(proposal["proposal_id"])

        rejected = self._approve(proposal["proposal_id"], approve=False)
        self.assertTrue(rejected["ok"], rejected)
        self.assertTrue(rejected["discarded"])
        self.assertTrue(rejected["reclaimed"]["reclaimed"])
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("spiral_stair_stack").get("status"), "deprecated")
        self.assertIsNone(loader.get_by_name("spiral_stair_stack"))

    def test_reject_does_not_touch_foreign_artifact(self):
        proposal = self._proposal()
        foreign = self.skills_dir / "spiral_stair_stack.md"
        foreign.write_text(
            "---\nstatus: active\nsource_trace_id: someone-else\n---\n\n别人的内容\n",
            encoding="utf-8",
        )
        result = self._approve(proposal["proposal_id"], approve=False)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["reclaimed"]["reclaimed"])
        self.assertEqual(result["reclaimed"]["reason"], "foreign_artifact")
        self.assertIn("status: active", foreign.read_text(encoding="utf-8"))


class TestClaimsGuard(_ServiceCase):
    def test_structural_promotion_capped_when_unverified_claims(self):
        _write_quality_run(self.project)
        content = json.dumps(
            {
                "name": "stair_spacing_rule",
                "pattern_type": "repeating_geometry",
                "content": (
                    "## 适用场景 / When to Use\n楼梯踏步排布。\n\n"
                    "## 经验取值\n- 踏步高 25mm 经验值，进深 50mm 经验值；\n"
                    "- PRISM_ 自述为本实现推荐做法。"
                ),
            },
            ensure_ascii=False,
        )
        result, _ = self.propose(llm_content=content)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["claims"]["unverified"], result["claims"])
        self.assertEqual(result["claims"]["project_selection"]["project"], "SpiralStair")

        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            approved = SkillProposalService(self.session).confirm(
                {"proposal_id": result["proposal_id"], "approve": True}
            )
        self.assertTrue(approved["ok"], approved)
        self.assertIs(approved["verified"], False)
        self.assertEqual(approved["gate"], "structural_capped")
        self.assertEqual(approved["verification"]["state"], "claims_unverified")
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("stair_spacing_rule").get("status"), "proposed")
        self.assertIsNone(loader.get_by_name("stair_spacing_rule"))

    def test_claims_recorded_in_payload_evidence(self):
        _write_quality_run(self.project)
        content = json.dumps(
            {
                "name": "spacing_rule",
                "pattern_type": "repeating_geometry",
                "content": (
                    "## 适用场景 / When to Use\n踏步与层板排布模式复用。\n\n"
                    "## 经验取值\n- 间距 50mm 推荐值，来自本项目实测；\n"
                    "- 同时保留参数化入口，避免硬编码尺寸。"
                ),
            },
            ensure_ascii=False,
        )
        result, _ = self.propose(llm_content=content)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["evidence"]["project_selection"]["project"], "SpiralStair")
        kinds = {claim["kind"] for claim in result["claims"]["unverified"]}
        self.assertIn("measurement_experience", kinds)


class TestExplicitStreamRoute(_ServiceCase):
    def test_stream_explicit_request_is_processed_before_generator_iteration(self):
        """返工：显式沉淀在返回生成器之前同步完成（持 request_gate 锁）。"""
        with patch(
            "openbrep.runtime.skill_harvest._build_session_llm",
            return_value=FakeLLM(_proposal_json()),
        ), patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            service = WorkbenchAssistantService(self.session)
            stream = service.generate_with_assistant_route(
                {"stream": True, "message": "把这轮修改沉淀成楼梯skill"}
            )
            # 还没迭代生成器，候选已经落盘（说明是同步处理的）
            self.assertFalse(isinstance(stream, dict))
            self.assertEqual(len(list_candidates(self.project.root)), 1)
            events = list(stream)
        self.assertEqual(events[0]["type"], "done")
        self.assertTrue(events[0]["data"]["ok"])
        self.assertEqual(events[0]["data"]["skill_proposal"]["status"], "draft")

    def test_non_explicit_stream_still_uses_pipeline_generator(self):
        self.session.source_path = None
        service = WorkbenchAssistantService(self.session)
        stream = service.generate_with_assistant_route({"stream": True, "message": "修改楼梯"})
        events = list(stream)
        self.assertTrue(events)
        self.assertEqual(events[0]["type"], "error")


class TestRejectUndo(_ServiceCase):
    def test_reject_after_verified_approval_deprecates_owned_artifact(self):
        """已批准并 verified 的候选再拒绝 = 撤销：只回收本候选产物。"""
        _write_quality_run(self.project)
        proposal, _ = self.propose()
        self.assertTrue(proposal["ok"], proposal)
        with patch(
            "openbrep.runtime.skill_harvest.resolve_skills_dir", return_value=str(self.skills_dir)
        ):
            approved = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": True}
            )
            self.assertTrue(approved["ok"], approved)
            self.assertIs(approved["verified"], True)
            # 再次 approve → 幂等
            again = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": True}
            )
            self.assertTrue(again.get("already_decided"))
            # 拒绝 = 撤销
            undone = SkillProposalService(self.session).confirm(
                {"proposal_id": proposal["proposal_id"], "approve": False}
            )
        self.assertTrue(undone["ok"], undone)
        self.assertTrue(undone["reclaimed"]["reclaimed"])
        loader = SkillsLoader(str(self.skills_dir))
        loader.load()
        self.assertEqual(loader.skill_meta("spiral_stair_stack").get("status"), "deprecated")
        self.assertIsNone(loader.get_by_name("spiral_stair_stack"))
