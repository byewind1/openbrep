"""Lossless, atomic structured mutations for HSF ``paramlist.xml``.

The public operation is deliberately independent of workbench session state so
the UI and both MODIFY agent loops can share one domain implementation.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openbrep.hsf_project import VALID_PARAM_TYPES, GDLParameter, HSFProject
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


def _normalize_value(type_tag: str, value: Any) -> str:
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
        value = _normalize_value(type_tag, operation.get("value"))
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
        value = _normalize_value(param.type_tag, operation.get("value"))
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


def compact_result_json(result: ParameterMutationResult) -> str:
    """Stable model-facing result without echoing the full parameter table."""
    return json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":"))
