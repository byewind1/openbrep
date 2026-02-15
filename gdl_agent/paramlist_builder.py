"""
Paramlist XML Builder — Generates and parses paramlist.xml for HSF.

This is the most error-prone part of GDL development. LLMs frequently
use wrong type tags (Float instead of RealNum, Text instead of String).
This module enforces the Graphisoft XML Schema strictly.

Key rule: Description MUST use CDATA wrapping.
Key rule: All files MUST be UTF-8 with BOM.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from gdl_agent.hsf_project import GDLParameter, VALID_PARAM_TYPES


def build_paramlist_xml(parameters: list[GDLParameter]) -> str:
    """
    Build paramlist.xml content from parameter list.

    Format matches real LP_XMLConverter libpart2hsf output:
    - Root: <ParamSection>
    - Header: <ParamSectHeader> with defaults
    - Parameters wrapper: <Parameters SectVersion="27" ...>
    - Description uses CDATA with quoted string: <![CDATA["text"]]>
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ParamSection>',
        '\t<ParamSectHeader>',
        '\t\t<AutoHotspots>false</AutoHotspots>',
        '\t\t<StatBits>',
        '\t\t\t<STBit_FixSize/>',
        '\t\t</StatBits>',
        '\t\t<WDLeftFrame>0</WDLeftFrame>',
        '\t\t<WDRightFrame>0</WDRightFrame>',
        '\t\t<WDTopFrame>0</WDTopFrame>',
        '\t\t<WDBotFrame>0</WDBotFrame>',
        '\t\t<LayFlags>65535</LayFlags>',
        '\t\t<WDMirrorThickness>0</WDMirrorThickness>',
        '\t\t<WDWallInset>0</WDWallInset>',
        '\t</ParamSectHeader>',
        '\t<Parameters SectVersion="27" SectionFlags="0" SubIdent="0">',
    ]

    for param in parameters:
        tag = param.type_tag

        # Title and Separator have no value
        if tag == "Title":
            lines.append(f'\t\t<Title Name="{_escape_attr(param.name)}">')
            lines.append(f'\t\t\t<Description><![CDATA["{param.description}"]]></Description>')
            lines.append(f'\t\t</Title>')
            continue

        if tag == "Separator":
            lines.append(f'\t\t<Separator/>')
            continue

        # Standard parameter
        lines.append(f'\t\t<{tag} Name="{_escape_attr(param.name)}">')
        lines.append(f'\t\t\t<Description><![CDATA["{param.description}"]]></Description>')

        if param.is_fixed:
            lines.append(f'\t\t\t<Fix/>')

        if param.flags:
            lines.append(f'\t\t\t<Flags>')
            for flag in param.flags:
                lines.append(f'\t\t\t\t<{flag}/>')
            lines.append(f'\t\t\t</Flags>')

        # Value formatting
        value = _format_value(tag, param.value)
        lines.append(f'\t\t\t<Value>{value}</Value>')

        lines.append(f'\t\t</{tag}>')

    lines.append('\t</Parameters>')
    lines.append('</ParamSection>')
    return "\n".join(lines) + "\n"


def parse_paramlist_xml(content: str) -> list[GDLParameter]:
    """
    Parse paramlist.xml content into parameter list.

    Handles real LP_XMLConverter format:
    <ParamSection>
      <ParamSectHeader>...</ParamSectHeader>
      <Parameters ...>
        <Length Name="A">...</Length>
      </Parameters>
    </ParamSection>
    """
    parameters = []

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return parameters

    # Find <Parameters> wrapper (real format) or fall back to root children
    params_elem = root.find(".//Parameters")
    if params_elem is None:
        params_elem = root  # fallback for simplified format

    for elem in params_elem:
        tag = elem.tag

        if tag == "Separator":
            parameters.append(GDLParameter(
                name="_separator",
                type_tag="Separator",
            ))
            continue

        if tag not in VALID_PARAM_TYPES:
            continue

        name = elem.get("Name", "")

        # Extract description from CDATA (format: <![CDATA["text"]]>)
        desc_el = elem.find("Description")
        description = ""
        if desc_el is not None and desc_el.text:
            description = desc_el.text.strip()
            # Strip surrounding quotes from CDATA content
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]

        # Extract value
        val_el = elem.find("Value")
        value = ""
        if val_el is not None and val_el.text:
            value = val_el.text.strip()

        # Check for Fix tag
        is_fixed = elem.find("Fix") is not None

        # Collect flags from <Flags> wrapper
        flags = []
        flags_elem = elem.find("Flags")
        if flags_elem is not None:
            for flag_child in flags_elem:
                flags.append(flag_child.tag)

        parameters.append(GDLParameter(
            name=name,
            type_tag=tag,
            description=description,
            value=value,
            is_fixed=is_fixed,
            flags=flags,
        ))

    return parameters


def validate_paramlist(parameters: list[GDLParameter]) -> list[str]:
    """
    Validate parameter list for common errors.

    Returns list of error/warning messages.
    """
    issues = []
    names_seen = set()

    for i, param in enumerate(parameters):
        # Duplicate names
        if param.name in names_seen:
            issues.append(f"Duplicate parameter name: '{param.name}'")
        names_seen.add(param.name)

        # Invalid type
        if param.type_tag not in VALID_PARAM_TYPES:
            issues.append(
                f"Invalid type '{param.type_tag}' for parameter '{param.name}'"
            )

        # Boolean value check
        if param.type_tag == "Boolean":
            if param.value not in ("0", "1"):
                issues.append(
                    f"Boolean '{param.name}' value must be '0' or '1', "
                    f"got '{param.value}'"
                )

        # Integer value check
        if param.type_tag == "Integer":
            try:
                int(param.value)
            except ValueError:
                issues.append(
                    f"Integer '{param.name}' has non-integer value: '{param.value}'"
                )

        # Length/Angle/RealNum value check
        if param.type_tag in ("Length", "Angle", "RealNum"):
            try:
                float(param.value)
            except ValueError:
                issues.append(
                    f"{param.type_tag} '{param.name}' has non-numeric value: "
                    f"'{param.value}'"
                )

        # Reserved parameter checks
        if param.name in ("A", "B", "ZZYZX"):
            if param.type_tag != "Length":
                issues.append(
                    f"Reserved parameter '{param.name}' must be Length type, "
                    f"got '{param.type_tag}'"
                )
            if not param.is_fixed:
                issues.append(
                    f"Reserved parameter '{param.name}' should have <Fix/> tag"
                )

    return issues


# ── Internal Helpers ──────────────────────────────────────

def _escape_attr(s: str) -> str:
    """Escape string for use in XML attributes."""
    return (s
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _format_value(type_tag: str, value: str) -> str:
    """Format parameter value according to its type.
    
    CRITICAL: Material, FillPattern, LineType in HSF paramlist.xml
    must be INTEGER indices, not string names.
    LP_XMLConverter rejects string values like "Wood - Oak".
    """
    if type_tag in ("Length", "Angle", "RealNum"):
        try:
            return f"{float(value):.6g}"
        except ValueError:
            return value
    if type_tag in ("Integer", "Boolean", "PenColor"):
        try:
            return str(int(float(value)))
        except ValueError:
            return value
    if type_tag in ("Material", "FillPattern", "LineType"):
        # Must be integer index. If it's a string name, use default index.
        try:
            return str(int(float(value)))
        except ValueError:
            # String name like "Wood - Oak" → can't use in HSF, use default
            _MATERIAL_DEFAULTS = {
                "Material": "0",      # 0 = General Surface
                "FillPattern": "0",
                "LineType": "0",
            }
            return _MATERIAL_DEFAULTS.get(type_tag, "0")
    # String — pass through
    return value
