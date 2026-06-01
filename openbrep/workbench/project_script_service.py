from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openbrep.hsf_project import HSFProject, ScriptType


SCRIPT_FILE_ORDER = [
    "3d.gdl",
    "2d.gdl",
    "1d.gdl",
    "vl.gdl",
    "pr.gdl",
    "ui.gdl",
    "paramlist.xml",
    "libpartdata.xml",
]

SCRIPT_NAME_TO_TYPE = {
    ScriptType.SCRIPT_3D.value: ScriptType.SCRIPT_3D,
    ScriptType.SCRIPT_2D.value: ScriptType.SCRIPT_2D,
    ScriptType.MASTER.value: ScriptType.MASTER,
    ScriptType.PARAM.value: ScriptType.PARAM,
    ScriptType.PROPERTIES.value: ScriptType.PROPERTIES,
    ScriptType.UI.value: ScriptType.UI,
}


class WorkbenchProjectScriptService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def list_project_scripts(self) -> dict[str, Any]:
        return {"ok": True, "scripts": [script_file_info(self.session.project, name) for name in SCRIPT_FILE_ORDER]}

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        path = script_relative_path(resolved)
        content = read_project_file_content(self.session.project, resolved)
        if content is None:
            return {"ok": False, "error": f"Script file not found: {resolved}"}
        return {"ok": True, "name": resolved, "path": path, "content": content}

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        content = str(body.get("content") or "")
        script_type = SCRIPT_NAME_TO_TYPE.get(resolved)
        if script_type is not None:
            self.session.project.set_script(script_type, content)
            self.session.project.save_to_disk()
        else:
            if self.session.source_path is None:
                return {"ok": False, "error": "Load an HSF project before saving XML files."}
            target = project_file_path(self.session.project, resolved)
            if target is None:
                return {"ok": False, "error": f"Unsupported script file: {script_name}"}
            target.write_text(content, encoding="utf-8-sig")
            self.session.project = HSFProject.load_from_disk(str(self.session.source_path))
        return {
            "ok": True,
            "success": True,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }


def resolve_script_name(script_name: str) -> str | None:
    cleaned = Path(str(script_name)).name.lower()
    for name in SCRIPT_FILE_ORDER:
        if cleaned == name.lower():
            return name
    return None


def script_relative_path(script_name: str) -> str:
    return script_name if script_name.endswith(".xml") else f"scripts/{script_name}"


def project_file_path(project: HSFProject, script_name: str) -> Path | None:
    resolved = resolve_script_name(script_name)
    if resolved is None:
        return None
    return project.root / script_relative_path(resolved)


def script_file_info(project: HSFProject, script_name: str) -> dict[str, Any]:
    path = project_file_path(project, script_name)
    content = read_project_file_content(project, script_name)
    return {
        "name": script_name,
        "path": script_relative_path(script_name),
        "exists": bool(path and path.exists()),
        "size": len(content.encode("utf-8")) if content is not None else 0,
        "empty": not bool((content or "").strip()),
    }


def read_project_file_content(project: HSFProject, script_name: str) -> str | None:
    script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
    if script_type is not None:
        return project.get_script(script_type) or ""
    if project.root is None:
        return None
    file_path = project_file_path(project, script_name)
    if file_path is None or not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8-sig")
