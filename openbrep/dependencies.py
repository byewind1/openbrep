"""
Macro Dependency Resolver — on-demand CALL dependency analysis.

When the current XML contains CALL "Macro_Name" statements, this module
lazily resolves the called macro's parameter definitions from the workspace,
so the LLM knows exactly what parameters to pass.

Design decision: We do NOT pre-scan the entire workspace at startup.
Instead, we resolve dependencies on-demand when CALL statements are
detected. This is faster for large libraries (500+ files) and avoids
wasting memory on macros that are never referenced.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MacroSignature:
    """Parameter signature of a GDL macro."""
    name: str
    parameters: list[dict] = field(default_factory=list)  # [{name, type, value, description}]
    source_path: str = ""

    def format_for_prompt(self) -> str:
        """Format as a concise reference for the LLM prompt."""
        if not self.parameters:
            return f'Macro "{self.name}": (no parameters found)'

        lines = [f'Macro "{self.name}" parameters:']
        for p in self.parameters:
            desc = f'  — {p.get("description", "")}' if p.get("description") else ""
            lines.append(
                f'  {p.get("name", "?")} : {p.get("type", "?")} = {p.get("value", "?")}{desc}'
            )
        return "\n".join(lines)


class DependencyResolver:
    """
    Resolves CALL dependencies from GDL XML source files.

    Usage:
        resolver = DependencyResolver(src_dir="./src")
        deps = resolver.resolve("src/window.xml")
        for sig in deps:
            print(sig.format_for_prompt())
    """

    def __init__(self, src_dir: str = "./src", templates_dir: str = "./templates"):
        self.search_dirs = [Path(src_dir), Path(templates_dir)]
        self._cache: dict[str, Optional[MacroSignature]] = {}

    def extract_call_names(self, xml_content: str) -> list[str]:
        """
        Extract all macro names referenced by CALL statements in the XML.

        Handles both:
          - CALL "Macro_Name"
          - CALL macro_variable
        Only returns string literals (quoted names), not variable references.
        """
        # Find CALL "literal_name" patterns across all script sections
        pattern = r'\bCALL\s+"([^"]+)"'
        return list(set(re.findall(pattern, xml_content, re.IGNORECASE)))

    def resolve(self, xml_path_or_content: str) -> list[MacroSignature]:
        """
        Resolve all CALL dependencies for a given XML file or content.

        Args:
            xml_path_or_content: Either a file path or raw XML content.

        Returns:
            List of MacroSignature objects for all referenced macros.
        """
        # Determine if it's a path or content
        if "<" in xml_path_or_content:
            content = xml_path_or_content
        else:
            path = Path(xml_path_or_content)
            if not path.exists():
                return []
            content = path.read_text(encoding="utf-8")

        call_names = self.extract_call_names(content)
        if not call_names:
            return []

        signatures = []
        for name in call_names:
            sig = self._resolve_macro(name)
            if sig:
                signatures.append(sig)

        return signatures

    def _resolve_macro(self, macro_name: str) -> Optional[MacroSignature]:
        """
        Find and parse a macro's XML source to extract its parameter signature.

        Search strategy:
        1. Check cache
        2. Search src/ and templates/ for matching XML files
        3. Parse parameters from the found file
        """
        if macro_name in self._cache:
            return self._cache[macro_name]

        # Search for the macro's XML file
        xml_path = self._find_macro_file(macro_name)
        if not xml_path:
            # Cache negative result to avoid repeated searches
            self._cache[macro_name] = None
            return None

        # Parse parameters
        sig = self._parse_parameters(macro_name, xml_path)
        self._cache[macro_name] = sig
        return sig

    def _find_macro_file(self, macro_name: str) -> Optional[Path]:
        """
        Search for a macro's XML source file.

        Naming conventions tried:
          - {macro_name}.xml
          - {macro_name}/  (directory with XML inside)
          - Case-insensitive match
        """
        for search_dir in self.search_dirs:
            if not search_dir.exists():
                continue

            # Direct file match
            for pattern in [f"{macro_name}.xml", f"{macro_name}/*.xml"]:
                matches = list(search_dir.glob(pattern))
                if matches:
                    return matches[0]

            # Case-insensitive search (GDL macro names can vary in case)
            name_lower = macro_name.lower()
            for xml_file in search_dir.rglob("*.xml"):
                if xml_file.stem.lower() == name_lower:
                    return xml_file

        return None

    def _parse_parameters(self, macro_name: str, xml_path: Path) -> MacroSignature:
        """Parse parameter definitions from a macro's XML file."""
        try:
            content = xml_path.read_text(encoding="utf-8")
            root = ET.fromstring(content)
        except (ET.ParseError, UnicodeDecodeError, OSError):
            return MacroSignature(name=macro_name, source_path=str(xml_path))

        params = []
        for param in root.findall(".//Parameter"):
            entry = {}
            for child in param:
                tag = child.tag.lower()
                if tag in ("n", "name"):
                    entry["name"] = child.text or ""
                elif tag == "type":
                    entry["type"] = child.text or ""
                elif tag == "value":
                    entry["value"] = child.text or ""
                elif tag == "description":
                    entry["description"] = child.text or ""
            if entry.get("name"):
                params.append(entry)

        return MacroSignature(
            name=macro_name,
            parameters=params,
            source_path=str(xml_path),
        )

    def format_all_for_prompt(self, signatures: list[MacroSignature]) -> str:
        """Format all resolved dependencies as a prompt section."""
        if not signatures:
            return ""

        parts = ["\n## Referenced Macro Signatures\n"]
        parts.append(
            "The current file CALLs the following macros. "
            "Use EXACTLY these parameter names when writing CALL statements:\n"
        )
        for sig in signatures:
            parts.append(f"```\n{sig.format_for_prompt()}\n```\n")

        return "\n".join(parts)

    def clear_cache(self) -> None:
        """Clear the macro resolution cache."""
        self._cache.clear()
