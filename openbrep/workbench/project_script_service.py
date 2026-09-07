from __future__ import annotations

import xml.etree.ElementTree as ET
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
        if self.session.project is None:
            return {"ok": True, "scripts": []}
        return {"ok": True, "scripts": [script_file_info(self.session.project, name) for name in SCRIPT_FILE_ORDER]}

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before reading scripts."}
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        path = script_relative_path(resolved)
        content = read_project_file_content(self.session.project, resolved)
        if content is None:
            return {"ok": False, "error": f"Script file not found: {resolved}"}
        return {"ok": True, "name": resolved, "path": path, "content": content}

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before saving scripts."}
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        content = str(body.get("content") or "")
        script_type = SCRIPT_NAME_TO_TYPE.get(resolved)
        if script_type is not None:
            self.session.project.set_script(script_type, content)
            if self.session.source_path is not None:
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
    is_unsaved_project = bool(project.root and not project.root.exists())
    is_authorable_gdl = script_name in SCRIPT_NAME_TO_TYPE
    return {
        "name": script_name,
        "path": script_relative_path(script_name),
        "exists": bool(path and path.exists()) or (is_unsaved_project and is_authorable_gdl),
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


# ── SF1：Save As 的 script_overrides（只读校验 + 应用到工作副本）────────────
# 白名单与 save_project_script 完全一致（SCRIPT_FILE_ORDER / resolve_script_name），
# 不另造更宽的文件写 API。XML 覆盖必须能通过既有解析路径（well-formed 校验 +
# parse_paramlist_xml / _parse_libpartdata），非法 XML 明确失败。


def validate_script_overrides(raw: Any) -> tuple[dict[str, str] | None, str | None]:
    """校验 export-hsf 的 script_overrides 请求字段；返回 (overrides, error)。"""
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return None, "script_overrides must be a mapping of script file name to content."
    overrides: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key)
        # 拒绝绝对路径 / 路径穿越：键必须是白名单内的裸文件名
        if name != Path(name).name or name in (".", "..") or "\\" in name:
            return None, f"Invalid script override name: {name!r}."
        resolved = resolve_script_name(name)
        if resolved is None:
            return None, f"Unsupported script file in overrides: {name!r}."
        if not isinstance(value, str):
            return None, f"script_overrides content must be a string: {name!r}."
        overrides[resolved] = value
    return overrides, None


def apply_script_overrides(project: HSFProject, overrides: dict[str, str]) -> str | None:
    """把 overrides 应用到（工作副本）project；返回错误文本或 None。

    普通脚本复用 HSFProject.set_script；XML 覆盖先校验 well-formed，
    再走既有解析路径保持副本内存参数与落盘一致。
    """
    from openbrep.paramlist_builder import parse_paramlist_xml

    for name, content in overrides.items():
        script_type = SCRIPT_NAME_TO_TYPE.get(name)
        if script_type is not None:
            project.set_script(script_type, content)
            continue
        try:
            ET.fromstring(content)
        except ET.ParseError as exc:
            return f"Invalid XML in {name}: {exc}."
        if name == "paramlist.xml":
            project.parameters = parse_paramlist_xml(content)
        elif name == "libpartdata.xml":
            project._parse_libpartdata(content)
        else:  # pragma: no cover - resolve_script_name 白名单已拦截
            return f"Unsupported script file in overrides: {name!r}."
    return None
