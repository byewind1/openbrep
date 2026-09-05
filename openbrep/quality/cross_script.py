"""Static, observer-only analysis of an HSF project's script contract.

This module deliberately does not interpret GDL.  It records what can be
proved from source text and marks everything else as unknown.  The result is
safe to put in a quality record: it never raises and it never emits a
verification warning.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_NAMES = ("1d.gdl", "2d.gdl", "3d.gdl", "vl.gdl", "ui.gdl")
PARAM_TYPES = {
    "Length",
    "Angle",
    "RealNum",
    "Integer",
    "Boolean",
    "String",
    "Material",
    "PenColor",
    "FillPattern",
    "LineType",
    "Title",
    "Separator",
}
GEOMETRY_WORDS = re.compile(
    r"\b(BLOCK|BRICK|CYLIND|CONE|SPHERE|PRISM|PRISM_|PGON|POLY2|PROJECT2|LINE2|RECT2|CIRCLE2|ARC2|BODY|VERT|EDGE|TUBE|RULED|REVOLVE|SWEEP)\b",
    re.I,
)
IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
DIRECTIVE = re.compile(r"\b(VALUES|PARAMETERS|LOCK)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.I)
STRING_LITERAL = re.compile(r"\"([^\"]*)\"")
RANGE_RE = re.compile(r"\bRANGE\s*(?:\[|\()\s*([^,;\]\)]+)\s*,\s*([^\]\)]+)", re.I)


@dataclass
class CrossScriptGraph:
    """Serializable graph and eligibility report.

    Lists contain plain dictionaries on purpose: the quality schema is JSON
    and older records must remain readable without importing this module.
    """

    status: str = "measured"
    parameters: list[dict[str, Any]] = field(default_factory=list)
    scripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    unknown_edges: list[dict[str, Any]] = field(default_factory=list)
    eligibility: dict[str, dict[str, Any]] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "parameters": self.parameters,
            "scripts": self.scripts,
            "edges": self.edges,
            "issues": self.issues,
            "unknown_edges": self.unknown_edges,
            "eligibility": self.eligibility,
            "coverage": self.coverage,
        }


def _issue(
    graph: CrossScriptGraph,
    kind: str,
    detail: str,
    file: str = "paramlist.xml",
    line: int | None = None,
) -> None:
    graph.issues.append({"kind": kind, "detail": detail, "file": file, "line": line})


def _unknown(graph: CrossScriptGraph, reason: str, file: str, line: int | None = None) -> None:
    graph.unknown_edges.append({"reason": reason, "file": file, "line": line})


def _clean_line(line: str) -> str:
    return line.split("!", 1)[0]


def _line_for(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return None


def _read_parameters(root: Path, graph: CrossScriptGraph) -> tuple[dict[str, dict[str, Any]], str]:
    path = root / "paramlist.xml"
    try:
        text = path.read_text(encoding="utf-8-sig")
        xml_root = ET.fromstring(text)
    except FileNotFoundError:
        _unknown(graph, "paramlist.xml is missing", "paramlist.xml")
        graph.status = "partial"
        return {}, ""
    except (OSError, UnicodeError, ET.ParseError) as exc:
        _issue(graph, "paramlist_parse_error", f"Could not parse paramlist.xml: {exc}")
        _unknown(graph, "paramlist.xml could not be parsed", "paramlist.xml")
        graph.status = "partial"
        return {}, ""

    params_elem = xml_root.find(".//Parameters")
    if params_elem is None:
        params_elem = xml_root
    params: dict[str, dict[str, Any]] = {}
    for elem in params_elem:
        type_tag = elem.tag.rsplit("}", 1)[-1]
        if type_tag not in PARAM_TYPES or type_tag in {"Title", "Separator"}:
            continue
        name = elem.get("Name", "")
        if not name:
            _unknown(graph, "parameter has no Name", "paramlist.xml")
            continue
        value_elem = elem.find("Value")
        value = (value_elem.text or "").strip() if value_elem is not None else ""
        if type_tag == "String" and len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        params[name] = {
            "name": name,
            "type": type_tag,
            "default": value,
            "source": {"file": "paramlist.xml", "line": _line_for(text, f'Name="{name}"')},
        }
    if not params:
        _unknown(graph, "no readable parameter declarations", "paramlist.xml")
        graph.status = "partial"
    graph.parameters = list(params.values())
    return params, text


def _identifiers(line: str, params: set[str]) -> set[str]:
    return {match.group(0) for match in IDENTIFIER.finditer(line) if match.group(0) in params}


def _scan_scripts(
    root: Path, params: dict[str, dict[str, Any]], graph: CrossScriptGraph
) -> dict[str, str]:
    texts: dict[str, str] = {}
    names = set(params)
    for script_name in SCRIPT_NAMES:
        path = root / "scripts" / script_name
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            graph.scripts[script_name] = {"read": [], "write": [], "missing": True}
            continue
        except (OSError, UnicodeError) as exc:
            _unknown(graph, f"could not read script: {exc}", f"scripts/{script_name}")
            graph.scripts[script_name] = {"read": [], "write": [], "partial": True}
            graph.status = "partial"
            continue
        texts[script_name] = text
        reads: list[dict[str, Any]] = []
        writes: list[dict[str, Any]] = []
        for line_no, original in enumerate(text.splitlines(), 1):
            line = _clean_line(original)
            if not line.strip():
                continue
            target = ASSIGNMENT.match(line)
            if target and target.group(1) in names:
                writes.append({"name": target.group(1), "line": line_no})
            for directive in DIRECTIVE.finditer(line):
                if directive.group(2) in names and directive.group(1).upper() == "PARAMETERS":
                    writes.append({"name": directive.group(2), "line": line_no})
            for name in sorted(_identifiers(line, names)):
                # A directive/assignment target is still useful as a write, but
                # it also appears in the expression when there is a RHS.
                if (
                    not target
                    or target.group(1) != name
                    or ASSIGNMENT.sub("", line, count=1).strip()
                ):
                    reads.append({"name": name, "line": line_no})
        graph.scripts[script_name] = {"read": reads, "write": writes, "missing": False}
    return texts


def _edges_and_directives(
    texts: dict[str, str], params: dict[str, dict[str, Any]], graph: CrossScriptGraph
) -> None:
    names = set(params)
    for script_name, text in texts.items():
        script_data = graph.scripts.get(script_name, {})
        for item in script_data.get("read", []):
            graph.edges.append(
                {
                    "kind": "script_read",
                    "from": item["name"],
                    "to": f"scripts/{script_name}",
                    "file": f"scripts/{script_name}",
                    "line": item["line"],
                }
            )
        for item in script_data.get("write", []):
            graph.edges.append(
                {
                    "kind": "script_write",
                    "from": f"scripts/{script_name}",
                    "to": item["name"],
                    "file": f"scripts/{script_name}",
                    "line": item["line"],
                }
            )
        for line_no, original in enumerate(text.splitlines(), 1):
            line = _clean_line(original)
            target = ASSIGNMENT.match(line)
            if target:
                refs = _identifiers(line, names)
                for ref in sorted(refs):
                    graph.edges.append(
                        {
                            "kind": (
                                "master_assignment"
                                if script_name == "1d.gdl" and target.group(1) in names
                                else "derived"
                            ),
                            "from": ref,
                            "to": target.group(1),
                            "file": f"scripts/{script_name}",
                            "line": line_no,
                        }
                    )
            for directive in DIRECTIVE.finditer(line):
                kind, target_name = directive.group(1).upper(), directive.group(2)
                if target_name not in names:
                    _issue(
                        graph,
                        "unknown_target",
                        f"{kind} targets unknown parameter '{target_name}'",
                        f"scripts/{script_name}",
                        line_no,
                    )
                else:
                    graph.edges.append(
                        {
                            "kind": kind.lower(),
                            "from": target_name,
                            "to": f"scripts/{script_name}",
                            "file": f"scripts/{script_name}",
                            "line": line_no,
                        }
                    )
                if kind == "VALUES":
                    declared_type = params.get(target_name, {}).get("type")
                    value_text = line[directive.end() :]
                    literals = STRING_LITERAL.findall(value_text)
                    if declared_type == "Boolean" and any(
                        v.lower() not in {"true", "false", "on", "off", "0", "1"} for v in literals
                    ):
                        _issue(
                            graph,
                            "type_incompatible",
                            f"VALUES for Boolean '{target_name}' contains string literal",
                            f"scripts/{script_name}",
                            line_no,
                        )


def _enum_issues(
    texts: dict[str, str], params: dict[str, dict[str, Any]], graph: CrossScriptGraph
) -> dict[str, list[Any]]:
    enums: dict[str, list[Any]] = {}
    for script_name, text in texts.items():
        for line_no, original in enumerate(text.splitlines(), 1):
            line = _clean_line(original)
            match = DIRECTIVE.search(line)
            if not match or match.group(1).upper() != "VALUES" or match.group(2) not in params:
                continue
            name = match.group(2)
            quoted = STRING_LITERAL.findall(line[match.end() :])
            if quoted:
                enums.setdefault(name, []).extend(quoted)
    for name, values in enums.items():
        branches: list[tuple[str, str, int]] = []
        for script_name, text in texts.items():
            if script_name not in {"2d.gdl", "3d.gdl"}:
                continue
            for line_no, original in enumerate(text.splitlines(), 1):
                line = _clean_line(original)
                if name not in line or "IF" not in line.upper():
                    continue
                for literal in STRING_LITERAL.findall(line):
                    branches.append((literal, f"scripts/{script_name}", line_no))
        for value in dict.fromkeys(values):
            if value not in {item[0] for item in branches}:
                _issue(
                    graph,
                    "enum_missing_branch",
                    f"VALUES item '{value}' for '{name}' has no 2D/3D branch",
                    "scripts/vl.gdl",
                    None,
                )
        for value, file, line in branches:
            if value not in values:
                _issue(
                    graph,
                    "enum_unknown_branch",
                    f"branch value '{value}' for '{name}' is not declared by VALUES",
                    file,
                    line,
                )
    return enums


def _eligibility(
    texts: dict[str, str],
    params: dict[str, dict[str, Any]],
    graph: CrossScriptGraph,
    enums: dict[str, list[Any]],
) -> None:
    for name, param in params.items():
        evidence: list[str] = []
        values: list[Any] = []
        all_refs = [
            (script, item)
            for script, data in graph.scripts.items()
            for kind in ("read", "write")
            for item in data.get(kind, [])
            if item["name"] == name
        ]
        geometry_refs = [
            (script, item)
            for script, item in all_refs
            if script in {"2d.gdl", "3d.gdl"}
            and GEOMETRY_WORDS.search(
                texts.get(script, "").splitlines()[item["line"] - 1] if texts.get(script) else ""
            )
        ]
        master_write = any(
            item["name"] == name for item in graph.scripts.get("1d.gdl", {}).get("write", [])
        )
        if param["type"] in {"Material", "PenColor", "FillPattern", "LineType"}:
            role = "material"
            evidence.append("material-like parameter type")
        elif geometry_refs:
            role = "derived" if master_write else "geometry_driver"
            evidence.append("referenced by a 2D/3D geometry command")
        elif all_refs and all(script == "ui.gdl" for script, _ in all_refs):
            role = "ui_only"
            evidence.append("only referenced by ui.gdl")
        elif param["type"] == "Boolean" and all_refs:
            role = "visibility"
            evidence.append("Boolean used in script condition")
        elif master_write:
            role = "derived"
            evidence.append("assigned by Master script")
        else:
            role = "unknown"
        if name in enums:
            values.extend(dict.fromkeys(enums[name]))
            evidence.append("vl.gdl VALUES enumeration")
        else:
            match = re.search(
                rf"\bVALUES\s+\"?{re.escape(name)}\"?[^\n]*", texts.get("vl.gdl", ""), re.I
            )
            if match:
                range_match = RANGE_RE.search(match.group(0))
                if range_match:
                    try:
                        lo, hi = float(range_match.group(1)), float(range_match.group(2))
                        values.extend([lo, (lo + hi) / 2, hi])
                        evidence.append("vl.gdl RANGE")
                    except ValueError:
                        _unknown(graph, "RANGE bounds are not numeric", "scripts/vl.gdl")
        if not values:
            if param["type"] == "Boolean":
                values = [0, 1]
            elif param["type"] in {"Length", "Angle", "RealNum", "Integer"}:
                values = [0, 1]
            elif param["default"]:
                values = [param["default"]]
        if (
            values
            and param["type"] in {"Length", "Angle", "RealNum", "Integer"}
            and values == [0, 1]
        ):
            evidence.append("default numeric domain; absolute step=1")
        graph.eligibility[name] = {"role": role, "test_values": values, "evidence_lines": evidence}


def build_cross_script_graph(project_root: Any) -> CrossScriptGraph:
    """Build a best-effort graph.  Missing or malformed input never raises."""
    graph = CrossScriptGraph()
    try:
        root = Path(project_root)
        params, _ = _read_parameters(root, graph)
        texts = _scan_scripts(root, params, graph)
        _edges_and_directives(texts, params, graph)
        enums = _enum_issues(texts, params, graph)
        _eligibility(texts, params, graph, enums)
        known = len(params)
        used = sum(1 for item in graph.eligibility.values() if item["role"] != "unknown")
        graph.coverage = {
            "parameters": known,
            "classified": used,
            "ratio": round(used / known, 3) if known else None,
        }
    except Exception as exc:  # observer-only boundary
        graph.status = "unavailable"
        _unknown(graph, f"analysis failed: {exc}", "project")
    return graph


def format_graph(graph: CrossScriptGraph) -> str:
    """Human-readable CLI view; intentionally contains no score."""
    lines = [f"cross-script scan: {graph.status}", f"coverage: {graph.coverage}", "", "parameters:"]
    for item in graph.parameters:
        eligibility = graph.eligibility.get(item["name"], {})
        role = eligibility.get("role", "unknown")
        test_values = eligibility.get("test_values", [])
        lines.append(f"  {item['name']} ({item['type']}) role={role} test_values={test_values}")
    lines.append("\nedges:")
    for edge in graph.edges:
        location = f"{edge.get('file')}:{edge.get('line')}"
        lines.append(f"  {edge['kind']}: {edge.get('from')} -> {edge.get('to')} ({location})")
    lines.append("\nissues:")
    lines.extend(
        f"  {issue['kind']}: {issue['detail']} ({issue['file']}:{issue.get('line') or '?'})"
        for issue in graph.issues
    )
    lines.append(f"\nunknown_edges: {len(graph.unknown_edges)}")
    return "\n".join(lines)


__all__ = ["CrossScriptGraph", "build_cross_script_graph", "format_graph"]
