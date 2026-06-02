from __future__ import annotations

from typing import Any

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.project_parameter_service import parameter_values
from ui.three_preview import preview_3d_to_three_payload


class WorkbenchPreviewService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def preview(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "preview": empty_preview_payload()}
        parameters, scripts = split_preview_request(request)
        return {
            "ok": True,
            "preview": preview_payload(self.session.project, parameters, scripts),
        }

    def preview_2d(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "preview": empty_preview_2d_payload()}
        parameters, scripts = split_preview_request(request)
        return {
            "ok": True,
            "preview": preview_2d_payload(self.session.project, parameters, scripts),
        }


def split_preview_request(request: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, str]]:
    if not request:
        return {}, {}
    if "parameters" in request or "scripts" in request:
        parameters = request.get("parameters") if isinstance(request.get("parameters"), dict) else {}
        scripts = request.get("scripts") if isinstance(request.get("scripts"), dict) else {}
        return parameters, normalize_script_overrides(scripts)
    return request, {}


def normalize_script_overrides(scripts: dict[Any, Any]) -> dict[str, str]:
    valid_names = {script_type.value for script_type in ScriptType}
    return {
        str(name): str(content)
        for name, content in scripts.items()
        if str(name) in valid_names and isinstance(content, str)
    }


def preview_payload(
    project: HSFProject,
    overrides: dict[str, Any] | None = None,
    script_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    scripts = script_overrides or {}
    result = preview_3d_script(
        script_for(project, ScriptType.SCRIPT_3D, scripts),
        parameters=parameter_values(project, overrides),
        setup_script=script_for(project, ScriptType.MASTER, scripts),
        unknown_command_policy="warn",
        quality="fast",
    )
    payload = preview_3d_to_three_payload(result)
    payload["warnings"] = result.warnings
    payload["verification"] = preview_verification(scripts)
    return payload


def empty_preview_payload() -> dict[str, Any]:
    return {
        "meshes": [],
        "wires": [],
        "warnings": [],
        "verification": preview_verification({}),
    }


def preview_2d_payload(
    project: HSFProject,
    overrides: dict[str, Any] | None = None,
    script_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    scripts = script_overrides or {}
    result = preview_2d_script(
        script_for(project, ScriptType.SCRIPT_2D, scripts),
        parameters=parameter_values(project, overrides),
        setup_script=script_for(project, ScriptType.MASTER, scripts),
        unknown_command_policy="warn",
        quality="fast",
    )
    return {
        "lines": [{"from": list(p1), "to": list(p2)} for p1, p2 in result.lines],
        "polygons": [[list(point) for point in polygon] for polygon in result.polygons],
        "circles": [
            {"cx": cx, "cy": cy, "r": r}
            for cx, cy, r in result.circles
        ],
        "arcs": [
            {"cx": cx, "cy": cy, "r": r, "a0": a0, "a1": a1}
            for cx, cy, r, a0, a1 in result.arcs
        ],
        "warnings": result.warnings,
        "verification": preview_verification(scripts),
    }


def empty_preview_2d_payload() -> dict[str, Any]:
    return {
        "lines": [],
        "polygons": [],
        "circles": [],
        "arcs": [],
        "warnings": [],
        "verification": preview_verification({}),
    }


def script_for(project: HSFProject, script_type: ScriptType, script_overrides: dict[str, str]) -> str:
    return script_overrides.get(script_type.value, project.get_script(script_type))


def preview_verification(script_overrides: dict[str, str]) -> dict[str, Any]:
    names = sorted(script_overrides)
    return {
        "source": "editor_buffer" if names else "saved",
        "script_overrides": names,
    }
