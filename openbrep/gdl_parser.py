"""
GDL Source Parser v0.4 — Parses .gdl files into HSFProject.

Converts raw GDL source files (human-written, AI-generated, or messy)
into a properly structured HSFProject object.

v0.4 change: Output is HSFProject (directory) instead of single XML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from openbrep.hsf_project import HSFProject, GDLParameter, ScriptType


# ── Section Detection Patterns ────────────────────────────

_SECTION_PATTERNS = [
    ("master", re.compile(r"(?:(?:MASTER\s*SCRIPT)|(?:主\s*脚本))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("param",  re.compile(r"(?:(?:PARAMETER\s+SCRIPT)|(?:参数\s*脚本))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("2d",     re.compile(r"(?:(?:2D\s*SCRIPT)|(?:二维\s*脚本)|(?:平面\s*脚本))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("3d",     re.compile(r"(?:(?:3D\s*SCRIPT)|(?:三维\s*脚本))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("ui",     re.compile(r"(?:(?:UI\s*SCRIPT)|(?:界面\s*脚本)|(?:INTERFACE\s*SCRIPT))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("pr",     re.compile(r"(?:(?:PROPERT(?:Y|IES)\s*SCRIPT)|(?:属性\s*脚本))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
    ("params", re.compile(r"(?:(?:PARAMETERS?(?:\s+LIST)?)|(?:参数\s*列表)|(?:参数列表))(?:\s*[（(].*?[)）])?", re.IGNORECASE)),
]

_HEADER_DECORATION = re.compile(r'^[=\-_*#\s]+|[=\-_*#\s]+$')

_SECTION_TO_SCRIPT = {
    "master": ScriptType.MASTER,
    "param":  ScriptType.PARAM,
    "2d":     ScriptType.SCRIPT_2D,
    "3d":     ScriptType.SCRIPT_3D,
    "ui":     ScriptType.UI,
    "pr":     ScriptType.PROPERTIES,
}

# ── Parameter Parsing ─────────────────────────────────────

_PARAM_PATTERN = re.compile(
    r'^!\s*(\w+)\s+'
    r'(Length|Integer|Boolean|RealNum|Angle|String|Material|'
    r'FillPattern|LineType|PenColor)\s+'
    r'("[^"]*"|\S+)'
    r'(?:\s+(.+))?',
    re.IGNORECASE
)


def parse_gdl_source(content: str, name: str = "Untitled") -> HSFProject:
    """
    Parse GDL source text into an HSFProject.

    Handles:
    - Comment-style parameter declarations
    - Section headers (Chinese + English)
    - Commented-out UI scripts (auto-uncomment)
    - Messy formatting
    """
    project = HSFProject(name)
    project.parameters = []

    lines = content.splitlines()

    # Extract metadata from header comments
    _extract_metadata(project, lines[:20])

    # Identify sections
    sections = _identify_sections(lines)

    # Parse parameters
    if "params" in sections:
        project.parameters = _parse_parameters(sections["params"])
    else:
        # Try to find parameters anywhere in the file
        project.parameters = _parse_parameters(lines)

    # Ensure A, B, ZZYZX exist
    _ensure_reserved_params(project)

    # Assign scripts
    for section_key, script_type in _SECTION_TO_SCRIPT.items():
        if section_key in sections:
            script_content = "\n".join(sections[section_key])
            # Auto-uncomment UI scripts
            if script_type == ScriptType.UI:
                script_content = _uncomment_script(script_content)
            script_content = _clean_script(script_content)
            if script_content.strip():
                project.scripts[script_type] = script_content

    # If no sections detected, treat entire content as 3D script
    if not project.scripts and not sections:
        cleaned = _clean_script(content)
        if cleaned.strip():
            project.scripts[ScriptType.SCRIPT_3D] = cleaned

    return project


def parse_gdl_file(path: str, encoding: Optional[str] = None) -> HSFProject:
    """
    Parse a .gdl file from disk into HSFProject.

    Tries UTF-8, then GBK, then Latin-1 as fallback encodings.
    """
    file_path = Path(path)
    name = file_path.stem

    content = None
    encodings = [encoding] if encoding else ["utf-8", "utf-8-sig", "gbk", "latin-1"]

    for enc in encodings:
        try:
            content = file_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if content is None:
        raise ValueError(f"Cannot read {path} with any supported encoding")

    return parse_gdl_source(content, name)


# ── Internal Helpers ──────────────────────────────────────

def _extract_metadata(project: HSFProject, header_lines: list[str]) -> None:
    """Extract object name, description, version from header comments."""
    for line in header_lines:
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue
        text = stripped.lstrip("! ").strip()

        # Object name
        name_match = re.match(r'(?:Object|Name|对象|名称)\s*[:：]\s*(.+)', text, re.IGNORECASE)
        if name_match:
            project.name = name_match.group(1).strip()
            project.root = project.work_dir / project.name
            continue

        # Description
        desc_match = re.match(r'(?:Description|描述|说明)\s*[:：]\s*(.+)', text, re.IGNORECASE)
        if desc_match:
            project.description = desc_match.group(1).strip()
            continue


def _normalize_section_header(line: str) -> str:
    """Normalize possible section header lines for robust matching."""
    text = line.strip()
    if not text:
        return ""
    if text.startswith("!"):
        text = text.lstrip("!").strip()
    text = _HEADER_DECORATION.sub("", text)
    text = text.strip("[]【】<>《》:：-_=*# ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _identify_sections(lines: list[str]) -> dict[str, list[str]]:
    """Split source into named sections based on header comments."""
    sections: dict[str, list[str]] = {}
    current_section: Optional[str] = None
    current_lines: list[str] = []

    for line in lines:
        candidate = _normalize_section_header(line)

        # Check if this line is a section header
        detected = None
        if candidate:
            for section_name, pattern in _SECTION_PATTERNS:
                if pattern.fullmatch(candidate):
                    detected = section_name
                    break

        if detected:
            # Save previous section
            if current_section and current_lines:
                sections[current_section] = current_lines
            current_section = detected
            current_lines = []
        elif current_section:
            current_lines.append(line)

    # Save last section
    if current_section and current_lines:
        sections[current_section] = current_lines

    return sections


def _parse_parameters(lines: list[str]) -> list[GDLParameter]:
    """Extract parameter declarations from comment lines."""
    params = []
    seen_names = set()

    for line in lines:
        stripped = line.strip()
        match = _PARAM_PATTERN.match(stripped)
        if not match:
            continue

        name = match.group(1)
        type_tag = match.group(2)
        value = match.group(3).strip('"')
        description = (match.group(4) or "").strip()

        if name in seen_names:
            continue
        seen_names.add(name)

        # Fix case for type tags
        type_tag = _normalize_type(type_tag)

        is_fixed = name in ("A", "B", "ZZYZX")

        params.append(GDLParameter(
            name=name,
            type_tag=type_tag,
            description=description,
            value=value,
            is_fixed=is_fixed,
        ))

    return params


def _normalize_type(type_tag: str) -> str:
    """Normalize parameter type tag to exact Graphisoft spelling."""
    type_map = {
        "length":      "Length",
        "integer":     "Integer",
        "boolean":     "Boolean",
        "realnum":     "RealNum",
        "angle":       "Angle",
        "string":      "String",
        "material":    "Material",
        "fillpattern": "FillPattern",
        "linetype":    "LineType",
        "pencolor":    "PenColor",
    }
    return type_map.get(type_tag.lower(), type_tag)


def _ensure_reserved_params(project: HSFProject) -> None:
    """Ensure A, B, ZZYZX exist in parameters."""
    existing_names = {p.name for p in project.parameters}

    defaults = [
        GDLParameter("A",     "Length", "Width",  "1.00", is_fixed=True),
        GDLParameter("B",     "Length", "Depth",  "1.00", is_fixed=True),
        GDLParameter("ZZYZX", "Length", "Height", "1.00", is_fixed=True),
    ]

    for default in defaults:
        if default.name not in existing_names:
            project.parameters.insert(0, default)


def _uncomment_script(content: str) -> str:
    """Uncomment lines that start with '! ' (commented-out scripts)."""
    lines = []
    commented_count = 0
    total_count = 0

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("! ") or stripped.startswith("!	"):
            commented_count += 1
        if stripped:
            total_count += 1

    # Only uncomment if majority of lines are commented
    if total_count > 0 and commented_count / total_count > 0.6:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("! "):
                lines.append(stripped[2:])
            elif stripped.startswith("!	"):
                lines.append(stripped[1:])
            else:
                lines.append(line)
        return "\n".join(lines)

    return content


def _clean_script(content: str) -> str:
    """Remove decorative separators and excessive blank lines."""
    lines = []
    prev_blank = False

    for line in content.splitlines():
        stripped = line.strip()

        # Skip pure separator lines
        if re.match(r'^[!=\-_*]{3,}\s*$', stripped):
            continue
        if re.match(r'^!\s*[-=_*]{3,}\s*$', stripped):
            continue

        # Collapse multiple blank lines
        if not stripped:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False

        lines.append(line)

    return "\n".join(lines).strip() + "\n"
