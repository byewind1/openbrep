"""
Context Surgery — feed the LLM only what it needs to see.

A real GDL XML file can be 3000+ lines. Stuffing all of it into the prompt
wastes tokens and dilutes attention. This module extracts only the sections
relevant to the user's instruction, while always preserving Parameters
(the "global context" that every script depends on).

Design principle: "Separation of Concerns" for context windows.
- Changing material? → Parameters + Script_PR
- Fixing 2D symbol? → Parameters + Script_2D
- Adding 3D geometry? → Parameters + Script_3D
- Modifying UI panel? → Parameters + Script_UI
- General/unclear? → Full file (safe fallback)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


# ── Intent-to-Section mapping ──────────────────────────────────────────

_SECTION_TRIGGERS: dict[str, list[str]] = {
    "Script_3D": [
        "3d", "geometry", "prism", "revolve", "extrude", "tube", "block", "cone",
        "sphere", "cutplane", "几何", "三维", "形体", "造型", "百叶",
        "material", "surface", "材质", "表面",
    ],
    "Script_2D": [
        "2d", "symbol", "floor plan", "plan view", "line2", "rect2", "poly2",
        "平面", "符号", "平面图", "线",
    ],
    "Script_UI": [
        "ui", "interface", "panel", "dialog", "infield", "button", "popup",
        "界面", "面板", "菜单", "选项",
    ],
    "Script_PR": [
        "parameter", "property", "values", "range", "constraint", "default",
        "参数", "属性", "约束", "范围", "默认值",
    ],
    "Script_1D": [
        "1d", "master", "initialization", "主脚本", "初始化",
    ],
}

# These sections are ALWAYS included regardless of intent
_ALWAYS_INCLUDE = {"Parameters"}


@dataclass
class ContextSlice:
    """A focused slice of the XML file for LLM consumption."""
    parameters_xml: str             # Always included
    relevant_sections: dict[str, str]  # tag → content
    omitted_sections: list[str]     # Tags that were filtered out
    total_chars: int                # Original file size
    sliced_chars: int               # Size after slicing
    is_full: bool = False           # True if no filtering applied

    @property
    def savings_pct(self) -> int:
        """Percentage of tokens saved by slicing."""
        if self.total_chars == 0:
            return 0
        return int((1 - self.sliced_chars / self.total_chars) * 100)

    def to_xml_string(self) -> str:
        """Reconstruct a focused XML string for the LLM prompt."""
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Symbol>"]

        if self.parameters_xml:
            parts.append(self.parameters_xml)

        for tag, content in self.relevant_sections.items():
            parts.append(content)

        if self.omitted_sections:
            parts.append(
                f"  <!-- OMITTED sections (not relevant to this task): "
                f"{', '.join(self.omitted_sections)} -->"
            )

        parts.append("</Symbol>")
        return "\n".join(parts)


def detect_relevant_sections(instruction: str) -> set[str]:
    """
    Detect which XML sections are relevant based on the user's instruction.

    Returns:
        Set of section tag names (e.g., {"Script_3D", "Script_UI"}).
        Empty set means "include everything" (fallback for unclear intent).
    """
    instruction_lower = instruction.lower()
    matched = set()

    for section, triggers in _SECTION_TRIGGERS.items():
        for trigger in triggers:
            if trigger in instruction_lower:
                matched.add(section)
                break

    return matched


def slice_context(xml_content: str, instruction: str) -> ContextSlice:
    """
    Extract only the relevant sections from a GDL XML file.

    Args:
        xml_content: Full XML source content.
        instruction: User's natural language instruction.

    Returns:
        ContextSlice with focused content for the LLM.
    """
    total_chars = len(xml_content)

    # Determine which sections to include
    relevant_tags = detect_relevant_sections(instruction)

    # If no specific sections detected, return full content
    if not relevant_tags:
        return ContextSlice(
            parameters_xml="",
            relevant_sections={},
            omitted_sections=[],
            total_chars=total_chars,
            sliced_chars=total_chars,
            is_full=True,
        )

    # Parse XML
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        # If XML is broken, return full content (let other validators catch it)
        return ContextSlice(
            parameters_xml="",
            relevant_sections={},
            omitted_sections=[],
            total_chars=total_chars,
            sliced_chars=total_chars,
            is_full=True,
        )

    # Extract Parameters (always included)
    params_elem = root.find("Parameters")
    params_xml = ""
    if params_elem is not None:
        params_xml = _element_to_string(params_elem)

    # Extract relevant script sections
    all_script_tags = [
        "Script_1D", "Script_2D", "Script_3D", "Script_UI", "Script_PR",
        "Script_BWM", "Script_FWM",
    ]

    relevant_sections = {}
    omitted_sections = []

    for tag in all_script_tags:
        elem = root.find(tag)
        if elem is None:
            continue

        if tag in relevant_tags:
            relevant_sections[tag] = _element_to_string(elem)
        else:
            # Check if the section has substantial content worth omitting
            text = elem.text or ""
            if len(text.strip()) > 10:  # Non-trivial content
                omitted_sections.append(tag)

    # Calculate sliced size
    sliced_content = params_xml + "".join(relevant_sections.values())
    sliced_chars = len(sliced_content) + 100  # overhead for wrapping

    return ContextSlice(
        parameters_xml=params_xml,
        relevant_sections=relevant_sections,
        omitted_sections=omitted_sections,
        total_chars=total_chars,
        sliced_chars=sliced_chars,
    )


def _element_to_string(elem: ET.Element) -> str:
    """Convert an ElementTree element back to an XML string."""
    # Use a simple approach that preserves CDATA
    return ET.tostring(elem, encoding="unicode", xml_declaration=False)
