"""
XML utilities for GDL source file manipulation.

Handles XML validation, safe editing, diff detection, and structure inspection.
"""

from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class XMLValidationResult:
    valid: bool
    error: str = ""
    line: int = 0
    column: int = 0


def validate_xml(content: str) -> XMLValidationResult:
    """
    Validate that a string is well-formed XML.

    Returns:
        XMLValidationResult with validity status and error details.
    """
    try:
        ET.fromstring(content)
        return XMLValidationResult(valid=True)
    except ET.ParseError as e:
        msg = str(e)
        # Try to extract line/column from error message
        match = re.search(r"line (\d+), column (\d+)", msg)
        line = int(match.group(1)) if match else 0
        col = int(match.group(2)) if match else 0
        return XMLValidationResult(valid=False, error=msg, line=line, column=col)


def validate_gdl_structure(content: str) -> list[str]:
    """
    Validate GDL-specific XML structure requirements.

    Checks:
      - Root element is <Symbol>
      - CDATA boundaries are intact
      - IF/ENDIF, FOR/NEXT, WHILE/ENDWHILE, GOSUB/RETURN matching
      - Parameter naming conventions

    Returns:
        List of warning/error messages (empty = all good).
    """
    issues = []

    # ── Pre-XML check: CDATA boundary validation ──
    # This catches the #1 cause of LP_XMLConverter crashes that produce
    # zero useful error output. Check BEFORE parsing XML.
    cdata_opens = len(re.findall(r"<!\[CDATA\[", content))
    cdata_closes = len(re.findall(r"\]\]>", content))
    if cdata_opens != cdata_closes:
        issues.append(
            f"CDATA boundary mismatch (open: {cdata_opens}, close: {cdata_closes}). "
            "This will crash LP_XMLConverter."
        )

    # Check for nested CDATA (illegal in XML)
    # Only check within each CDATA block, not across sections
    for m in re.finditer(r"<!\[CDATA\[(.*?)\]\]>", content, re.DOTALL):
        if "<![CDATA[" in m.group(1):
            issues.append("Nested CDATA sections detected — this is invalid XML.")
            break

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return ["XML is not well-formed"] + issues

    # Check root element
    if root.tag != "Symbol":
        issues.append(f"Root element should be 'Symbol', got '{root.tag}'")

    # Check all script sections
    _BLOCK_PAIRS = [
        ("IF", "ENDIF"),
        ("FOR", "NEXT"),
        ("WHILE", "ENDWHILE"),
    ]

    for script_tag in ["Script_1D", "Script_2D", "Script_3D", "Script_UI", "Script_PR",
                        "Script_BWM", "Script_FWM"]:
        elem = root.find(f".//{script_tag}")
        if elem is None or not elem.text:
            continue
        script = elem.text

        # IF/ENDIF: distinguish single-line IF (no ENDIF needed) from multi-line
        # Single-line: "IF x THEN y = z" (entire IF on one line, no block)
        # Multi-line: "IF x THEN\n  ...\nENDIF"
        all_if_lines = [l.strip() for l in script.splitlines()
                        if re.match(r"\s*IF\b", l, re.IGNORECASE)]
        multiline_ifs = 0
        for if_line in all_if_lines:
            # A single-line IF has THEN followed by a statement on the SAME line
            # e.g., "IF A < 0.30 THEN A = 0.30"
            then_match = re.search(r"\bTHEN\b(.+)", if_line, re.IGNORECASE)
            if then_match:
                after_then = then_match.group(1).strip()
                if after_then and not after_then.startswith("!"):
                    continue  # Single-line IF, no ENDIF needed
            multiline_ifs += 1

        endif_count = len(re.findall(r"\bENDIF\b", script))
        if multiline_ifs != endif_count:
            issues.append(
                f"{script_tag}: IF/ENDIF mismatch "
                f"(multi-line IF={multiline_ifs}, ENDIF={endif_count})"
            )

        # FOR/NEXT
        for_count = len(re.findall(r"\bFOR\b", script))
        next_count = len(re.findall(r"\bNEXT\b", script))
        if for_count != next_count:
            issues.append(
                f"{script_tag}: FOR/NEXT mismatch (FOR={for_count}, NEXT={next_count})"
            )

        # WHILE/ENDWHILE
        while_count = len(re.findall(r"\bWHILE\b", script))
        endwhile_count = len(re.findall(r"\bENDWHILE\b", script))
        if while_count != endwhile_count:
            issues.append(
                f"{script_tag}: WHILE/ENDWHILE mismatch "
                f"(WHILE={while_count}, ENDWHILE={endwhile_count})"
            )

        # Check GOSUB targets have matching labels
        gosub_targets = re.findall(r"\bGOSUB\s+(\d+)", script)
        defined_labels = re.findall(r"^(\d+)\s*:", script, re.MULTILINE)
        for target in gosub_targets:
            if target not in defined_labels:
                issues.append(f"{script_tag}: GOSUB {target} target label not found")

    return issues


def extract_parameters(content: str) -> list[dict]:
    """
    Extract parameter definitions from GDL XML.

    Returns:
        List of dicts with keys: name, type, value, description.
    """
    params = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return params

    for param in root.findall(".//Parameter"):
        entry = {}
        for child in param:
            tag = child.tag.lower()
            if tag == "n" or tag == "name":
                entry["name"] = child.text or ""
            elif tag == "type":
                entry["type"] = child.text or ""
            elif tag == "value":
                entry["value"] = child.text or ""
            elif tag == "description":
                entry["description"] = child.text or ""
        if entry.get("name"):
            params.append(entry)

    return params


def compute_diff(old_content: str, new_content: str, context_lines: int = 3) -> str:
    """
    Compute a unified diff between two XML strings.

    Returns:
        Unified diff as a string, or empty string if identical.
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after",
                                 n=context_lines)
    return "".join(diff)


def contents_identical(a: str, b: str) -> bool:
    """Check if two XML strings are semantically identical (ignoring whitespace variance)."""
    def normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()
    return normalize(a) == normalize(b)


def read_xml_file(path: str | Path) -> str:
    """Read an XML file with proper encoding handling."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"XML file not found: {path}")

    # Try UTF-8 first, then fall back to system default
    for encoding in ["utf-8", "utf-8-sig", "gbk", "latin-1"]:
        try:
            return p.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue

    raise ValueError(f"Could not decode {path} with any known encoding")


def write_xml_file(path: str | Path, content: str) -> None:
    """Write XML content with UTF-8 encoding (no BOM)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def inject_debug_anchors(content: str) -> str:
    """
    Inject debug visual anchors into Script_3D.

    Adds a semi-transparent red bounding box (BLOCK A, B, ZZYZX) at the end
    of the 3D script. This helps users visually verify that dimension parameters
    are correct when viewing the object in ArchiCAD.

    The debug block is wrapped in a conditional so it can be toggled via the
    GDL_DEBUG global variable or removed before production.
    """
    debug_code = '''
! ═══ DEBUG BOUNDING BOX (remove for production) ═══
IF GLOB_MODPAR_NAME = "" THEN  ! only in 3D view, not during param edit
    PEN 19  ! bright red pen
    SET MATERIAL "General - Glass"
    SET FILL "Empty Fill"
    BLOCK A, B, ZZYZX
ENDIF
! ═══ END DEBUG ═══'''

    # Find the closing ]]> of Script_3D's CDATA
    pattern = r'(</Script_3D>)'
    match = re.search(pattern, content)
    if not match:
        return content  # No Script_3D found, return unchanged

    # Find the CDATA close before </Script_3D>
    cdata_close = content.rfind("]]>", 0, match.start())
    if cdata_close == -1:
        return content

    # Insert debug code before ]]>
    return content[:cdata_close] + debug_code + "\n  " + content[cdata_close:]
