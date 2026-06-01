from __future__ import annotations

from typing import Any

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.project_parameter_service import parameter_values
from ui.three_preview import preview_3d_to_three_payload


class WorkbenchPreviewService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_payload(self.session.project, overrides or {}),
        }

    def preview_2d(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_2d_payload(self.session.project, overrides or {}),
        }


def preview_payload(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    result = preview_3d_script(
        project.get_script(ScriptType.SCRIPT_3D),
        parameters=parameter_values(project, overrides),
        setup_script=project.get_script(ScriptType.MASTER),
        unknown_command_policy="warn",
        quality="fast",
    )
    payload = preview_3d_to_three_payload(result)
    payload["warnings"] = result.warnings
    return payload


def preview_2d_payload(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    result = preview_2d_script(
        project.get_script(ScriptType.SCRIPT_2D),
        parameters=parameter_values(project, overrides),
        setup_script=project.get_script(ScriptType.MASTER),
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
    }
