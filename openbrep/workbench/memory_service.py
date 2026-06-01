from __future__ import annotations

from typing import Any

from openbrep.learning import ErrorLearningStore


class WorkbenchMemoryService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def memory_status(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {
                "ok": True,
                "memory": {
                    "memory_root": "",
                    "chat_count": 0,
                    "lesson_count": 0,
                    "has_learned_skill": False,
                    "total_bytes": 0,
                },
            }
        try:
            status = ErrorLearningStore(self.session.source_path).memory_status()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read project memory status: {exc}"}
        return {"ok": True, "memory": memory_status_to_api(status)}

    def list_memory_lessons(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": True, "lessons": []}
        try:
            lessons = ErrorLearningStore(self.session.source_path).list_error_lessons(include_seed=False)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read project memory lessons: {exc}", "lessons": []}
        return {"ok": True, "lessons": [error_lesson_to_api(lesson) for lesson in lessons]}

    def summarize_project_memory(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before summarizing project memory."}
        body = body or {}
        try:
            limit = int(body.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        limit = max(1, min(limit, 50))
        try:
            store = ErrorLearningStore(self.session.source_path)
            summary = store.summarize_to_skill(
                project_name=self.session.project.name,
                limit=limit,
                scan_chat=True,
                llm_refiner=None,
            )
            skill = store.load_learned_skill()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to summarize project memory: {exc}"}
        return {
            "ok": bool(summary.ok),
            "summary": learning_summary_to_api(summary),
            "skill": skill,
            **({} if summary.ok else {"error": summary.message}),
        }

    def delete_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before deleting project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        try:
            deleted, remaining_count = ErrorLearningStore(self.session.source_path).delete_error_lesson(cleaned)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to delete project memory lesson: {exc}"}
        if not deleted:
            return {"ok": False, "error": "Project memory lesson was not found.", "remaining_count": remaining_count}
        return {"ok": True, "deleted": cleaned, "remaining_count": remaining_count}

    def ignore_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before ignoring project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        try:
            ignored, remaining_count = ErrorLearningStore(self.session.source_path).ignore_error_lesson(cleaned)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to ignore project memory lesson: {exc}"}
        if not ignored:
            return {"ok": False, "error": "Project memory lesson was not found.", "remaining_count": remaining_count}
        return {"ok": True, "ignored": cleaned, "remaining_count": remaining_count}

    def update_memory_lesson(self, fingerprint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before editing project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        body = body or {}
        updates = {
            key: body[key]
            for key in ("category", "summary", "guidance", "example")
            if key in body
        }
        if not updates:
            return {"ok": False, "error": "No editable lesson fields were provided."}
        try:
            lesson = ErrorLearningStore(self.session.source_path).update_error_lesson(cleaned, updates)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to update project memory lesson: {exc}"}
        if lesson is None:
            return {"ok": False, "error": "Project memory lesson was not found."}
        return {"ok": True, "lesson": error_lesson_to_api(lesson)}

    def clear_project_memory(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {
                "ok": True,
                "before": {
                    "memory_root": "",
                    "chat_count": 0,
                    "lesson_count": 0,
                    "has_learned_skill": False,
                    "total_bytes": 0,
                },
            }
        try:
            before = ErrorLearningStore(self.session.source_path).clear_memory()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to clear project memory: {exc}"}
        return {"ok": True, "before": memory_status_to_api(before)}


def memory_status_to_api(status) -> dict[str, Any]:
    return {
        "memory_root": str(status.memory_root),
        "chat_count": status.chat_count,
        "lesson_count": status.lesson_count,
        "has_learned_skill": bool(status.has_learned_skill),
        "total_bytes": status.total_bytes,
    }


def error_lesson_to_api(lesson) -> dict[str, Any]:
    return {
        "fingerprint": lesson.fingerprint,
        "category": lesson.category,
        "summary": lesson.summary,
        "guidance": lesson.guidance,
        "example": lesson.example,
        "count": lesson.count,
        "first_seen": lesson.first_seen,
        "last_seen": lesson.last_seen,
        "source": lesson.source,
        "project_name": lesson.project_name,
        "raw_excerpt": lesson.raw_excerpt,
        "ignored": bool(getattr(lesson, "ignored", False)),
    }


def learning_summary_to_api(summary) -> dict[str, Any]:
    return {
        "ok": bool(summary.ok),
        "lesson_count": summary.lesson_count,
        "path": str(summary.path),
        "message": summary.message,
    }
