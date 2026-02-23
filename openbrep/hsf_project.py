"""
HSF Project Manager — Core data model for HSF (Hierarchical Source Format).

HSF is ArchiCAD's text-based library part format where each object is a
directory containing separate XML metadata files and .gdl script files.
This module provides in-memory representation and disk I/O for HSF projects.

Key insight from the HSF spec: file system IS the data structure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Script Types ──────────────────────────────────────────

class ScriptType(Enum):
    """GDL script types mapped to HSF filenames."""
    MASTER     = "1d.gdl"   # Master Script: globals, subroutines
    SCRIPT_2D  = "2d.gdl"   # 2D Script: plan view symbol
    SCRIPT_3D  = "3d.gdl"   # 3D Script: 3D model geometry
    PARAM      = "vl.gdl"   # Parameter Script: VALUES, LOCK
    UI         = "ui.gdl"   # Interface Script: UI panels
    PROPERTIES = "pr.gdl"   # Properties Script: IFC/scheduling
    FWD_MIGR   = "fwm.gdl"  # Forward migration
    BWD_MIGR   = "bwm.gdl"  # Backward migration


# ── Parameter Types ───────────────────────────────────────

# Valid paramlist.xml type tags (Graphisoft XML Schema)
VALID_PARAM_TYPES = {
    "Length", "Angle", "RealNum", "Integer", "Boolean",
    "String", "PenColor", "FillPattern", "LineType", "Material",
    "Title", "Separator",
}

# Common LLM mistakes → correct type mapping
PARAM_TYPE_CORRECTIONS = {
    "Float":   "RealNum",
    "Real":    "RealNum",
    "Double":  "RealNum",
    "Number":  "RealNum",
    "Int":     "Integer",
    "Bool":    "Boolean",
    "Text":    "String",
    "Str":     "String",
    "Pen":     "PenColor",
    "Fill":    "FillPattern",
    "Line":    "LineType",
    "Mat":     "Material",
}


@dataclass
class GDLParameter:
    """A single GDL parameter definition."""
    name: str
    type_tag: str          # Must be in VALID_PARAM_TYPES
    description: str = ""
    value: str = ""
    is_fixed: bool = False
    flags: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Auto-correct common LLM type mistakes."""
        if self.type_tag not in VALID_PARAM_TYPES:
            corrected = PARAM_TYPE_CORRECTIONS.get(self.type_tag)
            if corrected:
                self.type_tag = corrected
            else:
                raise ValueError(
                    f"Invalid parameter type '{self.type_tag}' for '{self.name}'. "
                    f"Valid types: {', '.join(sorted(VALID_PARAM_TYPES))}"
                )


# ── HSF Project ───────────────────────────────────────────

class HSFProject:
    """
    In-memory representation of an HSF library part.

    An HSF project maps to a directory on disk:
        MyObject/
        ├── libpartdata.xml
        ├── paramlist.xml
        ├── ancestry.xml
        └── scripts/
            ├── 1d.gdl
            ├── 2d.gdl
            ├── 3d.gdl
            ├── vl.gdl
            └── ui.gdl
    """

    def __init__(self, name: str, work_dir: str = "."):
        self.name = name
        self.work_dir = Path(work_dir)
        self.root = self.work_dir / name

        # Metadata
        self.guid: str = self._generate_guid()
        self.version: int = 46           # AC27 default
        self.owner: str = "0"
        self.signature: str = "0"
        self.subtype_guid: str = "F938E33A-329D-4A36-BE3E-85E126820996"  # General GDL Object
        self.description: str = ""

        # Parameters (ordered list — order matters in ArchiCAD)
        self.parameters: list[GDLParameter] = []

        # Scripts (only populated scripts are written to disk)
        self.scripts: dict[ScriptType, str] = {}

    # ── Factory Methods ───────────────────────────────────

    @classmethod
    def create_new(cls, name: str, work_dir: str = ".",
                   ac_version: int = 46) -> HSFProject:
        """Create a new HSF project with standard defaults."""
        proj = cls(name, work_dir)
        proj.version = ac_version

        # ArchiCAD reserved parameters (every object has these)
        proj.parameters = [
            GDLParameter("A",     "Length",  "Width",  "1.00", is_fixed=True),
            GDLParameter("B",     "Length",  "Depth",  "1.00", is_fixed=True),
            GDLParameter("ZZYZX", "Length",  "Height", "1.00", is_fixed=True),
        ]

        # Default 3D script: simple block
        proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"

        return proj

    @classmethod
    def load_from_disk(cls, hsf_dir: str) -> HSFProject:
        """Load an existing HSF directory into memory."""
        from openbrep.paramlist_builder import parse_paramlist_xml

        root = Path(hsf_dir)
        if not root.is_dir():
            raise FileNotFoundError(f"HSF directory not found: {hsf_dir}")

        name = root.name
        proj = cls(name, str(root.parent))

        # Load libpartdata.xml
        libpart_path = root / "libpartdata.xml"
        if libpart_path.exists():
            proj._parse_libpartdata(libpart_path.read_text(encoding="utf-8-sig"))

        # Load paramlist.xml
        paramlist_path = root / "paramlist.xml"
        if paramlist_path.exists():
            proj.parameters = parse_paramlist_xml(
                paramlist_path.read_text(encoding="utf-8-sig")
            )

        # Load scripts
        scripts_dir = root / "scripts"
        if scripts_dir.is_dir():
            for st in ScriptType:
                script_path = scripts_dir / st.value
                if script_path.exists():
                    proj.scripts[st] = script_path.read_text(encoding="utf-8-sig")

        # Load ancestry
        ancestry_path = root / "ancestry.xml"
        if ancestry_path.exists():
            proj._parse_ancestry(ancestry_path.read_text(encoding="utf-8-sig"))

        return proj

    # ── Disk I/O ──────────────────────────────────────────

    def save_to_disk(self) -> Path:
        """
        Write HSF project to disk.

        CRITICAL: All text files use UTF-8 with BOM (utf-8-sig).
        This is a hard requirement from LP_XMLConverter.
        """
        from openbrep.paramlist_builder import build_paramlist_xml

        # Create directory structure
        self.root.mkdir(parents=True, exist_ok=True)
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        # Write libpartdata.xml
        self._write_file(
            self.root / "libpartdata.xml",
            self._build_libpartdata()
        )

        # Write paramlist.xml
        self._write_file(
            self.root / "paramlist.xml",
            build_paramlist_xml(self.parameters)
        )

        # Write ancestry.xml
        self._write_file(
            self.root / "ancestry.xml",
            self._build_ancestry()
        )

        # Write calledmacros.xml
        self._write_file(
            self.root / "calledmacros.xml",
            self._build_calledmacros()
        )

        # Write libpartdocs.xml
        self._write_file(
            self.root / "libpartdocs.xml",
            self._build_libpartdocs()
        )

        # Write scripts
        for script_type, content in self.scripts.items():
            self._write_file(scripts_dir / script_type.value, content)

        return self.root

    # ── Parameter Operations ──────────────────────────────

    def add_parameter(self, param: GDLParameter) -> None:
        """Add a parameter, checking for duplicates."""
        existing_names = {p.name for p in self.parameters}
        if param.name in existing_names:
            raise ValueError(f"Parameter '{param.name}' already exists")
        self.parameters.append(param)

    def get_parameter(self, name: str) -> Optional[GDLParameter]:
        """Get parameter by name."""
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def remove_parameter(self, name: str) -> bool:
        """Remove parameter by name. Returns True if found."""
        for i, p in enumerate(self.parameters):
            if p.name == name:
                self.parameters.pop(i)
                return True
        return False

    # ── Script Operations ─────────────────────────────────

    def get_script(self, script_type: ScriptType) -> str:
        """Get script content, empty string if not set."""
        return self.scripts.get(script_type, "")

    def set_script(self, script_type: ScriptType, content: str) -> None:
        """Set script content."""
        self.scripts[script_type] = content

    def get_affected_scripts(self, instruction: str) -> list[ScriptType]:
        """
        Determine which scripts are affected by an instruction.
        Used for context surgery — only load relevant scripts into LLM context.
        """
        instruction_lower = instruction.lower()

        keywords_3d = ["3d", "geometry", "block", "prism", "sphere", "revolve",
                       "material", "几何", "三维", "材质", "模型"]
        keywords_2d = ["2d", "plan", "symbol", "line2", "poly2",
                       "平面", "符号", "二维"]
        keywords_ui = ["ui", "panel", "dialog", "interface",
                       "界面", "面板", "对话框"]
        keywords_pr = ["parameter", "values", "lock", "hide",
                       "参数", "约束", "锁定", "隐藏"]
        keywords_prop = ["property", "ifc", "schedule", "component",
                         "属性", "算量", "组件"]

        affected = []

        if any(kw in instruction_lower for kw in keywords_3d):
            affected.append(ScriptType.SCRIPT_3D)
        if any(kw in instruction_lower for kw in keywords_2d):
            affected.append(ScriptType.SCRIPT_2D)
        if any(kw in instruction_lower for kw in keywords_ui):
            affected.append(ScriptType.UI)
        if any(kw in instruction_lower for kw in keywords_pr):
            affected.append(ScriptType.PARAM)
        if any(kw in instruction_lower for kw in keywords_prop):
            affected.append(ScriptType.PROPERTIES)

        # Default: 3D + PARAM if nothing specific detected
        if not affected:
            affected = [ScriptType.SCRIPT_3D, ScriptType.PARAM]

        # Master script always included (global variables)
        if ScriptType.MASTER not in affected:
            affected.insert(0, ScriptType.MASTER)

        return affected

    # ── Internal Helpers ──────────────────────────────────

    @staticmethod
    def _generate_guid() -> str:
        """Generate UUID v4, uppercase, for ArchiCAD MainGUID."""
        return str(uuid.uuid4()).upper()

    def _write_file(self, path: Path, content: str) -> None:
        """Write file with UTF-8 BOM encoding (LP_XMLConverter requirement)."""
        path.write_text(content, encoding="utf-8-sig")

    def _build_libpartdata(self) -> str:
        """Generate libpartdata.xml content.
        
        Format reverse-engineered from real LP_XMLConverter libpart2hsf output.
        Root tag is <LibpartData> with Owner/Signature/Version as attributes.
        """
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<LibpartData Owner="{self.owner}" Signature="{self.signature}" Version="{self.version}">
\t<Identification>
\t\t<MainGUID>{self.guid}</MainGUID>
\t\t<IsPlaceable>true</IsPlaceable>
\t\t<IsArchivable>false</IsArchivable>
\t\t<MigrationValue>Normal</MigrationValue>
\t\t<IsTemplate>false</IsTemplate>
\t</Identification>
\t<Ancestry SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<CalledMacros SectVersion="2" SectionFlags="0" SubIdent="0"/>
\t<Script_3D SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<Script_2D SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<Script_1D SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<Script_UI SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<Script_VL SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<ParamSection SectVersion="27" SectionFlags="0" SubIdent="0"/>
\t<Copyright SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<Keywords SectVersion="1" SectionFlags="0" SubIdent="0"/>
</LibpartData>
'''

    def _build_ancestry(self) -> str:
        """Generate ancestry.xml content.
        
        Real format: <Ancestry> with one or more <MainGUID> entries.
        First GUID = base subtype, subsequent = more specific subtypes.
        Default: General GDL Object GUID.
        """
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<Ancestry>
\t<MainGUID>{self.subtype_guid}</MainGUID>
\t<MainGUID>103E8D2C-8230-42E1-9597-46F84CCE28C0</MainGUID>
</Ancestry>
'''

    def _parse_libpartdata(self, content: str) -> None:
        """Parse libpartdata.xml to extract GUID and version."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            # Real format: attributes on root tag
            ver = root.get("Version")
            if ver:
                self.version = int(ver)
            owner = root.get("Owner")
            if owner:
                self.owner = owner
            sig = root.get("Signature")
            if sig:
                self.signature = sig
            guid_el = root.find(".//MainGUID")
            if guid_el is not None and guid_el.text:
                self.guid = guid_el.text.strip()
        except ET.ParseError:
            pass  # Keep defaults

    def _parse_ancestry(self, content: str) -> None:
        """Parse ancestry.xml to extract subtype GUID."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            # First MainGUID is the base subtype
            guid_el = root.find("MainGUID")
            if guid_el is not None and guid_el.text:
                self.subtype_guid = guid_el.text.strip()
        except ET.ParseError:
            pass

    def _build_calledmacros(self) -> str:
        """Generate calledmacros.xml content."""
        return '''<?xml version="1.0" encoding="UTF-8"?>
<CalledMacros>
</CalledMacros>
'''

    def _build_libpartdocs(self) -> str:
        """Generate libpartdocs.xml content."""
        return '''<?xml version="1.0" encoding="UTF-8"?>
<libpartdocs>
\t<Copyright>
\t\t<Author></Author>
\t\t<License>
\t\t\t<Type>CC BY</Type>
\t\t\t<Version>4.0</Version>
\t\t</License>
\t</Copyright>
\t<Keywords SectVersion="1" SectionFlags="0" SubIdent="0">
\t\t<![CDATA[]]>
\t</Keywords>
</libpartdocs>
'''

    # ── Representation ────────────────────────────────────

    def __repr__(self) -> str:
        scripts = [st.value for st in self.scripts]
        return (
            f"HSFProject('{self.name}', "
            f"params={len(self.parameters)}, "
            f"scripts={scripts})"
        )

    def summary(self) -> str:
        """Human-readable project summary."""
        lines = [
            f"📦 {self.name}",
            f"   GUID: {self.guid}",
            f"   AC Version: {self.version}",
            f"   Parameters: {len(self.parameters)}",
        ]
        for p in self.parameters:
            fixed = " [FIXED]" if p.is_fixed else ""
            lines.append(f"     {p.type_tag:10s} {p.name:20s} = {p.value}{fixed}")
        lines.append(f"   Scripts:")
        for st, content in self.scripts.items():
            line_count = content.count("\n") + 1
            lines.append(f"     {st.value:10s} ({line_count} lines)")
        return "\n".join(lines)
