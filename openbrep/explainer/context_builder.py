from __future__ import annotations

from openbrep.hsf_project import HSFProject, ScriptType


_SCRIPT_LABELS = {
    ScriptType.MASTER: "1D",
    ScriptType.SCRIPT_2D: "2D",
    ScriptType.SCRIPT_3D: "3D",
    ScriptType.PARAM: "PARAM",
    ScriptType.UI: "UI",
    ScriptType.PROPERTIES: "PROPERTIES",
}


def build_script_context(script_type: str, script_text: str, parameters: list[str] | None = None) -> dict:
    return {
        "script_type": script_type,
        "script_text": script_text,
        "parameters": parameters or [],
    }


def build_project_context(project: HSFProject) -> dict:
    scripts = {}
    for script_type, content in project.scripts.items():
        if not content:
            continue
        label = _SCRIPT_LABELS.get(script_type, str(script_type))
        scripts[label] = content

    return {
        "gsm_name": project.name,
        "scripts": scripts,
        "parameters": [param.name for param in project.parameters],
    }
