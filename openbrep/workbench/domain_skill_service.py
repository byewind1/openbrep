"""Project-scoped, explicit domain Skill discovery and selection."""
from __future__ import annotations

from typing import Any

from openbrep.domain_skill_selection import (
    load_project_skill_selections,
    select_project_skill,
    set_project_skill_enabled,
)
from openbrep.domain_skills import DomainSkillRegistry


class WorkbenchDomainSkillService:
    def __init__(self, session: Any):
        self._session = session

    def route(self, method: str, route: str, body: dict[str, Any]) -> dict[str, Any]:
        if method == "GET" and route == "/api/project/domain-skills":
            return self.list()
        if method == "POST" and route == "/api/project/domain-skills/select":
            return self.select(body)
        if method == "POST" and route == "/api/project/domain-skills/enabled":
            return self.set_enabled(body)
        return {"ok": False, "code": "method_not_found", "error": "未知 Domain Skill 路由"}

    def list(self) -> dict[str, Any]:
        project = self._session.project
        if project is None:
            return {"ok": False, "code": "project_required", "error": "请先打开 HSF 项目"}
        registry = DomainSkillRegistry.for_project(project.root)
        selected = load_project_skill_selections(project.root, registry)
        records = {item.skill_id: item for item in selected.records}
        items = []
        for skill_id in registry.skill_ids:
            loaded = registry.load(skill_id)
            if not loaded.ok or loaded.skill is None:
                items.append({"skill_id": skill_id, "available": False,
                              "issues": [issue.code for issue in loaded.issues]})
                continue
            skill = loaded.skill
            binding = records.get(skill_id)
            items.append({
                "skill_id": skill.skill_id,
                "version": skill.version,
                "status": skill.status,
                "content_hash": skill.content_hash,
                "selected": binding is not None,
                "enabled": binding.enabled if binding is not None else False,
                "pinned_version": binding.version if binding is not None else None,
                "pinned_hash": binding.content_hash if binding is not None else None,
            })
        return {
            "ok": True,
            "skills": items,
            "selected": [item.to_dict() for item in selected.records],
            "issues": [issue.__dict__ for issue in selected.issues],
        }

    def select(self, body: dict[str, Any]) -> dict[str, Any]:
        project = self._session.project
        if project is None:
            return {"ok": False, "code": "project_required", "error": "请先打开 HSF 项目"}
        skill_id = str(body.get("skill_id") or "").strip()
        registry = DomainSkillRegistry.for_project(project.root)
        loaded = registry.load(skill_id)
        if not loaded.ok or loaded.skill is None:
            return {"ok": False, "code": "skill_unavailable",
                    "error": "Skill 不可用", "issues": [issue.__dict__ for issue in loaded.issues]}
        record = select_project_skill(project.root, loaded.skill)
        return {"ok": True, "selection": record.to_dict()}

    def set_enabled(self, body: dict[str, Any]) -> dict[str, Any]:
        project = self._session.project
        if project is None:
            return {"ok": False, "code": "project_required", "error": "请先打开 HSF 项目"}
        skill_id = str(body.get("skill_id") or "").strip()
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return {"ok": False, "code": "invalid_request", "error": "enabled 必须是布尔值"}
        try:
            set_project_skill_enabled(project.root, skill_id, enabled)
        except KeyError:
            return {"ok": False, "code": "skill_not_selected", "error": "该 Skill 尚未选用"}
        return {"ok": True, "skill_id": skill_id, "enabled": enabled}
