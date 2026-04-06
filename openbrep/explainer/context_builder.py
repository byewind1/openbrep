from __future__ import annotations

import re

from openbrep.hsf_project import HSFProject, ScriptType


_SCRIPT_LABELS = {
    ScriptType.MASTER: "1D",
    ScriptType.SCRIPT_2D: "2D",
    ScriptType.SCRIPT_3D: "3D",
    ScriptType.PARAM: "PARAM",
    ScriptType.UI: "UI",
    ScriptType.PROPERTIES: "PROPERTIES",
}

_LABEL_TO_SCRIPT_TYPE = {label: script_type for script_type, label in _SCRIPT_LABELS.items()}
_SCRIPT_ALIASES = {
    "1d": "1D",
    "master": "1D",
    "master script": "1D",
    "2d": "2D",
    "3d": "3D",
    "param": "PARAM",
    "parameter": "PARAM",
    "parameter script": "PARAM",
    "ui": "UI",
    "properties": "PROPERTIES",
    "property": "PROPERTIES",
    "pr": "PROPERTIES",
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


def resolve_script_target(user_input: str) -> str | None:
    text = (user_input or "").lower()
    if not text:
        return None
    for alias, label in _SCRIPT_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return label
    return None


def resolve_parameter_targets(project: HSFProject, user_input: str) -> list[str]:
    text = user_input or ""
    if not text:
        return []
    matched = []
    for param in project.parameters:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(param.name)}(?![A-Za-z0-9_])"
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(param.name)
    return matched


def build_project_script_context(project: HSFProject, script_label: str) -> dict | None:
    normalized = (script_label or "").upper()
    script_type = _LABEL_TO_SCRIPT_TYPE.get(normalized)
    if script_type is None:
        return None
    script_text = project.get_script(script_type)
    if not script_text:
        return None
    return build_script_context(
        script_type=normalized,
        script_text=script_text,
        parameters=[param.name for param in project.parameters],
    )


def build_project_parameter_context(project: HSFProject, param_name: str) -> dict | None:
    parameter = project.get_parameter(param_name)
    if parameter is None:
        return None

    usage_hits = []
    for script_type in ScriptType:
        content = project.get_script(script_type)
        if not content:
            continue
        lines = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(param_name)}(?![A-Za-z0-9_])"
            if re.search(pattern, line, re.IGNORECASE):
                lines.append(line)
        if lines:
            label = _SCRIPT_LABELS.get(script_type, str(script_type))
            usage_hits.append({
                "script": label,
                "lines": lines[:3],
            })

    return {
        "name": parameter.name,
        "type_tag": parameter.type_tag,
        "default_value": parameter.value,
        "description": parameter.description,
        "is_fixed": parameter.is_fixed,
        "usage_hits": usage_hits,
    }
