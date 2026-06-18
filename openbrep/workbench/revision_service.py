from __future__ import annotations

from typing import Any

from openbrep.hsf_project import HSFProject


class WorkbenchRevisionService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def list_project_revisions(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before reading revisions.", "revisions": []}
        try:
            from openbrep.revisions import get_latest_revision_id, list_revisions

            latest = get_latest_revision_id(self.session.source_path)
            revisions = [
                revision_to_api_item(revision, latest_revision_id=latest)
                for revision in reversed(list_revisions(self.session.source_path))
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read revisions: {exc}", "revisions": []}
        return {"ok": True, "revisions": revisions, "latest_revision_id": latest}

    def save_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before saving revisions."}
        try:
            from openbrep.revisions import create_revision, get_latest_revision_id

            self.session.project.save_to_disk()
            message = str(body.get("message") or "").strip()
            revision = create_revision(
                self.session.source_path,
                message=message,
                gsm_name=self.session.project.name,
                trigger="manual",
                parent_revision_id=get_latest_revision_id(self.session.source_path),
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save revision: {exc}"}
        return {
            "ok": True,
            "revision": revision_to_api_item(revision, latest_revision_id=revision.revision_id),
            "latest_revision_id": revision.revision_id,
        }

    def restore_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before restoring revisions."}
        revision_id = str(body.get("revision_id") or "").strip()
        if not revision_id:
            return {"ok": False, "error": "Revision id is required."}
        try:
            from openbrep.revisions import restore_revision

            restored = restore_revision(
                self.session.source_path,
                revision_id,
                message=f"workbench restore {revision_id}",
            )
            self.session.project = HSFProject.load_from_disk(str(self.session.source_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to restore revision: {exc}"}
        return {
            "ok": True,
            "restored_revision_id": revision_id,
            "revision": revision_to_api_item(restored, latest_revision_id=restored.revision_id),
            "latest_revision_id": restored.revision_id,
            **self.session.snapshot(),
        }


def revision_to_api_item(revision, *, latest_revision_id: str | None = None) -> dict[str, Any]:
    return {
        "revision_id": revision.revision_id,
        "project_name": revision.project_name,
        "gsm_name": revision.gsm_name,
        "created_at": revision.created_at,
        "message": revision.message,
        "file_count": len(revision.files or []),
        "trigger": revision.trigger,
        "intent": revision.intent,
        "user_instruction": revision.user_instruction,
        "changed_files": list(revision.changed_files or []),
        "parent_revision_id": revision.parent_revision_id,
        "compile": revision.compile or {},
        "explanation": revision.explanation,
        "is_latest": revision.revision_id == latest_revision_id,
    }
