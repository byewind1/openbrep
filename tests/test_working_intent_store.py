from pathlib import Path
from types import SimpleNamespace

from openbrep.workbench.conversation_service import WorkbenchConversationService
from openbrep.workbench.working_intent import intent_context
from openbrep.workbench.working_intent_store import STORE_RELATIVE_PATH


def _conversation(root: Path, session_id: str, epoch: int = 1):
    return WorkbenchConversationService(SimpleNamespace(
        project=SimpleNamespace(root=root),
        project_epoch=epoch,
        session_id=session_id,
    ))


def test_project_working_intent_reopens_with_keep_constraints(tmp_path):
    root = tmp_path / "Object.hsf"
    root.mkdir()
    first = _conversation(root, "session-a")
    first._reduce_working_intent({
        "kind": "turn",
        "message_id": "turn-a",
        "message": "加背板，保持宽度不变",
        "constraints": ["保持宽度不变"],
        "execute": True,
    })

    reopened = _conversation(root, "session-b", epoch=4)
    assert intent_context(reopened.working_intent)["constraints"][0]["value"] == "保持宽度不变"
    assert reopened.working_intent["session_id"] == "session-b"
    assert reopened.working_intent["project_epoch"] == 4
    assert (root / STORE_RELATIVE_PATH).is_file()


def test_project_copy_keeps_author_rules_but_invalidates_original_evidence(tmp_path):
    source = tmp_path / "source.hsf"
    copied = tmp_path / "copy.hsf"
    source.mkdir()
    copied.mkdir()
    first = _conversation(source, "session-a")
    first._reduce_working_intent({"kind": "turn", "message_id": "t1", "message": "不改外框",
                                  "constraints": ["不改外框"], "execute": True})
    first._reduce_working_intent({"kind": "result", "task_id": "t1", "result": {"ok": True,
        "assistant": {"run_id": "run-1", "changed_files": ["3d.gdl"],
                      "delivery_source": {"run_id": "run-1", "after_revision_id": "rev-1"},
                      "verification": {"passed": False}}}})
    (copied / STORE_RELATIVE_PATH).parent.mkdir(parents=True)
    (copied / STORE_RELATIVE_PATH).write_bytes((source / STORE_RELATIVE_PATH).read_bytes())

    opened_copy = _conversation(copied, "session-copy")
    assert intent_context(opened_copy.working_intent)["constraints"][0]["value"] == "不改外框"
    task = opened_copy.working_intent["tasks"][0]
    assert task["state"] == "invalidated"
    assert task["run_id"] is None
    assert task["delivery_ref"] is None


def test_corrupt_project_intent_degrades_visibly(tmp_path):
    root = tmp_path / "Object.hsf"
    root.mkdir()
    path = root / STORE_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    service = _conversation(root, "session-a")
    assert service.working_intent_persistence == "load_failed"
    assert service.working_intent_issue == "working_intent_load_failed:JSONDecodeError"
    assert service.intent_summary()["persistence_issue"] == service.working_intent_issue
