from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from openbrep.learning import ErrorLearningStore

MEMORY_LESSON_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)$")
MEMORY_LESSON_IGNORE_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)/ignore$")


class WorkbenchMemoryService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def memory_route(
        self,
        normalized_method: str,
        route: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """/api/memory* 与 /api/lessons* 的路由分发（workbench_api 只留薄壳）。

        会话锁在 WorkbenchSession.route 层（mutating 路由默认锁定）；这里只做
        分发，业务在下方各方法。memory 教训 fingerprint 走路径段（unquote），
        蒸馏教训 fingerprint 走 body（避免路径转义）。
        """
        if normalized_method == "GET" and route == "/api/memory/status":
            return self.memory_status()
        if normalized_method == "GET" and route == "/api/memory/lessons":
            return self.list_memory_lessons()
        if normalized_method == "POST" and route == "/api/memory/summarize":
            return self.summarize_project_memory(body)
        lesson_ignore_match = MEMORY_LESSON_IGNORE_ROUTE_RE.match(route)
        if lesson_ignore_match and normalized_method == "POST":
            return self.ignore_memory_lesson(unquote(lesson_ignore_match.group(1)))
        lesson_match = MEMORY_LESSON_ROUTE_RE.match(route)
        if lesson_match and normalized_method == "PATCH":
            return self.update_memory_lesson(unquote(lesson_match.group(1)), body)
        if lesson_match and normalized_method == "DELETE":
            return self.delete_memory_lesson(unquote(lesson_match.group(1)))
        if normalized_method == "DELETE" and route == "/api/memory":
            return self.clear_project_memory()
        # G4：蒸馏教训确认卡（lesson ≠ skill）
        if normalized_method == "GET" and route == "/api/lessons":
            return self.list_distilled_lessons(body)
        if normalized_method == "POST" and route == "/api/lessons/distill":
            return self.distill_distilled_lessons(body)
        if normalized_method == "POST" and route == "/api/lessons/status":
            return self.set_distilled_lesson_status(body)
        return {"ok": False, "error": f"Unknown route: {normalized_method} {route}"}

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

    # ── G4：蒸馏教训确认卡（lesson ≠ skill；同库、同状态机，只加视图/触发）──

    def list_distilled_lessons(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET /api/lessons：蒸馏教训确认卡列表（?status= 过滤，缺省全部）。

        lesson_cards_view 读 <project>/.openbrep/memory/learnings/
        distilled_lessons.jsonl（ErrorLearningStore 同根、独立文件）。
        """
        if self.session.source_path is None:
            return {"ok": True, "lessons": []}
        body = body or {}
        status = str(body.get("status") or "").strip() or None
        try:
            from openbrep import feedback_distill

            cards = feedback_distill.lesson_cards_view(self.session.source_path, status)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read distilled lessons: {exc}", "lessons": []}
        return {"ok": True, "lessons": cards}

    def distill_distilled_lessons(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /api/lessons/distill：以当前项目为 work_dir+scan_root 触发质量蒸馏。

        llm=None → feedback_distill 按配置自建（无 LLM 时返回 llm_unavailable
        note）；加工层绝不抛异常，失败只降级，绝不阻塞主流程。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before distilling quality lessons."}
        try:
            from openbrep import feedback_distill

            return feedback_distill.distill_quality_records(
                work_dir=self.session.source_path,
                scan_root=self.session.source_path,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to distill quality lessons: {exc}"}

    def set_distilled_lesson_status(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /api/lessons/status：approve→promote / ignore→reject / 撤回→demote。

        薄转发 feedback_distill.set_lesson_status（状态机校验与原子写在那边）。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before updating distilled lessons."}
        body = body or {}
        fingerprint = str(body.get("fingerprint") or "").strip()
        decision = str(body.get("decision") or "").strip()
        if not fingerprint:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        if not decision:
            return {"ok": False, "error": "Lesson decision is required."}
        try:
            from openbrep import feedback_distill

            return feedback_distill.set_lesson_status(
                self.session.source_path, fingerprint, decision
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to update distilled lesson: {exc}"}


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
