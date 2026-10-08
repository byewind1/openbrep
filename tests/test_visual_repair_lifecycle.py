from pathlib import Path
from types import SimpleNamespace

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.revisions import list_revisions
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench.conversation_service import WorkbenchConversationService


def _service(tmp_path: Path):
    project = HSFProject.create_new("RepairLifecycle", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    root = project.save_to_disk()
    session = SimpleNamespace(project=project, project_epoch=1, session_id="repair-test")
    service = WorkbenchConversationService(session)
    turn = SimpleNamespace(
        body={"message": "repair one visual finding"},
        repair_context={
            "state": "awaiting_approval",
            "review_id": "review-" + "a" * 32,
            "finding_id": "finding-1",
            "run_id": "original-run",
            "project_root": str(root.resolve()),
            "before_source_fingerprint": compute_source_fingerprint(root),
        },
    )
    return service, project, root, turn


def test_visual_repair_checkpoints_then_restores_required_regression(tmp_path):
    service, _project, root, turn = _service(tmp_path)
    before_script = (root / "scripts" / "3d.gdl").read_text(encoding="utf-8")

    error = service._checkpoint_visual_repair(turn)
    assert error is None
    assert turn.repair_context["before_revision_id"]
    assert turn.repair_context["state"] == "executing"
    (root / "scripts" / "3d.gdl").write_text("BLOCK 2, 2, 2\n", encoding="utf-8")

    service._finish_visual_repair(turn, {
        "ok": True,
        "assistant": {
            "run_id": "repair-run",
            "verification": {"passed": False, "requirements_passed": False},
        },
    })

    assert (root / "scripts" / "3d.gdl").read_text(encoding="utf-8") == before_script
    assert turn.repair_context["state"] == "restored_after_requirement_failure"
    assert turn.repair_context["restore_revision_id"]
    assert len(list_revisions(root)) == 2


def test_visual_repair_checkpoint_rejects_stale_source(tmp_path):
    service, _project, root, turn = _service(tmp_path)
    (root / "scripts" / "3d.gdl").write_text("BLOCK 4, 4, 4\n", encoding="utf-8")

    error = service._checkpoint_visual_repair(turn)

    assert error["code"] == "REPAIR_SOURCE_STALE"
    assert turn.repair_context.get("before_revision_id") is None
    assert list_revisions(root) == []
