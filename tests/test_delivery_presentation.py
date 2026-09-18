"""ST03：delivery presentation / assistant 交付载荷 / revision 恢复与差异契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

from openbrep.runtime.delivery_finalizer import DeliverySource
from openbrep.workbench.delivery_presentation import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_NO_CHANGE,
    STATUS_SNAPSHOT_FAILED,
    STATUS_UNLINKED,
    attach_continue_linkage,
    build_delivery_presentation,
    delivery_payload_from_result,
    normalize_continue_from,
    unlinked_delivery_presentation,
)


def _ds(**kwargs) -> dict:
    base = {
        "schema_version": 1,
        "run_id": "r_20260918_120000_abc123",
        "state": "verified_change",
        "before_revision_id": "r0001",
        "after_revision_id": "r0002",
        "source_fingerprint": "sha256:fd85d4f724f9",
        "changed_files": ["scripts/3d.gdl"],
        "snapshot_status": "saved",
        "error_code": None,
    }
    base.update(kwargs)
    return base


class TestU01PartialChangePresentation:
    def test_partial_shows_incomplete_and_recover(self):
        p = build_delivery_presentation(
            _ds(
                state="partial_change",
                after_revision_id=None,
                snapshot_status="skipped",
                changed_files=["scripts/3d.gdl", "paramlist.xml"],
            ),
            intent="MODIFY",
            claimed_change=True,
            verification_passed=False,
        )
        assert p["status"] == STATUS_INCOMPLETE
        assert p["unlinked"] is False
        assert p["show_success_badge"] is False
        assert "未完成" in p["headline"]
        assert p["changed_files"] == ["scripts/3d.gdl", "paramlist.xml"]
        assert p["can_recover"] is True
        assert p["recover_revision_id"] == "r0001"
        assert p["can_view_diff"] is True
        assert p["can_continue"] is True
        assert p["show_before_after"] is False
        # F1：partial 无 after → 差异目标必须是工作源
        assert p["diff_target"] == "working"
        assert p["after_revision_id"] is None

    def test_partial_with_error_code_reason(self):
        p = build_delivery_presentation(
            _ds(
                state="partial_change",
                after_revision_id=None,
                error_code="epoch_changed",
                changed_files=["scripts/3d.gdl"],
            ),
            intent="MODIFY",
            claimed_change=True,
        )
        assert "项目已切换" in p["reason"] or "epoch" in p["reason"].lower() or "项目" in p["reason"]


class TestU05UnchangedNoFakeFix:
    def test_claimed_modify_no_diff(self):
        p = build_delivery_presentation(
            _ds(
                state="unchanged",
                after_revision_id=None,
                changed_files=[],
                snapshot_status="skipped",
            ),
            intent="MODIFY",
            claimed_change=True,
        )
        assert p["status"] == STATUS_NO_CHANGE
        assert p["show_success_badge"] is False
        assert "未产生源码变化" in p["headline"]
        assert "已修复" not in p["headline"]
        assert "未检测到源码差异" in p["reason"] or "无源码" in p["reason"]
        assert p["can_continue"] is True  # 允许继续描述

    def test_explain_only_not_claimed_change(self):
        p = build_delivery_presentation(
            _ds(state="unchanged", after_revision_id=None, changed_files=[]),
            intent="EXPLAIN",
            claimed_change=False,
        )
        assert p["status"] == STATUS_NO_CHANGE
        assert "解释" in p["headline"] or "检查" in p["headline"]
        assert p["show_success_badge"] is False


class TestSnapshotFailedSplit:
    def test_check_passed_version_failed(self):
        p = build_delivery_presentation(
            _ds(
                state="snapshot_failed",
                after_revision_id=None,
                snapshot_status="failed",
                error_code="snapshot_write_failed",
            ),
            intent="MODIFY",
            claimed_change=True,
            verification_passed=True,
        )
        assert p["status"] == STATUS_SNAPSHOT_FAILED
        assert p["check_status"] == "passed"
        assert p["show_success_badge"] is False
        assert "检查通过" in p["headline"]
        assert "版本" in p["headline"]
        assert p["version_status"] == "failed"

    def test_check_failed_version_failed(self):
        p = build_delivery_presentation(
            _ds(
                state="snapshot_failed",
                after_revision_id=None,
                snapshot_status="rejected",
                error_code="source_changed_after_verification",
            ),
            intent="MODIFY",
            claimed_change=True,
            verification_passed=False,
        )
        assert p["status"] == STATUS_SNAPSHOT_FAILED
        assert p["check_status"] == "failed"
        assert "检查未通过" in p["headline"]
        assert p["show_success_badge"] is False


class TestVerifiedChangeBeforeAfter:
    def test_shows_before_after_and_success(self):
        p = build_delivery_presentation(
            _ds(state="verified_change"),
            intent="MODIFY",
            claimed_change=True,
            verification_passed=True,
        )
        assert p["status"] == STATUS_COMPLETED
        assert p["show_success_badge"] is True
        assert p["show_before_after"] is True
        assert p["before_revision_id"] == "r0001"
        assert p["after_revision_id"] == "r0002"
        assert p["can_view_diff"] is True
        assert p["recover_revision_id"] == "r0001"


class TestUnlinkedOldRecords:
    def test_missing_delivery_source(self):
        p = build_delivery_presentation(None, intent="MODIFY")
        assert p["status"] == STATUS_UNLINKED
        assert p["unlinked"] is True
        assert p["show_success_badge"] is False
        assert "未关联" in p["headline"]
        assert p["before_revision_id"] is None

    def test_invalid_state_rejected(self):
        p = build_delivery_presentation({"state": "nope", "run_id": "x"}, intent="MODIFY")
        assert p["status"] == STATUS_UNLINKED

    def test_from_dict_roundtrip(self):
        ds = DeliverySource.from_dict(_ds(state="verified_change"))
        assert ds is not None
        p = build_delivery_presentation(ds, intent="MODIFY", verification_passed=True)
        assert p["state"] == "verified_change"
        assert p["run_id"] == "r_20260918_120000_abc123"


class TestContinueLinkage:
    def test_normalize_continue_from(self):
        cf = normalize_continue_from(
            {"origin_run_id": "r_orig", "original_instruction": "把层板数改成 5"}
        )
        assert cf == {
            "origin_run_id": "r_orig",
            "original_instruction": "把层板数改成 5",
        }

    def test_normalize_rejects_empty(self):
        assert normalize_continue_from(None) is None
        assert normalize_continue_from({}) is None
        assert normalize_continue_from("x") is None

    def test_attach_continued_from(self):
        p = build_delivery_presentation(
            _ds(state="partial_change", after_revision_id=None, changed_files=["scripts/3d.gdl"]),
            intent="MODIFY",
            claimed_change=True,
        )
        linked = attach_continue_linkage(
            p,
            {"origin_run_id": "r_orig", "original_instruction": "把层板数改成 5"},
        )
        assert linked["continued_from"]["origin_run_id"] == "r_orig"
        assert linked["original_instruction"] == "把层板数改成 5"


class TestDeliveryPayloadFromResult:
    def test_result_with_metadata_delivery_source(self):
        result = SimpleNamespace(
            intent="MODIFY",
            verification={"passed": True},
            metadata={
                "delivery_source": _ds(state="verified_change"),
                "acceptance": {"summary_lines": ["ok"]},
            },
            plain_text="done",
            scripts={"scripts/3d.gdl": "PRIM 1"},
        )
        payload = delivery_payload_from_result(
            result,
            original_instruction="把层板数改成 5",
        )
        assert payload["delivery_source"]["state"] == "verified_change"
        assert payload["presentation"]["status"] == STATUS_COMPLETED
        assert payload["original_instruction"] == "把层板数改成 5"

    def test_result_without_delivery_source_unlinked(self):
        result = SimpleNamespace(
            intent="MODIFY",
            verification=None,
            metadata={},
            plain_text="ok",
            scripts={},
        )
        payload = delivery_payload_from_result(result)
        assert payload["delivery_source"] is None
        assert payload["presentation"]["status"] == STATUS_UNLINKED
        assert payload["presentation"]["unlinked"] is True

    def test_continue_from_in_metadata(self):
        result = SimpleNamespace(
            intent="MODIFY",
            verification={"passed": False},
            metadata={
                "delivery_source": _ds(state="verified_change"),
                "continue_from": {"origin_run_id": "r_old", "original_instruction": "原指令"},
            },
        )
        payload = delivery_payload_from_result(result)
        assert payload["continue_from"]["origin_run_id"] == "r_old"
        assert payload["presentation"]["continued_from"]["origin_run_id"] == "r_old"


class TestAssistantServiceGeneratePayload:
    def test_generate_assistant_dict_includes_delivery(self):
        from openbrep.workbench.assistant_service import WorkbenchAssistantService

        result = SimpleNamespace(
            intent="MODIFY",
            verification={"passed": True},
            metadata={"delivery_source": _ds(state="verified_change"), "acceptance": None},
            plain_text="已完成",
            scripts={"scripts/3d.gdl": "BODY"},
        )
        assistant = WorkbenchAssistantService._generate_assistant_dict(
            result,
            instruction="把层板数改成 5",
            continue_from={"origin_run_id": "r_old", "original_instruction": "把层板数改成 5"},
        )
        assert assistant["delivery"]["state"] == "verified_change"
        assert assistant["delivery_source"]["before_revision_id"] == "r0001"
        assert assistant["run_id"] == "r_20260918_120000_abc123"
        assert assistant["continue_from"]["origin_run_id"] == "r_old"
        assert assistant["changed_files"] == ["scripts/3d.gdl"]

    def test_partial_prefers_delivery_changed_files(self):
        from openbrep.workbench.assistant_service import WorkbenchAssistantService

        result = SimpleNamespace(
            intent="MODIFY",
            verification={"passed": False},
            metadata={
                "delivery_source": _ds(
                    state="partial_change",
                    after_revision_id=None,
                    changed_files=["scripts/3d.gdl", "paramlist.xml"],
                )
            },
            plain_text="中断",
            scripts={},  # handler 未报告 scripts，delivery 才是权威
        )
        assistant = WorkbenchAssistantService._generate_assistant_dict(result)
        assert assistant["delivery"]["status"] == STATUS_INCOMPLETE
        assert assistant["changed_files"] == ["scripts/3d.gdl", "paramlist.xml"]
        assert assistant["delivery"]["show_success_badge"] is False

    def test_merge_continue_from_writes_metadata(self):
        from openbrep.workbench.assistant_service import WorkbenchAssistantService

        result = SimpleNamespace(metadata={}, intent="MODIFY", verification=None, plain_text="", scripts={})
        WorkbenchAssistantService._merge_continue_from(
            result,
            {"origin_run_id": "r_old", "original_instruction": "原指令"},
        )
        assert result.metadata["continue_from"]["origin_run_id"] == "r_old"


class TestRevisionServiceRestoreAndDiff:
    def test_restore_requires_draft_policy_when_present(self):
        class FakeSession:
            source_path = object()
            project = None

        from openbrep.workbench.revision_service import WorkbenchRevisionService

        svc = WorkbenchRevisionService(FakeSession())
        bad = svc.restore_project_revision({"revision_id": "r0001", "draft_policy": "maybe"})
        assert bad["ok"] is False
        assert bad["code"] == "INVALID_DRAFT_POLICY"

    def test_restore_happy_path_echoes_policy(self, tmp_path):
        from openbrep.revisions import create_revision
        from openbrep.workbench.revision_service import WorkbenchRevisionService

        # minimal HSF project
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "3d.gdl").write_text("BODY\n", encoding="utf-8")
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        rev = create_revision(tmp_path, message="base", trigger="manual")

        class FakeProject:
            name = "Chair"

        class FakeSession:
            source_path = tmp_path
            project = FakeProject()
            project_epoch = 1

            def snapshot(self):
                return {
                    "project": {"name": "Chair", "path": str(tmp_path)},
                    "parameters": [],
                    "preview": {"meshes": []},
                }

        svc = WorkbenchRevisionService(FakeSession())
        # 制造工作源差异后再恢复
        (scripts / "3d.gdl").write_text("CHANGED\n", encoding="utf-8")
        result = svc.restore_project_revision(
            {"revision_id": rev.revision_id, "draft_policy": "keep"}
        )
        assert result["ok"] is True
        assert result["restored_revision_id"] == rev.revision_id
        assert result["restore"]["draft_policy"] == "keep"
        assert result["restore"]["hsf_reloaded"] is True
        assert result["restore"]["preview_cleared"] is True
        assert (scripts / "3d.gdl").read_text(encoding="utf-8") == "BODY\n"

    def test_revision_diff_between_before_after(self, tmp_path):
        from openbrep.revisions import create_revision
        from openbrep.workbench.revision_service import WorkbenchRevisionService

        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "3d.gdl").write_text("BODY\n", encoding="utf-8")
        before = create_revision(tmp_path, message="before", trigger="manual")
        (scripts / "3d.gdl").write_text("BODY\nPRIM 1\n", encoding="utf-8")
        after = create_revision(tmp_path, message="after", trigger="modify")

        class FakeSession:
            source_path = tmp_path
            project = None

        svc = WorkbenchRevisionService(FakeSession())
        result = svc.get_revision_diff(
            {"from_revision_id": before.revision_id, "to_revision_id": after.revision_id}
        )
        assert result["ok"] is True
        assert result["changed"] is True
        assert "3d.gdl" in result["diff"]
        assert result["to_working_tree"] is False

        same = svc.get_revision_diff(
            {"from_revision_id": before.revision_id, "to_revision_id": before.revision_id}
        )
        assert same["ok"] is True
        assert same["changed"] is False
        assert "from_equals_to" in (same.get("warning") or "")

    def test_f1_partial_working_tree_diff_not_before_to_before(self, tmp_path):
        """F1 阻断项：partial 无 after 时，diff 必须是 before→工作源且含真实变更。"""
        from openbrep.revisions import WORKING_TREE_SENTINEL, create_revision
        from openbrep.workbench.revision_service import WorkbenchRevisionService
        from openbrep.workbench.delivery_presentation import build_delivery_presentation

        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "3d.gdl").write_text("BODY\n", encoding="utf-8")
        before = create_revision(tmp_path, message="before", trigger="manual")
        # 模拟超时后的部分修改：工作树变了，但没有 after revision
        (scripts / "3d.gdl").write_text("BODY\nPRIM 1\n", encoding="utf-8")

        p = build_delivery_presentation(
            _ds(
                state="partial_change",
                after_revision_id=None,
                before_revision_id=before.revision_id,
                changed_files=["scripts/3d.gdl"],
            ),
            intent="MODIFY",
            claimed_change=True,
        )
        assert p["can_view_diff"] is True
        assert p["diff_target"] == "working"
        assert p["after_revision_id"] is None

        class FakeSession:
            source_path = tmp_path
            project = None

        svc = WorkbenchRevisionService(FakeSession())
        # 模拟前端：partial 时 to 省略 / working sentinel
        for to in (None, "", WORKING_TREE_SENTINEL):
            result = svc.get_revision_diff(
                {"from_revision_id": before.revision_id, "to_revision_id": to}
            )
            assert result["ok"] is True
            assert result["to_working_tree"] is True
            assert result["changed"] is True
            assert "PRIM" in result["diff"]
            assert f"{before.revision_id}/scripts/3d.gdl" in result["diff"]
            assert "__working__/scripts/3d.gdl" in result["diff"]

        # 回归：before→before 不得被包装成有效差异
        bad = svc.get_revision_diff(
            {"from_revision_id": before.revision_id, "to_revision_id": before.revision_id}
        )
        assert bad["changed"] is False
        assert bad.get("to_working_tree") is False

    def test_f2_history_meta_roundtrip_and_continue_persisted(self, tmp_path):
        """F2：聊天历史持久化 delivery/continue；刷新后可恢复/显示 unlinked。"""
        from openbrep.learning import ErrorLearningStore
        from openbrep.workbench.assistant_service import WorkbenchAssistantService
        from openbrep.workbench.delivery_presentation import unlinked_delivery_presentation
        from openbrep.runtime.pipeline import TaskRequest

        store = ErrorLearningStore(tmp_path)
        delivery = build_delivery_presentation(
            _ds(state="partial_change", after_revision_id=None, changed_files=["scripts/3d.gdl"]),
            intent="MODIFY",
            claimed_change=True,
        )
        continue_from = {"origin_run_id": "r_old", "original_instruction": "把层板数改成 5"}
        store.rewrite_chat_transcript(
            [
                {"role": "user", "content": "把层板数改成 5"},
                {
                    "role": "assistant",
                    "content": "未完成，存在部分修改\n\nChanged files: scripts/3d.gdl",
                    "delivery": delivery,
                    "delivery_source": _ds(state="partial_change", after_revision_id=None),
                    "delivery_continue_from": continue_from,
                    "original_instruction": "把层板数改成 5",
                    "run_id": delivery.get("run_id"),
                    "changed_files": ["scripts/3d.gdl"],
                },
                # 旧记录：无 delivery meta
                {"role": "assistant", "content": "历史遗留回复"},
            ],
            project_name="Chair",
            source="react_workbench",
        )

        class FakeSession:
            source_path = tmp_path

        svc = WorkbenchAssistantService(FakeSession())
        listed = svc.list_assistant_history()
        assert listed["ok"] is True
        messages = listed["messages"]
        assert len(messages) == 3
        restored = messages[1]
        assert restored["delivery"]["status"] == "incomplete"
        assert restored["delivery_continue_from"]["origin_run_id"] == "r_old"
        assert restored["original_instruction"] == "把层板数改成 5"
        assert restored["run_id"] == delivery.get("run_id")
        # 旧记录无 delivery 字段 → 前端 hydrate 显示 unlinked（契约层断言 helper）
        assert "delivery" not in messages[2]
        assert unlinked_delivery_presentation()["status"] == "unlinked"

        # continue_from 进入 TaskRequest → pipeline metadata（不在 prompt）
        request = TaskRequest(user_input="把层板数改成 5", intent="MODIFY", continue_from=continue_from)
        assert request.continue_from == continue_from

    def test_quality_context_includes_continue_from(self, tmp_path):
        """pipeline 质量档案 context 携带 continue_from（刷新后可重读）。"""
        from openbrep.runtime.pipeline import TaskPipeline

        pipeline = TaskPipeline.__new__(TaskPipeline)
        pipeline.quality_ledger_enabled = False
        # 写入路径的 context 构造在 _write_quality_record；此处验证 source 含 continue_from
        source = open(
            "/Users/ren/MAC工作/工作/code/开源项目/gdl-agent/.worktrees/st03-delivery-ui/openbrep/runtime/pipeline.py",
            encoding="utf-8",
        ).read()
        assert '"continue_from": (result.metadata or {}).get("continue_from")' in source
        assert "continue_from=continue_from" in source

    def test_unlinked_helper_stable(self):
        p = unlinked_delivery_presentation()
        assert p["status"] == STATUS_UNLINKED
        assert p["show_success_badge"] is False
        assert p["status"] != STATUS_FAILED or p["unlinked"] is True
