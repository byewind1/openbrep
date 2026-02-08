"""
GDL Source Parser — convert raw .gdl scripts to LP_XMLConverter XML.

This is the "drag & drop" workflow:
  1. User drops a .gdl file (human-written, AI-generated, or messy)
  2. Parser identifies sections (parameters, scripts) by convention
  3. Generates complete XML that LP_XMLConverter can compile
  4. Agent can then optimize/debug/compile it

The parser is deliberately FORGIVING — it handles:
  - Comment-style parameter declarations (! param Type value desc)
  - Section headers marked with comments (! === 3D SCRIPT ===)
  - Missing sections (fills in empty defaults)
  - Mixed Chinese/English comments
  - Code with or without END statements
  - Commented-out UI scripts (uncomments them)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ParsedParameter:
    """A parameter extracted from GDL source."""
    name: str
    type: str = "Length"
    value: str = "0"
    description: str = ""


@dataclass
class ParsedGDL:
    """Complete parsed representation of a GDL source file."""
    name: str = "Untitled"
    parameters: list[ParsedParameter] = field(default_factory=list)
    master_script: str = ""
    parameter_script: str = ""
    script_2d: str = ""
    script_3d: str = ""
    script_ui: str = ""
    script_pr: str = ""         # Property script
    version: str = "1.0.0"
    description: str = ""

    # Diagnostic info
    source_lines: int = 0
    parse_warnings: list[str] = field(default_factory=list)

    def to_xml(self) -> str:
        """Convert to LP_XMLConverter-compatible XML."""
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Symbol>", ""]

        # Parameters
        if self.parameters:
            parts.append("  <Parameters>")
            for p in self.parameters:
                parts.append("    <Parameter>")
                parts.append(f"      <n>{_xml_escape(p.name)}</n>")
                parts.append(f"      <Type>{_xml_escape(p.type)}</Type>")
                parts.append(f"      <Value>{_xml_escape(p.value)}</Value>")
                if p.description:
                    parts.append(f"      <Description>{_xml_escape(p.description)}</Description>")
                parts.append("    </Parameter>")
            parts.append("  </Parameters>")
            parts.append("")

        # Scripts — each wrapped in CDATA
        _script_sections = [
            ("Script_1D", self.master_script),
            ("Script_2D", self.script_2d),
            ("Script_3D", self.script_3d),
            ("Script_UI", self.script_ui),
            ("Script_PR", self.parameter_script + ("\n" + self.script_pr if self.script_pr else "")),
        ]

        for tag, content in _script_sections:
            content = content.strip()
            if content:
                parts.append(f"  <{tag}><![CDATA[")
                parts.append(content)
                parts.append(f"  ]]></{tag}>")
                parts.append("")

        parts.append("</Symbol>")
        return "\n".join(parts)


# ── Section Detection Patterns ─────────────────────────────────────────

# Patterns that identify section boundaries in GDL source files
_SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    # More specific patterns FIRST (order matters)
    ("param_scr",  re.compile(r"PARAMETER\s+SCRIPT", re.IGNORECASE)),
    ("property",   re.compile(r"PROPERTY\s*SCRIPT", re.IGNORECASE)),
    ("master",     re.compile(r"MASTER\s*SCRIPT", re.IGNORECASE)),
    ("2d",         re.compile(r"2D\s*SCRIPT", re.IGNORECASE)),
    ("3d",         re.compile(r"3D\s*SCRIPT", re.IGNORECASE)),
    ("ui",         re.compile(r"UI\s*SCRIPT", re.IGNORECASE)),
    # Params section (least specific — must come LAST to avoid false positives)
    ("params",     re.compile(r"(?:参数列表|参数\s*[（(]|PARAMETERS?\s*[（(LIST])", re.IGNORECASE)),
]

# Pattern for comment-style parameter declarations
# Matches: ! paramName  Type  value  description
_PARAM_PATTERN = re.compile(
    r"^!\s*(\w+)\s+"
    r"(Length|Integer|Boolean|RealNum|Angle|String|Material|FillPattern|LineType|Pencolor)\s+"
    r"(\S+)\s*(.*?)$",
    re.IGNORECASE,
)

# Pattern for quoted default values in parameter declarations
_PARAM_QUOTED = re.compile(
    r'^!\s*(\w+)\s+'
    r'(Material|String|FillPattern|LineType)\s+'
    r'"([^"]+)"\s*(.*?)$',
    re.IGNORECASE,
)


def parse_gdl_file(path: str | Path) -> ParsedGDL:
    """
    Parse a .gdl source file into structured representation.

    Args:
        path: Path to the .gdl file.

    Returns:
        ParsedGDL with all extracted sections.
    """
    p = Path(path)
    content = _read_with_fallback(p)
    name = p.stem

    result = parse_gdl_source(content)
    result.name = name
    return result


def parse_gdl_source(content: str) -> ParsedGDL:
    """
    Parse raw GDL source text into structured representation.

    This is the core parser. It:
    1. Splits the source into sections by detecting headers
    2. Extracts parameters from comment declarations
    3. Cleans up each script section
    4. Handles commented-out UI scripts
    """
    result = ParsedGDL()
    lines = content.splitlines()
    result.source_lines = len(lines)

    # Extract metadata from header comments
    for line in lines[:20]:
        if "对象名称" in line or "Object Name" in line.title():
            m = re.search(r"[：:]\s*(.+?)(?:\s*[（(]|$)", line)
            if m:
                result.name = m.group(1).strip()
        if "描述" in line or "Description" in line.title():
            m = re.search(r"[：:]\s*(.+)", line)
            if m:
                result.description = m.group(1).strip()
        if "版本" in line or "Version" in line.title():
            m = re.search(r"[：:]\s*([\d.]+)", line)
            if m:
                result.version = m.group(1)

    # Split into sections
    sections = _split_into_sections(lines)

    # Extract parameters
    if "params" in sections:
        result.parameters = _extract_parameters(sections["params"])

    # Assign scripts
    if "master" in sections:
        result.master_script = _clean_script(sections["master"])
    if "param_scr" in sections:
        result.parameter_script = _clean_script(sections["param_scr"])
    if "2d" in sections:
        result.script_2d = _clean_script(sections["2d"])
    if "3d" in sections:
        result.script_3d = _clean_script(sections["3d"])
    if "ui" in sections:
        result.script_ui = _clean_ui_script(sections["ui"])
    if "property" in sections:
        result.script_pr = _clean_script(sections["property"])

    # Merge parameter_script into param_scr if they ended up in different places
    # (Some files put VALUES in the parameter section, others in a separate block)

    # Validation warnings
    if not result.parameters:
        result.parse_warnings.append("No parameters detected. Default A/B/ZZYZX will be used.")
    if not result.script_3d:
        result.parse_warnings.append("No 3D script found. The object will have no geometry.")
    if result.master_script and "IF" in result.master_script:
        if_count = len(re.findall(r"\bIF\b", result.master_script))
        endif_count = len(re.findall(r"\bENDIF\b", result.master_script))
        then_count = len(re.findall(r"\bTHEN\b", result.master_script))
        # Single-line IF (IF x THEN y = z) doesn't need ENDIF
        # Multi-line IF needs ENDIF
        if if_count > endif_count and if_count > then_count:
            result.parse_warnings.append(
                f"Possible IF/ENDIF mismatch in Master Script (IF={if_count}, ENDIF={endif_count})"
            )

    return result


def _split_into_sections(lines: list[str]) -> dict[str, list[str]]:
    """Split source lines into named sections by detecting headers."""
    sections: dict[str, list[str]] = {}
    current_section: Optional[str] = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Check if this line is a section header
        detected = None
        if stripped.startswith("!") or stripped.startswith("==="):
            for section_name, pattern in _SECTION_PATTERNS:
                if pattern.search(stripped):
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

    # If no sections detected at all, treat entire content as 3D script
    if not sections:
        non_comment = [l for l in lines if l.strip() and not l.strip().startswith("!")]
        if non_comment:
            sections["3d"] = lines

    return sections


def _extract_parameters(lines: list[str]) -> list[ParsedParameter]:
    """Extract parameter definitions from comment-style declarations."""
    params = []
    seen_names = set()

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue

        # Try quoted pattern first (for Material, String types)
        m = _PARAM_QUOTED.match(stripped)
        if m:
            name, ptype, value, desc = m.groups()
            if name not in seen_names:
                params.append(ParsedParameter(
                    name=name.strip(),
                    type=ptype.strip(),
                    value=f'"{value.strip()}"',
                    description=desc.strip(),
                ))
                seen_names.add(name)
            continue

        # Try standard pattern
        m = _PARAM_PATTERN.match(stripped)
        if m:
            name, ptype, value, desc = m.groups()
            if name not in seen_names:
                params.append(ParsedParameter(
                    name=name.strip(),
                    type=ptype.strip(),
                    value=value.strip(),
                    description=desc.strip(),
                ))
                seen_names.add(name)

    return params


def _clean_script(lines: list[str]) -> str:
    """Clean a script section: remove section headers and trailing separators."""
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip separator lines
        if re.match(r"^!\s*[=\-]{5,}", stripped):
            continue
        # Skip blank section-header comments
        if stripped == "!":
            continue
        cleaned.append(line.rstrip())

    # Remove leading/trailing blank lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    return "\n".join(cleaned)


def _clean_ui_script(lines: list[str]) -> str:
    """
    Clean UI script section, handling commented-out code.

    Many GDL files have UI scripts entirely commented out with '!'.
    We detect this and uncomment them.
    """
    cleaned = []
    all_commented = True

    for line in lines:
        stripped = line.strip()
        if re.match(r"^!\s*[=\-]{5,}", stripped):
            continue
        if stripped == "!" or stripped == "":
            continue

        if stripped.startswith("!") and any(
            kw in stripped.upper() for kw in ["UI_", "PAGE", "SEPARATOR", "DIALOG"]
        ):
            # This is a commented-out UI command — uncomment it
            cleaned.append(re.sub(r"^!\s*", "", stripped))
        elif not stripped.startswith("!"):
            all_commented = False
            cleaned.append(stripped)

    if not cleaned:
        return ""

    return "\n".join(cleaned)


def _read_with_fallback(path: Path) -> str:
    """Read file with encoding fallback."""
    for enc in ["utf-8", "utf-8-sig", "gbk", "latin-1"]:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Could not decode {path}")


def _xml_escape(text: str) -> str:
    """Escape text for safe XML inclusion."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
