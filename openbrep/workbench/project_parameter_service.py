from __future__ import annotations

import re
from typing import Any

from openbrep.hsf_project import GDLParameter, HSFProject, VALID_PARAM_TYPES
from openbrep.paramlist_builder import validate_paramlist


GDL_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AUTHORABLE_PARAM_TYPES = {"Length", "RealNum", "Integer", "Boolean", "String"}


class WorkbenchProjectParameterService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        changed = apply_parameter_values(self.session.project, changes)
        if changed and self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {"ok": True, "changed": changed, **self.session.snapshot()}

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            param = build_parameter_from_authoring_request(self.session.project, body)
            self.session.project.add_parameter(param)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.session.project.save_to_disk()
        return {
            "ok": True,
            "added": parameter_to_dict(param),
            **self.session.snapshot(),
        }

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}

        try:
            new_name = validate_authorable_parameter_name(
                self.session.project,
                str(body.get("new_name") if "new_name" in body else param.name),
                current_name=param.name,
            )
            new_type = validate_authorable_type(
                str(body.get("type_tag") if "type_tag" in body else param.type_tag)
            )
            if param.is_fixed and (new_name != param.name or new_type != param.type_tag):
                return {"ok": False, "error": f"Fixed parameter '{param.name}' cannot be renamed or retagged"}
            if "value" in body:
                param.value = coerce_parameter_value(new_type, body.get("value"))
            if "description" in body:
                param.description = str(body.get("description") or "").strip()
            param.name = new_name
            param.type_tag = new_type
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.session.project.save_to_disk()
        return {
            "ok": True,
            "updated": parameter_to_dict(param),
            **self.session.snapshot(),
        }

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}
        if param.is_fixed:
            return {"ok": False, "error": f"Fixed parameter '{name}' cannot be deleted"}
        self.session.project.remove_parameter(name)
        self.session.project.save_to_disk()
        return {
            "ok": True,
            "deleted": name,
            **self.session.snapshot(),
        }

    def validate_project_parameters(self) -> dict[str, Any]:
        return {
            "ok": True,
            "issues": validate_paramlist(self.session.project.parameters or []),
        }


def parameter_to_dict(param: GDLParameter) -> dict[str, Any]:
    return {
        "name": param.name,
        "type": param.type_tag,
        "type_tag": param.type_tag,
        "description": param.description,
        "value": param.value,
        "is_fixed": bool(param.is_fixed),
    }


def parameter_values(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, float]:
    values: dict[str, float] = {}
    for param in project.parameters:
        numeric = to_preview_number(param.value)
        if numeric is not None:
            values[param.name.upper()] = numeric
    for key, value in (overrides or {}).items():
        numeric = to_preview_number(value)
        if numeric is not None:
            values[str(key).upper()] = numeric
    return values


def apply_parameter_values(project: HSFProject, changes: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for name, value in changes.items():
        param = project.get_parameter(name)
        if param is None:
            continue
        param.value = coerce_parameter_value(param.type_tag, value)
        changed[name] = value
    return changed


def build_parameter_from_authoring_request(project: HSFProject, body: dict[str, Any]) -> GDLParameter:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(name):
        raise ValueError("Invalid parameter name.")
    if project.get_parameter(name) is not None:
        raise ValueError(f"Parameter '{name}' already exists")

    type_tag = str(body.get("type_tag") or "").strip()
    if not type_tag:
        raise ValueError("Parameter type is required.")
    if type_tag not in AUTHORABLE_PARAM_TYPES or type_tag not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {type_tag}")

    value = coerce_parameter_value(type_tag, body.get("value"))
    description = str(body.get("description") or "").strip()
    return GDLParameter(name=name, type_tag=type_tag, description=description, value=value)


def validate_authorable_parameter_name(project: HSFProject, name: str, *, current_name: str = "") -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(cleaned):
        raise ValueError("Invalid parameter name.")
    if cleaned != current_name and project.get_parameter(cleaned) is not None:
        raise ValueError(f"Parameter '{cleaned}' already exists")
    return cleaned


def validate_authorable_type(type_tag: str) -> str:
    cleaned = str(type_tag or "").strip()
    if not cleaned:
        raise ValueError("Parameter type is required.")
    if cleaned not in AUTHORABLE_PARAM_TYPES or cleaned not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {cleaned}")
    return cleaned


def coerce_parameter_value(type_tag: str, value: Any) -> str:
    if type_tag == "Boolean":
        if isinstance(value, bool):
            return "1" if value else "0"
        return "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"
    if type_tag == "Integer":
        return str(int(float(value or 0)))
    if type_tag in {"Length", "RealNum"}:
        return str(float(value or 0))
    return str(value or "")


def to_preview_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"true", "yes", "on"}:
        return 1.0
    if text.lower() in {"false", "no", "off"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None
