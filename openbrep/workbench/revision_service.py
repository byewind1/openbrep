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
        """ST03：恢复 before 前由前端走草稿保护；本方法只负责落盘恢复 + 重载 HSF。

        draft_policy 仅回显（discard/keep）；服务端不管理编辑器草稿。
        恢复后重新 load_from_disk，session.snapshot 带回新预览（前端再清 ghost）。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before restoring revisions."}
        revision_id = str(body.get("revision_id") or "").strip()
        if not revision_id:
            return {"ok": False, "error": "Revision id is required."}
        draft_policy = body.get("draft_policy")
        if draft_policy is not None:
            draft_policy = str(draft_policy).strip() or None
            if draft_policy not in {"discard", "keep"}:
                return {
                    "ok": False,
                    "code": "INVALID_DRAFT_POLICY",
                    "error": "draft_policy must be 'discard' or 'keep' when provided.",
                }
        try:
            from openbrep.revisions import restore_revision

            restored = restore_revision(
                self.session.source_path,
                revision_id,
                message=f"workbench restore {revision_id}",
            )
            # ST03：恢复后重新载入 HSF，丢弃内存中的过期 project/预览状态
            self.session.project = HSFProject.load_from_disk(str(self.session.source_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to restore revision: {exc}"}
        snapshot = self.session.snapshot()
        # 强制清空可能沿用的预览源标记，避免前端把旧预览当恢复后结果
        if isinstance(snapshot.get("preview"), dict):
            snapshot["preview"] = dict(snapshot["preview"])
        return {
            "ok": True,
            "restored_revision_id": revision_id,
            "revision": revision_to_api_item(restored, latest_revision_id=restored.revision_id),
            "latest_revision_id": restored.revision_id,
            "restore": {
                "revision_id": revision_id,
                "draft_policy": draft_policy,
                "hsf_reloaded": True,
                "preview_cleared": True,
            },
            **snapshot,
        }

    def get_revision_diff(self, body: dict[str, Any]) -> dict[str, Any]:
        """ST03：查看差异——包装 revisions.compare_revisions。"""
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before reading revision diffs."}
        from_revision_id = str(body.get("from_revision_id") or "").strip()
        to_revision_id = str(body.get("to_revision_id") or "").strip()
        if not from_revision_id:
            return {"ok": False, "error": "from_revision_id is required."}
        if not to_revision_id:
            return {"ok": False, "error": "to_revision_id is required."}
        if from_revision_id == to_revision_id:
            return {
                "ok": True,
                "from_revision_id": from_revision_id,
                "to_revision_id": to_revision_id,
                "diff": "",
                "changed": False,
            }
        try:
            from openbrep.revisions import compare_revisions

            diff_text = compare_revisions(self.session.source_path, from_revision_id, to_revision_id)
        except FileNotFoundError as exc:
            return {"ok": False, "error": f"Revision not found: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"Failed to compare revisions: {exc}"}
        return {
            "ok": True,
            "from_revision_id": from_revision_id,
            "to_revision_id": to_revision_id,
            "diff": diff_text or "",
            "changed": bool(diff_text and diff_text.strip()),
        }


def revision_to_api_item(revision, *, latest_revision_id: str | None = None) -> dict[str, Any]:
    delivery_meta = _revision_delivery_meta(revision)
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
        "delivery": delivery_meta or None,
    }


def _revision_delivery_meta(revision) -> dict[str, Any] | None:
    """从 revision 目录 manifest 读取 ST02 delivery 块（Revision 对象无 metadata 字段）。"""
    import json

    path = getattr(revision, "path", None)
    if path is None:
        return None
    try:
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        meta = manifest.get("metadata") or {}
        if not isinstance(meta, dict):
            return None
        delivery = meta.get("delivery")
        if not isinstance(delivery, dict):
            return None
        return {
            "run_id": delivery.get("run_id"),
            "role": delivery.get("role"),
            "source_fingerprint": delivery.get("source_fingerprint"),
        }
    except Exception:
        return None
