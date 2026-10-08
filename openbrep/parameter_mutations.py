"""Lossless, atomic structured mutations for HSF ``paramlist.xml``.

The public operation is deliberately independent of workbench session state so
the UI and both MODIFY agent loops can share one domain implementation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openbrep.hsf_project import VALID_PARAM_TYPES, GDLParameter, HSFProject
from openbrep.parameter_units import UnitValueError, normalize_typed_value
from openbrep.paramlist_builder import (
    _escape_attr,
    _format_value,
    clean_parameter_description,
    parameters_semantically_equal,
    parse_paramlist_xml,
    quoted_cdata,
    validate_paramlist,
)
from openbrep.source_fingerprint import compute_source_fingerprint

PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALUE_PARAMETER_TYPES = VALID_PARAM_TYPES - {"Title", "Separator"}
SUPPORTED_OPERATIONS = {"add", "set_value", "set_description", "delete"}
PROTECTED_PARAMETER_NAMES = {"A", "B", "ZZYZX"}


@dataclass
class ParameterMutationResult:
    ok: bool
    changed_parameters: list[dict[str, str]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    source_fingerprint: str | None = None
    error_code: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed_parameters": list(self.changed_parameters),
            "changed_files": list(self.changed_files),
            "source_fingerprint": self.source_fingerprint,
            "error_code": self.error_code,
            **({"error": self.error} if self.error else {}),
        }


class _MutationRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _NodeSpan:
    name: str
    tag: str
    start: int
    end: int
    text: str


_PARAMETERS_RE = re.compile(
    r"(?P<open><Parameters\b[^>]*>)(?P<body>.*?)(?P<close></Parameters\s*>)",
    re.DOTALL,
)
_NODE_RE = re.compile(
    r"(?P<indent>^[ \t]*)(?:"
    r"<(?P<tag>Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material|Title)\b"
    r"(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>"
    r"|<(?P<sep>Separator)\b[^>]*/>)"
    r"[ \t]*(?:\r?\n|$)",
    re.MULTILINE | re.DOTALL,
)
_NAME_ATTR_RE = re.compile(r"\bName\s*=\s*(['\"])(.*?)\1", re.DOTALL)


def _fail(code: str, message: str, *, fingerprint: str | None = None) -> ParameterMutationResult:
    return ParameterMutationResult(
        ok=False,
        source_fingerprint=fingerprint,
        error_code=code,
        error=message,
    )


def _current_fingerprint(project: HSFProject) -> str | None:
    try:
        if not project.root.is_dir():
            return None
        return compute_source_fingerprint(project.root)
    except Exception:
        return None


def _document_spans(text: str) -> tuple[re.Match[str], list[_NodeSpan]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise _MutationRejected("INVALID_PARAMLIST", f"paramlist.xml is not valid XML: {exc}")
    params = root.find(".//Parameters")
    if params is None:
        raise _MutationRejected("LOSSLESS_UNSUPPORTED", "paramlist.xml has no Parameters node")
    section = _PARAMETERS_RE.search(text)
    if section is None:
        raise _MutationRejected(
            "LOSSLESS_UNSUPPORTED", "Parameters node cannot be mapped losslessly"
        )

    body = section.group("body")
    matches = list(_NODE_RE.finditer(body))
    residue = _NODE_RE.sub("", body)
    if residue.strip():
        raise _MutationRejected(
            "LOSSLESS_UNSUPPORTED",
            "Parameters contains nodes or text that cannot be edited losslessly",
        )
    children = list(params)
    if len(matches) != len(children):
        raise _MutationRejected("LOSSLESS_UNSUPPORTED", "Parameter node mapping is incomplete")

    spans: list[_NodeSpan] = []
    names: set[str] = set()
    body_start = section.start("body")
    for child, match in zip(children, matches):
        tag = match.group("tag") or match.group("sep") or ""
        if child.tag != tag or tag not in VALID_PARAM_TYPES:
            raise _MutationRejected(
                "LOSSLESS_UNSUPPORTED", f"Unsupported parameter node: {child.tag}"
            )
        name = child.get("Name", "") if tag != "Separator" else "_separator"
        if tag != "Separator":
            attr_match = _NAME_ATTR_RE.search(match.group("attrs") or "")
            if attr_match is None or not name:
                raise _MutationRejected(
                    "LOSSLESS_UNSUPPORTED", f"{tag} node has no lossless Name mapping"
                )
        if name in names and tag != "Separator":
            raise _MutationRejected(
                "DUPLICATE_PARAMETER", f"Parameter '{name}' appears more than once"
            )
        names.add(name)
        spans.append(
            _NodeSpan(
                name=name,
                tag=tag,
                start=body_start + match.start(),
                end=body_start + match.end(),
                text=match.group(0),
            )
        )
    return section, spans


def _normalize_value(type_tag: str, value: Any, unit: Any = None) -> str:
    """值规整：无 unit = 旧语义（值已是 GDL 内部单位，不按大小重新解释）；
    显式 unit = U01-A typed 路径（唯一单位表 openbrep/parameter_units.py）。"""
    if unit:
        result = normalize_typed_value(type_tag, value, unit=unit)
        if isinstance(result, UnitValueError):
            raise _MutationRejected(result.code, result.message)
        return result.canonical
    if type_tag == "String":
        return str(value if value is not None else "")
    raw = str(value if value is not None else "").strip()
    if type_tag == "Boolean":
        if isinstance(value, bool):
            return "1" if value else "0"
        low = raw.lower()
        if low in {"1", "true", "yes", "on"}:
            return "1"
        if low in {"0", "false", "no", "off"}:
            return "0"
        raise _MutationRejected("INVALID_VALUE", "Boolean value must be 0/1 or true/false")
    if type_tag in {"Integer", "PenColor", "Material", "FillPattern", "LineType"}:
        try:
            number = float(raw)
            integer = int(number)
        except (TypeError, ValueError, OverflowError):
            raise _MutationRejected(
                "INVALID_VALUE", f"{type_tag} value must be an integer"
            ) from None
        if number != integer:
            raise _MutationRejected("INVALID_VALUE", f"{type_tag} value must be an integer")
        return str(integer)
    if type_tag in {"Length", "Angle", "RealNum"}:
        try:
            float(raw)
        except (TypeError, ValueError):
            raise _MutationRejected("INVALID_VALUE", f"{type_tag} value must be numeric") from None
        return _format_value(type_tag, raw)
    raise _MutationRejected("INVALID_TYPE", f"Unsupported parameter type: {type_tag}")


def _value_xml(type_tag: str, value: str) -> str:
    return quoted_cdata(value) if type_tag == "String" else value


def _replace_child(node: str, child: str, replacement: str, *, indent: str) -> str:
    pattern = re.compile(rf"<{child}\b[^>]*>.*?</{child}\s*>", re.DOTALL)
    matches = list(pattern.finditer(node))
    rendered = f"<{child}>{replacement}</{child}>"
    if len(matches) > 1:
        raise _MutationRejected("LOSSLESS_UNSUPPORTED", f"Parameter has multiple {child} nodes")
    if matches:
        match = matches[0]
        return node[: match.start()] + rendered + node[match.end() :]
    close = re.search(r"</[A-Za-z][A-Za-z0-9_]*>\s*(?:\r?\n)?$", node)
    if close is None:
        raise _MutationRejected("LOSSLESS_UNSUPPORTED", "Parameter closing tag cannot be mapped")
    newline = "\r\n" if "\r\n" in node else "\n"
    return node[: close.start()] + f"{indent}{rendered}{newline}" + node[close.start() :]


def _node_indent(node: str) -> tuple[str, str]:
    first = re.match(r"([ \t]*)<", node)
    outer = first.group(1) if first else "\t\t"
    return outer, outer + "\t"


def _render_new_node(param: GDLParameter, indent: str, newline: str) -> str:
    inner = indent + "\t"
    description = quoted_cdata(clean_parameter_description(param.description, param.type_tag))
    value = _value_xml(param.type_tag, _format_value(param.type_tag, param.value))
    return (
        newline.join(
            [
                f'{indent}<{param.type_tag} Name="{_escape_attr(param.name)}">',
                f"{inner}<Description>{description}</Description>",
                f"{inner}<Value>{value}</Value>",
                f"{indent}</{param.type_tag}>",
            ]
        )
        + newline
    )


def _script_references(project: HSFProject, name: str) -> list[str]:
    token = re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    hits: list[str] = []
    for script_type, content in project.scripts.items():
        for line_no, line in enumerate((content or "").splitlines(), start=1):
            code = line.split("!", 1)[0]
            if token.search(code):
                hits.append(f"scripts/{script_type.value}:{line_no}")
    return hits


def _apply_to_text(
    text: str,
    parameters: list[GDLParameter],
    operation: dict[str, Any],
    project: HSFProject,
) -> tuple[str, list[GDLParameter], dict[str, str]]:
    op = str(operation.get("op") or "").strip()
    if op not in SUPPORTED_OPERATIONS:
        raise _MutationRejected(
            "UNSUPPORTED_OPERATION", f"Unsupported operation: {op or '<empty>'}"
        )
    name = str(operation.get("name") or "").strip()
    if not name:
        raise _MutationRejected("INVALID_NAME", "Parameter name is required")
    if not PARAMETER_NAME_RE.match(name):
        raise _MutationRejected("INVALID_NAME", f"Invalid parameter name: {name!r}")

    section, spans = _document_spans(text)
    by_name = {span.name: span for span in spans if span.tag != "Separator"}
    by_param = {param.name: param for param in parameters}

    if op == "add":
        if name in by_name or name in by_param:
            raise _MutationRejected("DUPLICATE_PARAMETER", f"Parameter '{name}' already exists")
        type_tag = str(operation.get("type") or operation.get("type_tag") or "").strip()
        if type_tag not in VALUE_PARAMETER_TYPES:
            raise _MutationRejected("INVALID_TYPE", f"Unsupported parameter type: {type_tag}")
        value = _normalize_value(type_tag, operation.get("value"), operation.get("unit"))
        param = GDLParameter(
            name=name,
            type_tag=type_tag,
            value=value,
            description=str(operation.get("description") or ""),
        )
        candidate = parameters + [param]
        issues = validate_paramlist(candidate)
        if issues:
            raise _MutationRejected("INVALID_PARAMETER", issues[0])
        newline = "\r\n" if "\r\n" in text else "\n"
        indent = re.match(r"([ \t]*)", spans[0].text).group(1) if spans else "\t\t"
        insert_at = section.start("close")
        new_text = text[:insert_at] + _render_new_node(param, indent, newline) + text[insert_at:]
        return new_text, candidate, {"op": op, "name": name}

    param = by_param.get(name)
    span = by_name.get(name)
    if param is None or span is None:
        raise _MutationRejected("PARAMETER_NOT_FOUND", f"Parameter '{name}' was not found")
    if op == "delete":
        if param.is_fixed or name in PROTECTED_PARAMETER_NAMES:
            raise _MutationRejected(
                "PROTECTED_PARAMETER", f"Protected parameter '{name}' cannot be deleted"
            )
        refs = _script_references(project, name)
        if refs:
            raise _MutationRejected(
                "PARAMETER_IN_USE",
                f"Parameter '{name}' is still referenced by {', '.join(refs[:8])}",
            )
        candidate = [item for item in parameters if item.name != name]
        return text[: span.start] + text[span.end :], candidate, {"op": op, "name": name}

    replacement = span.text
    outer_indent, inner_indent = _node_indent(replacement)
    candidate = copy.deepcopy(parameters)
    candidate_param = next(item for item in candidate if item.name == name)
    if op == "set_value":
        value = _normalize_value(param.type_tag, operation.get("value"), operation.get("unit"))
        replacement = _replace_child(
            replacement,
            "Value",
            _value_xml(param.type_tag, value),
            indent=inner_indent,
        )
        candidate_param.value = value
    else:
        description = str(operation.get("description") or "")
        cleaned = clean_parameter_description(description, param.type_tag)
        replacement = _replace_child(
            replacement,
            "Description",
            quoted_cdata(cleaned),
            indent=inner_indent,
        )
        candidate_param.description = cleaned
    del outer_indent
    return text[: span.start] + replacement + text[span.end :], candidate, {"op": op, "name": name}


def _atomic_write(path: Path, data: bytes, *, replace_fn: Callable[[str, str], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_fn(temp_name, str(path))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def mutate_parameters(
    project: HSFProject,
    *,
    expected_source_fingerprint: str,
    operations: Any,
    replace_fn: Callable[[str, str], None] = os.replace,
    before_commit: Callable[[], None] | None = None,
) -> ParameterMutationResult:
    """Validate a complete batch in memory, then atomically replace paramlist.

    On every failure the project's parameter objects and original file bytes
    remain unchanged.
    """
    current = _current_fingerprint(project)
    if current is None:
        return _fail("SOURCE_UNAVAILABLE", "Project source is not saved on disk")
    if not expected_source_fingerprint or expected_source_fingerprint != current:
        return _fail(
            "SOURCE_CHANGED",
            "Project source changed; read parameters again before editing",
            fingerprint=current,
        )
    if not isinstance(operations, list) or not operations:
        return _fail(
            "INVALID_OPERATIONS",
            "operations must be a non-empty array",
            fingerprint=current,
        )

    path = project.root / "paramlist.xml"
    try:
        original = path.read_bytes()
        had_bom = original.startswith(b"\xef\xbb\xbf")
        working_text = original.decode("utf-8-sig")
    except Exception as exc:
        return _fail("INVALID_PARAMLIST", f"Cannot read paramlist.xml: {exc}", fingerprint=current)
    working_parameters = copy.deepcopy(project.parameters)
    changed: list[dict[str, str]] = []

    try:
        for raw_operation in operations:
            if not isinstance(raw_operation, dict):
                raise _MutationRejected("INVALID_OPERATION", "Each operation must be an object")
            working_text, working_parameters, change = _apply_to_text(
                working_text,
                working_parameters,
                raw_operation,
                project,
            )
            changed.append(change)
        parsed = parse_paramlist_xml(working_text)
        if not parameters_semantically_equal(parsed, working_parameters):
            raise _MutationRejected(
                "LOSSLESS_UNSUPPORTED",
                "Edited paramlist cannot be represented without losing parameter semantics",
            )
        issues = validate_paramlist(parsed)
        if issues:
            raise _MutationRejected("INVALID_PARAMETER", issues[0])
        # Recheck all managed source immediately before the atomic replacement.
        if compute_source_fingerprint(project.root) != expected_source_fingerprint:
            raise _MutationRejected(
                "SOURCE_CHANGED", "Project source changed during parameter edit"
            )
        encoded = working_text.encode("utf-8")
        if had_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        if encoded == original:
            return ParameterMutationResult(
                ok=True,
                source_fingerprint=current,
            )
        if before_commit is not None:
            before_commit()
        _atomic_write(path, encoded, replace_fn=replace_fn)
    except _MutationRejected as exc:
        return _fail(exc.code, exc.message, fingerprint=_current_fingerprint(project))
    except Exception as exc:
        return _fail(
            "SAVE_FAILED",
            f"Could not save paramlist.xml atomically: {exc}",
            fingerprint=current,
        )

    # Disk commit succeeded: only now publish the new in-memory value.
    project.parameters = parsed
    project._paramlist_raw = working_text
    project._paramlist_had_bom = had_bom
    new_fingerprint = compute_source_fingerprint(project.root)
    return ParameterMutationResult(
        ok=True,
        changed_parameters=changed,
        changed_files=["paramlist.xml"],
        source_fingerprint=new_fingerprint,
    )


def _contract_parameter_error(
    project: HSFProject,
    spec: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    """Validate deterministic parameter edits against an adopted ObjectSpec.

    The returned spec is a proposed metadata update (for example, a user-added
    typed parameter); current values never become ObjectSpec defaults.
    """
    updated = copy.deepcopy(spec)
    params = updated.get("params") if isinstance(updated.get("params"), list) else []
    by_name = {str(item.get("gdl_name") or ""): item for item in params if isinstance(item, dict)}
    current_parameters = {param.name: param for param in project.parameters}
    values: dict[str, Any] = {}
    for item in params:
        if not isinstance(item, dict):
            continue
        name = str(item.get("gdl_name") or "")
        param_id = str(item.get("param_id") or "")
        source_param = current_parameters.get(name)
        if source_param is not None:
            values[param_id] = source_param.value

    for operation in operations:
        op = str(operation.get("op") or "")
        name = str(operation.get("name") or "")
        if op in {"rename", "rename_param", "type_change"}:
            return "CONTRACT_REPLAN_REQUIRED", "重命名或改类型会改变合同身份，请先重规划并确认", updated
        if op == "add":
            if name in by_name:
                return "CONTRACT_PARAMETER_CONFLICT", f"参数 {name} 已在对象合同中", updated
            type_tag = str(operation.get("type") or "")
            if type_tag not in {"Length", "Angle", "RealNum", "Integer", "Boolean", "String"}:
                return "CONTRACT_REPLAN_REQUIRED", f"合同不支持新增类型 {type_tag}，请先重规划", updated
            slug = re.sub(r"[^a-z0-9_]+", "_", name.casefold()).strip("_") or "parameter"
            param_id = f"p.{slug}"
            if any(str(item.get("param_id")) == param_id for item in params):
                param_id = f"{param_id}_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"
            declared: dict[str, Any] = {
                "param_id": param_id,
                "gdl_name": name,
                "type": type_tag,
                "unit": "m" if type_tag == "Length" else "deg" if type_tag == "Angle" else None,
                "description": str(operation.get("description") or ""),
                "enum_values": [],
                "default_value": None,
                "required": False,
            }
            params.append(declared)
            by_name[name] = declared
            normalized_value = _normalize_value(type_tag, operation.get("value"), operation.get("unit"))
            values[param_id] = normalized_value
            current_parameters[name] = GDLParameter(
                name=name, type_tag=type_tag, description=str(operation.get("description") or ""),
                value=normalized_value,
            )
            continue

        contract_param = by_name.get(name)
        if contract_param is None:
            return "CONTRACT_REPLAN_REQUIRED", f"参数 {name} 不在已采用合同中，请先检查并更新合同", updated
        source_param = current_parameters.get(name)
        if source_param is None:
            return "CONTRACT_PARAMETER_MISSING", f"合同参数 {name} 在当前 HSF 中不存在", updated
        expected_type = str(contract_param.get("type") or "")
        actual_type = str(source_param.type_tag)
        compatible = expected_type == actual_type or (expected_type == "enum" and actual_type == "String")
        if not compatible:
            return "CONTRACT_TYPE_MISMATCH", f"参数 {name} 类型为 {actual_type}，合同要求 {expected_type}", updated

        if op == "set_value":
            value = _normalize_value(actual_type, operation.get("value"), operation.get("unit"))
            enum_values = contract_param.get("enum_values") or []
            if expected_type == "enum" and value not in [str(item) for item in enum_values]:
                return "CONTRACT_VALUE_OUT_OF_RANGE", f"参数 {name} 的值 {value!r} 不在合同枚举范围内", updated
            values[str(contract_param.get("param_id") or "")] = value
            current_parameters[name] = copy.copy(source_param)
            current_parameters[name].value = value
        elif op == "delete":
            if bool(contract_param.get("required")):
                return "CONTRACT_REQUIRED_PARAMETER", f"必需参数 {name} 不能删除", updated
            param_id = str(contract_param.get("param_id") or "")
            relations = updated.get("relations") or []
            if any(
                isinstance(relation, dict)
                and relation.get("status") == "defined"
                and (relation.get("left") == param_id
                     or (relation.get("right") or {}).get("param") == param_id)
                for relation in relations
            ):
                return "CONTRACT_REPLAN_REQUIRED", f"参数 {name} 参与已声明关系，请先重规划合同", updated
            params[:] = [item for item in params if item is not contract_param]
            by_name.pop(name, None)
            values.pop(param_id, None)
            current_parameters.pop(name, None)
        elif op == "set_description":
            contract_param["description"] = str(operation.get("description") or "")
        else:
            return "CONTRACT_REPLAN_REQUIRED", f"参数操作 {op} 暂不支持合同安全变更", updated

    relation_ops = {"==": lambda left, right: left == right, "!=": lambda left, right: left != right,
                    ">": lambda left, right: left > right, ">=": lambda left, right: left >= right,
                    "<": lambda left, right: left < right, "<=": lambda left, right: left <= right}
    for relation in updated.get("relations") or []:
        if not isinstance(relation, dict) or relation.get("status") != "defined":
            continue
        left_id = str(relation.get("left") or "")
        right = relation.get("right") or {}
        if left_id not in values:
            continue
        right_value = values.get(str(right.get("param") or "")) if "param" in right else right.get("const")
        if right_value is None:
            continue
        left_value = values[left_id]
        try:
            left_number = float(left_value)
            right_number = float(right_value)
            left_value, right_value = left_number, right_number
        except (TypeError, ValueError):
            left_value, right_value = str(left_value), str(right_value)
        comparator = relation_ops.get(str(relation.get("op") or ""))
        if comparator is not None and not comparator(left_value, right_value):
            return "CONTRACT_VALUE_OUT_OF_RANGE", f"参数值违反合同关系 {relation.get('relation_id') or ''}", updated
    return "", "", updated


def _mutate_project_definition(
    project: HSFProject,
    *,
    expected_source_fingerprint: str,
    operations: list[dict[str, Any]],
    before_commit: Callable[[], None] | None,
    commit_executor: Callable[[Callable[[], Any]], Any] | None,
) -> ParameterMutationResult:
    """Transactional path for parameter identity/type edits that touch scripts."""
    from openbrep.contracts.project_store import commit_project_state
    from openbrep.naming_alignment import _is_reserved, replace_identifier

    candidate = copy.deepcopy(project)
    changed: list[dict[str, str]] = []
    changed_scripts: set[str] = set()
    try:
        for operation in operations:
            op = str(operation.get("op") or "")
            name = str(operation.get("name") or "")
            if op == "rename":
                new_name = str(operation.get("new_name") or "")
                param = candidate.get_parameter(name)
                if param is None:
                    raise _MutationRejected("PARAMETER_NOT_FOUND", f"Parameter '{name}' was not found")
                if not PARAMETER_NAME_RE.fullmatch(new_name):
                    raise _MutationRejected("INVALID_NAME", f"Invalid parameter name: {new_name!r}")
                if candidate.get_parameter(new_name) is not None:
                    raise _MutationRejected("DUPLICATE_PARAMETER", f"Parameter '{new_name}' already exists")
                if param.is_fixed or _is_reserved(name) or _is_reserved(new_name):
                    raise _MutationRejected("PROTECTED_PARAMETER", f"Reserved parameter '{name}' cannot be renamed")
                param.name = new_name
                for script_type, content in list(candidate.scripts.items()):
                    updated, count = replace_identifier(content, name, new_name)
                    if count:
                        candidate.scripts[script_type] = updated
                        changed_scripts.add(script_type.value)
                changed.append({"op": op, "name": name, "new_name": new_name})
            elif op == "type_change":
                param = candidate.get_parameter(name)
                type_tag = str(operation.get("type") or "")
                if param is None:
                    raise _MutationRejected("PARAMETER_NOT_FOUND", f"Parameter '{name}' was not found")
                if type_tag not in VALUE_PARAMETER_TYPES:
                    raise _MutationRejected("INVALID_TYPE", f"Unsupported parameter type: {type_tag}")
                if param.is_fixed and type_tag != "Length":
                    raise _MutationRejected("PROTECTED_PARAMETER", f"Reserved parameter '{name}' must remain Length")
                param.type_tag = type_tag
                param.value = _normalize_value(
                    type_tag,
                    operation.get("value", param.value),
                    operation.get("unit"),
                )
                changed.append({"op": op, "name": name, "type": type_tag})
            elif op == "set_value":
                param = candidate.get_parameter(name)
                if param is None:
                    raise _MutationRejected("PARAMETER_NOT_FOUND", f"Parameter '{name}' was not found")
                param.value = _normalize_value(param.type_tag, operation.get("value"), operation.get("unit"))
                changed.append({"op": op, "name": name})
            elif op == "set_description":
                param = candidate.get_parameter(name)
                if param is None:
                    raise _MutationRejected("PARAMETER_NOT_FOUND", f"Parameter '{name}' was not found")
                param.description = clean_parameter_description(
                    str(operation.get("description") or ""), param.type_tag,
                )
                changed.append({"op": op, "name": name})
            else:
                raise _MutationRejected("UNSUPPORTED_OPERATION", f"Unsupported parameter operation: {op}")
    except _MutationRejected as exc:
        return _fail(exc.code, exc.message, fingerprint=expected_source_fingerprint)
    except Exception as exc:
        return _fail("INVALID_OPERATION", str(exc), fingerprint=expected_source_fingerprint)

    if candidate.parameters == project.parameters and candidate.scripts == project.scripts:
        return ParameterMutationResult(ok=True, source_fingerprint=expected_source_fingerprint)

    def write_candidate() -> None:
        if before_commit is not None:
            before_commit()
        candidate.save_to_disk()

    def commit_pair():
        return commit_project_state(project, None, source_writer=write_candidate)

    try:
        committed = commit_executor(commit_pair) if commit_executor else commit_pair()
    except Exception as exc:
        return _fail("COMMIT_REJECTED", str(exc), fingerprint=_current_fingerprint(project))
    if committed is None or not committed.ok:
        return _fail("SOURCE_COMMIT_FAILED", getattr(committed, "error", "commit not executed"),
                     fingerprint=_current_fingerprint(project))

    # HSFProject state is published only after the transaction commits.
    project.parameters = candidate.parameters
    project.scripts = candidate.scripts
    for attr in ("_paramlist_raw", "_paramlist_had_bom"):
        if hasattr(candidate, attr):
            setattr(project, attr, getattr(candidate, attr))
    changed_files = ["paramlist.xml", *(f"scripts/{name}" for name in changed_scripts)]
    return ParameterMutationResult(
        ok=True,
        changed_parameters=changed,
        changed_files=sorted(set(changed_files)),
        source_fingerprint=committed.source_fingerprint,
    )


def mutate_project_parameters(
    project: HSFProject,
    *,
    expected_source_fingerprint: str,
    operations: Any,
    before_commit: Callable[[], None] | None = None,
    replace_fn: Callable[[str, str], None] = os.replace,
    commit_executor: Callable[[Callable[[], Any]], Any] | None = None,
    mutation_fn: Callable[..., ParameterMutationResult] = mutate_parameters,
) -> ParameterMutationResult:
    """Shared validate→snapshot→commit→invalidate coordinator for parameter writes.

    When an adopted contract is fresh, validate against its declared parameter
    types, required fields, enums and relations, then atomically commit source
    plus the still-valid contract. A source-bound import Observation is rebuilt
    after mutation so its facts and fingerprint stay paired. Missing contracts
    retain the established parameter-only write path; stale/invalid contracts
    fail closed and ask the caller to review/re-adopt first.
    """
    current = _current_fingerprint(project)
    if current is None:
        return _fail("SOURCE_UNAVAILABLE", "Project source is not saved on disk")
    if current != expected_source_fingerprint:
        return _fail("SOURCE_CHANGED", "Project source changed; read parameters again before editing", fingerprint=current)
    if not isinstance(operations, list) or not operations:
        return _fail("INVALID_OPERATIONS", "operations must be a non-empty array", fingerprint=current)

    from openbrep.contracts.project_store import commit_project_state, load_project_contract

    try:
        contract = load_project_contract(project.root)
    except Exception as exc:
        return _fail("CONTRACT_INVALID", f"无法读取对象合同：{exc}", fingerprint=current)
    if contract.status == "missing":
        if any(str(item.get("op") or "") in {"rename", "type_change"}
               for item in operations if isinstance(item, dict)):
            return _mutate_project_definition(
                project,
                expected_source_fingerprint=expected_source_fingerprint,
                operations=operations,
                before_commit=before_commit,
                commit_executor=commit_executor,
            )
        def write_without_contract() -> ParameterMutationResult:
            return mutation_fn(
                project,
                expected_source_fingerprint=expected_source_fingerprint,
                operations=operations,
                before_commit=before_commit,
                replace_fn=replace_fn,
            )

        try:
            result = commit_executor(write_without_contract) if commit_executor else write_without_contract()
        except _MutationRejected as exc:
            return _fail(exc.code, exc.message, fingerprint=_current_fingerprint(project))
        except Exception as exc:
            return _fail("COMMIT_REJECTED", str(exc), fingerprint=_current_fingerprint(project))
        return result if isinstance(result, ParameterMutationResult) else _fail(
            "COMMIT_REJECTED", "提交协调器未执行参数写入", fingerprint=_current_fingerprint(project)
        )
    if contract.status != "fresh" or contract.object_spec is None:
        return _fail(
            "CONTRACT_STALE" if contract.status == "stale" else "CONTRACT_INVALID",
            "对象合同已过期或无效；请检查导入事实并重新采用后再修改参数",
            fingerprint=current,
        )
    if not all(isinstance(item, dict) for item in operations):
        return _fail("INVALID_OPERATION", "Each operation must be an object", fingerprint=current)
    try:
        rejection = _contract_parameter_error(project, contract.object_spec, operations)
    except _MutationRejected as exc:
        return _fail(exc.code, exc.message, fingerprint=current)
    code, message, updated_spec = rejection
    if code:
        return _fail(code, message, fingerprint=current)

    mutation_holder: dict[str, ParameterMutationResult] = {}

    def write_source() -> None:
        mutation = mutation_fn(
            project,
            expected_source_fingerprint=expected_source_fingerprint,
            operations=operations,
            before_commit=before_commit,
            replace_fn=replace_fn,
        )
        mutation_holder["result"] = mutation
        if not mutation.ok:
            raise RuntimeError(f"{mutation.error_code}: {mutation.error or 'parameter mutation failed'}")

    next_observation = copy.deepcopy(contract.observation)
    original_parameters = copy.deepcopy(project.parameters)
    original_paramlist_raw = getattr(project, "_paramlist_raw", None)
    original_paramlist_had_bom = getattr(project, "_paramlist_had_bom", False)
    if next_observation and next_observation.get("source") == "hsf_import":
        # Construct after the source mutation, inside the source/spec transaction.
        def write_source_and_refresh_observation() -> None:
            write_source()
            from openbrep.contracts.import_adapter import build_import_candidate

            refreshed = build_import_candidate(project)
            updated_spec["source_observation_id"] = refreshed["observation"]["observation_id"]
            next_observation.clear()
            next_observation.update(refreshed["observation"])
    else:
        write_source_and_refresh_observation = write_source

    def commit_pair():
        return commit_project_state(
            project,
            updated_spec,
            source_writer=write_source_and_refresh_observation,
            observation=next_observation,
        )

    try:
        committed = commit_executor(commit_pair) if commit_executor else commit_pair()
    except _MutationRejected as exc:
        return _fail(exc.code, exc.message, fingerprint=_current_fingerprint(project))
    except Exception as exc:
        return _fail("COMMIT_REJECTED", str(exc), fingerprint=_current_fingerprint(project))
    if committed is None:
        return _fail("COMMIT_REJECTED", "提交协调器未执行参数写入", fingerprint=_current_fingerprint(project))
    mutation = mutation_holder.get("result")
    if not committed.ok:
        # The source/spec transaction restores disk state. Mirror that rollback
        # in HSFProject's live parameter cache if failure occurred after the
        # paramlist replacement but before the contract was committed.
        project.parameters = original_parameters
        project._paramlist_raw = original_paramlist_raw
        project._paramlist_had_bom = original_paramlist_had_bom
        if mutation is not None and not mutation.ok:
            return mutation
        return _fail("SOURCE_SPEC_COMMIT_FAILED", committed.error, fingerprint=_current_fingerprint(project))
    if mutation is None:
        return _fail("SOURCE_SPEC_COMMIT_FAILED", "参数写入未执行", fingerprint=current)
    mutation.source_fingerprint = committed.source_fingerprint
    return mutation


def compact_result_json(result: ParameterMutationResult) -> str:
    """Stable model-facing result without echoing the full parameter table."""
    return json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":"))
