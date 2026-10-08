from __future__ import annotations

import logging
import re
from typing import Any

from openbrep.hsf_project import VALID_PARAM_TYPES, GDLParameter, HSFProject
from openbrep.parameter_mutations import mutate_project_parameters
from openbrep.parameter_units import UnitValueError, normalize_typed_value
from openbrep.paramlist_builder import validate_paramlist
from openbrep.source_fingerprint import (
    compute_source_fingerprint,
    expected_project_source_fingerprint,
)
from openbrep.values_declarations import parse_values_declarations  # compatibility export

logger = logging.getLogger(__name__)

GDL_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AUTHORABLE_PARAM_TYPES = {"Length", "RealNum", "Integer", "Boolean", "String"}


def normalize_typed_entry(
    type_tag: str, value: Any, unit: Any, field_path: str
) -> tuple[str | None, dict[str, Any] | None]:
    """U01-A typed 值归一：成功返回 (paramlist 规范值, None)，拒绝返回 (None, 错误响应)。

    显式单位（如 {"value": 900, "unit": "mm"}）经 openbrep.parameter_units
    换算为 GDL 内部单位（Length=米、Angle=度）；错误带机器码 + 字段路径。
    """
    result = normalize_typed_value(type_tag, value, unit=unit, field_path=field_path)
    if isinstance(result, UnitValueError):
        return None, {
            "ok": False,
            "error": result.message,
            "error_code": result.code,
            "field_path": result.field_path,
        }
    return result.canonical, None


class WorkbenchProjectParameterService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def ui_layout(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """L0b：解析 ui.gdl → Archicad 风格参数面板控件树。body.parameters
        为草稿覆盖，只影响 IF 分支裁剪，不落盘。"""
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project first."}
        from openbrep.hsf_project import ScriptType
        from openbrep.ui_layout import parse_ui_layout

        overrides = body.get("parameters") if isinstance(body, dict) else None
        if not isinstance(overrides, dict):
            overrides = None
        layout = parse_ui_layout(
            self.session.project.get_script(ScriptType.UI) or "",
            parameters=parameter_values(self.session.project, overrides),
            values_declarations=parse_values_declarations(
                self.session.project.get_script(ScriptType.PARAM) or ""
            ),
        )
        payload = layout.to_dict()
        payload["ok"] = True
        return payload

    def effective_parameters(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate saved parameters plus optional draft overrides without writes."""
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project first."}
        from openbrep.hsf_project import ScriptType
        from openbrep.parameter_observation import observe_parameters

        overrides = body.get("parameters") if isinstance(body, dict) else None
        if not isinstance(overrides, dict):
            overrides = {}
        project = self.session.project
        observation = observe_parameters(
            project.parameters,
            project.get_script(ScriptType.MASTER) or "",
            parameter_values(project, overrides),
            overrides=overrides,
            parameter_script=project.get_script(ScriptType.PARAM) or "",
        )
        return {
            "ok": True,
            "project_path": str(project.root),
            "project_epoch": getattr(self.session, "project_epoch", None),
            "source_fingerprint": compute_source_fingerprint(project.root),
            "supported": observation.supported,
            "parameters": [item.to_dict() for item in observation.parameters],
            "diagnostics": [
                {
                    "code": item.code,
                    "line": item.line,
                    "command": item.command,
                    "message": item.message,
                }
                for item in observation.diagnostics
            ],
        }

    def _values_for(self, name: str) -> dict[str, Any] | None:
        """当前项目 vl.gdl 中该参数的 VALUES 声明（无项目/无声明 → None）。"""
        if self.session.project is None:
            return None
        from openbrep.hsf_project import ScriptType

        return parse_values_declarations(
            self.session.project.get_script(ScriptType.PARAM)
        ).get(name)

    def _snapshot_before_parameter_write(self, operations: list[dict[str, Any]]) -> None:
        """Create the before revision at the shared coordinator's commit edge."""
        project = self.session.project
        if project is None:
            return
        try:
            from openbrep.revisions import create_revision, get_latest_revision_id

            create_revision(
                project.root,
                message="auto: before parameter edit",
                gsm_name=project.name,
                metadata={"parameter_mutation": {"source": "workbench", "operations": operations}},
                trigger="parameter_edit",
                intent="MODIFY",
                changed_files=["paramlist.xml"],
                parent_revision_id=get_latest_revision_id(project.root),
            )
        except Exception as exc:
            # Snapshot failures remain visible in logs but preserve the prior
            # explicit parameter-edit behavior: a valid edit can still commit.
            logger.warning("parameter edit snapshot failed: %s", exc)

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before applying parameters."}
        known = {param.name for param in self.session.project.parameters}
        types = {param.name: param.type_tag for param in self.session.project.parameters}
        # U01-A：typed 表单 {"value": 900, "unit": "mm"} 先归一为内部单位标量，
        # 旧标量表单（number/str = 内部单位）逐字节走原语义，不按大小重新解释。
        normalized_changes: dict[str, Any] = {}
        for name, spec in changes.items():
            if isinstance(spec, dict) and ("value" in spec or "unit" in spec):
                if name not in known:
                    continue  # 未知参数名保持既有静默跳过语义
                canonical, error = normalize_typed_entry(
                    types[name], spec.get("value"), spec.get("unit"),
                    f"parameters.{name}.value",
                )
                if error:
                    return error
                normalized_changes[name] = canonical
            else:
                normalized_changes[name] = spec
        operations = [
            {"op": "set_value", "name": name, "value": value}
            for name, value in normalized_changes.items()
            if name in known
        ]
        if operations and self.session.source_path is not None:
            result = mutate_project_parameters(
                self.session.project,
                expected_source_fingerprint=expected_project_source_fingerprint(self.session.project),
                operations=operations,
                before_commit=lambda: self._snapshot_before_parameter_write(operations),
            )
            if not result.ok:
                return {"ok": False, "error": result.error, "error_code": result.error_code}
            changed = {op["name"]: normalized_changes[op["name"]] for op in operations}
        else:
            changed = apply_parameter_values(self.session.project, normalized_changes)
        return {"ok": True, "changed": changed, **self.session.snapshot()}

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before adding parameters."}
        if self.session.source_path is not None:
            result = mutate_project_parameters(
                self.session.project,
                expected_source_fingerprint=expected_project_source_fingerprint(self.session.project),
                operations=[{
                    "op": "add",
                    "name": body.get("name"),
                    "type": body.get("type_tag"),
                    "value": body.get("value"),
                    "description": body.get("description"),
                    "unit": body.get("unit"),
                }],
                before_commit=lambda: self._snapshot_before_parameter_write([{
                    "op": "add", "name": body.get("name"), "type": body.get("type_tag"),
                }]),
            )
            if not result.ok:
                return {"ok": False, "error": result.error, "error_code": result.error_code}
            param = self.session.project.get_parameter(str(body.get("name") or "").strip())
        else:
            payload = body
            if body.get("unit"):
                canonical, error = normalize_typed_entry(
                    str(body.get("type_tag") or ""), body.get("value"), body.get("unit"), "value"
                )
                if error:
                    return error
                payload = {**body, "value": canonical}
            try:
                param = build_parameter_from_authoring_request(self.session.project, payload)
                self.session.project.add_parameter(param)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        assert param is not None
        return {
            "ok": True,
            "added": parameter_to_dict(param, values=self._values_for(param.name)),
            **self.session.snapshot(),
        }

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before updating parameters."}
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
            if self.session.source_path is not None:
                operations = []
                if new_name != param.name:
                    operations.append({"op": "rename", "name": param.name, "new_name": new_name})
                if new_type != param.type_tag:
                    operation = {"op": "type_change", "name": new_name, "type": new_type}
                    if "value" in body:
                        operation["value"] = body.get("value")
                        operation["unit"] = body.get("unit")
                    operations.append(operation)
                elif "value" in body:
                    operations.append({
                        "op": "set_value",
                        "name": new_name,
                        "value": body.get("value"),
                        "unit": body.get("unit"),
                    })
                if "description" in body:
                    operations.append({
                        "op": "set_description",
                        "name": new_name,
                        "description": body.get("description"),
                    })
                if operations:
                    result = mutate_project_parameters(
                        self.session.project,
                        expected_source_fingerprint=expected_project_source_fingerprint(self.session.project),
                        operations=operations,
                        before_commit=lambda: self._snapshot_before_parameter_write(operations),
                    )
                    if not result.ok:
                        return {"ok": False, "error": result.error, "error_code": result.error_code}
                    param = self.session.project.get_parameter(new_name)
                if param is None:
                    return {"ok": False, "error": f"Parameter '{new_name}' not found"}
            else:
                if "value" in body:
                    if body.get("unit"):
                        canonical, error = normalize_typed_entry(
                            new_type, body.get("value"), body.get("unit"), "value"
                        )
                        if error:
                            return error
                        param.value = canonical
                    else:
                        param.value = coerce_parameter_value(new_type, body.get("value"))
                if "description" in body:
                    param.description = str(body.get("description") or "").strip()
            param.name = new_name
            param.type_tag = new_type
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if self.session.source_path is not None:
            return {
                "ok": True,
                "updated": parameter_to_dict(param, values=self._values_for(param.name)),
                **self.session.snapshot(),
            }
        return {
            "ok": True,
            "updated": parameter_to_dict(param, values=self._values_for(param.name)),
            **self.session.snapshot(),
        }

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before deleting parameters."}
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}
        if self.session.source_path is not None:
            result = mutate_project_parameters(
                self.session.project,
                expected_source_fingerprint=expected_project_source_fingerprint(self.session.project),
                operations=[{"op": "delete", "name": name}],
                before_commit=lambda: self._snapshot_before_parameter_write([{"op": "delete", "name": name}]),
            )
            if not result.ok:
                return {"ok": False, "error": result.error, "error_code": result.error_code}
        else:
            if param.is_fixed:
                return {"ok": False, "error": f"Fixed parameter '{name}' cannot be deleted"}
            self.session.project.remove_parameter(name)
        return {
            "ok": True,
            "deleted": name,
            **self.session.snapshot(),
        }

    def validate_project_parameters(self) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "issues": []}
        return {
            "ok": True,
            "issues": validate_paramlist(self.session.project.parameters or []),
        }


# ── P11: vl.gdl VALUES 枚举解析 ────────────────────────────────
# 轻量行解析：只关心 `VALUES "name" ...` 声明，其余行（注释/IF/LOCK 等）
# 一律跳过。解析失败只影响本参数（不进入结果 → payload 字段为 None），
# 绝不抛出异常，不影响既有参数链路。


def parameter_to_dict(param: GDLParameter, values: dict[str, Any] | None = None) -> dict[str, Any]:
    options = values.get("options") if values else None
    range_values = values.get("range") if values else None
    return {
        "name": param.name,
        "type": param.type_tag,
        "type_tag": param.type_tag,
        "description": param.description,
        "value": param.value,
        "is_fixed": bool(param.is_fixed),
        # P11：vl.gdl VALUES 枚举（options）与 RANGE 约束（range）透传；
        # 无声明/解析失败时为 None，不改变既有 payload 形状。
        "options": options,
        "range": range_values,
    }


def parameter_values(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for param in project.parameters:
        numeric = to_preview_number(param.value)
        if numeric is not None:
            values[param.name.upper()] = numeric
        elif isinstance(param.value, str) and param.value.strip():
            # P9：非数值字符串参数（如 String 型 pattern_type）原样保留，
            # 供预览器字符串比较（IF pattern_type = "直棂"）使用。
            values[param.name.upper()] = param.value
    for key, value in (overrides or {}).items():
        numeric = to_preview_number(value)
        if numeric is not None:
            values[str(key).upper()] = numeric
        elif isinstance(value, str) and value.strip():
            values[str(key).upper()] = value
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
