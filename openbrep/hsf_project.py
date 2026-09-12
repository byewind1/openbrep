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
from typing import Any, Optional


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

# Graphisoft 注册常量（GSM 二进制头魔数 "MYSGCASG" 的来源）：
# Signature="MYSG"(LE)=1196644685，Owner="CASG"(LE)=1196638531。
# 为 0 时 LP_XMLConverter 产出的 GSM 缺该魔数，Archicad 拒绝打开
# （"Unsupported document version or incorrect file"，漏窗真机实测）。
DEFAULT_LIBPART_OWNER = "1196638531"
DEFAULT_LIBPART_SIGNATURE = "1196644685"


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
        # Graphisoft 注册常量（见模块顶部 DEFAULT_LIBPART_* 注释）。
        self.owner: str = DEFAULT_LIBPART_OWNER
        self.signature: str = DEFAULT_LIBPART_SIGNATURE
        self.subtype_guid: str = "F938E33A-329D-4A36-BE3E-85E126820996"  # General GDL Object
        # 完整 ancestry GUID 链（None = 未加载，写出时用默认两GUID模板）。
        # load_from_disk 后为首项 == subtype_guid 的完整列表，save 原样保留。
        self.subtype_guids: list[str] | None = None
        # Identification 字段（宏对象 IsPlaceable=false，加载后必须保留）。
        self.is_placeable: bool = True
        self.is_archivable: bool = False
        self.migration_value: str = "Normal"
        self.is_template: bool = False
        # 宏依赖表（name, MainGUID），来自 calledmacros.xml；Archicad 以
        # 名称—GUID 对绑定宏，缺失会导致重编译 GSM 丢失宏绑定（P0 数据丢失修复）。
        self.called_macros: list[tuple[str, str]] = []
        # libpartdocs.xml 原文透传（OpenBrep 不建模版权/关键字；载入后原样写回，
        # 避免把第三方图库的 CC0 等协议覆盖成默认模板）。
        self._libpartdocs_raw: str | None = None
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

        # Load calledmacros.xml（名称—GUID 宏依赖表，P0：不再丢弃）
        calledmacros_path = root / "calledmacros.xml"
        if calledmacros_path.exists():
            proj._parse_calledmacros(
                calledmacros_path.read_text(encoding="utf-8-sig")
            )

        # libpartdocs.xml 原文透传（不建模，save 时原样写回）
        libpartdocs_path = root / "libpartdocs.xml"
        if libpartdocs_path.exists():
            proj._libpartdocs_raw = libpartdocs_path.read_text(encoding="utf-8-sig")

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
\t\t<IsPlaceable>{str(self.is_placeable).lower()}</IsPlaceable>
\t\t<IsArchivable>{str(self.is_archivable).lower()}</IsArchivable>
\t\t<MigrationValue>{self.migration_value}</MigrationValue>
\t\t<IsTemplate>{str(self.is_template).lower()}</IsTemplate>
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
        加载过的项目保留完整 GUID 链（subtype_guids）；新建项目用默认模板
        （General GDL Object + 固定第二 GUID）。
        """
        guids = self.subtype_guids
        if not guids:
            guids = [self.subtype_guid, "103E8D2C-8230-42E1-9597-46F84CCE28C0"]
        entries = "".join(f"\t<MainGUID>{guid}</MainGUID>\n" for guid in guids)
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<Ancestry>
{entries}</Ancestry>
'''

    def _parse_libpartdata(self, content: str) -> None:
        """Parse libpartdata.xml to extract GUID, version and Identification flags."""
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

            def _flag(tag: str) -> bool | None:
                el = root.find(f"./Identification/{tag}")
                if el is None or el.text is None:
                    return None
                return el.text.strip().lower() in {"true", "1", "yes"}

            placeable = _flag("IsPlaceable")
            if placeable is not None:
                self.is_placeable = placeable
            archivable = _flag("IsArchivable")
            if archivable is not None:
                self.is_archivable = archivable
            template = _flag("IsTemplate")
            if template is not None:
                self.is_template = template
            migration_el = root.find("./Identification/MigrationValue")
            if migration_el is not None and migration_el.text:
                self.migration_value = migration_el.text.strip()
        except ET.ParseError:
            pass  # Keep defaults

    def _parse_ancestry(self, content: str) -> None:
        """Parse ancestry.xml: 保留完整 MainGUID 链（首项 = subtype_guid）。"""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            guids = [
                el.text.strip()
                for el in root.findall("MainGUID")
                if el.text and el.text.strip()
            ]
            if guids:
                self.subtype_guids = guids
                self.subtype_guid = guids[0]
        except ET.ParseError:
            pass

    @staticmethod
    def _unquote_macro_name(raw: str) -> str:
        """calledmacros.xml 的 MName 常带一层引号（CDATA["名称"]），剥掉它。"""
        text = (raw or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return text[1:-1]
        return text

    def _parse_calledmacros(self, content: str) -> None:
        """Parse calledmacros.xml 的 <Macro> 名称—GUID 映射（保持文件顺序）。"""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return
        macros: list[tuple[str, str]] = []
        for macro_el in root.findall("Macro"):
            name_el = macro_el.find("MName")
            guid_el = macro_el.find("MainGUID")
            if name_el is None or guid_el is None or not guid_el.text:
                continue
            name = self._unquote_macro_name(name_el.text or "")
            guid = guid_el.text.strip()
            if name and guid:
                macros.append((name, guid))
        self.called_macros = macros

    def called_macro_guid_map(self) -> dict[str, str]:
        """宏名 → MainGUID 映射（后出现的同名条目覆盖先出现的，与 Archicad 名称查找一致）。"""
        return {name: guid for name, guid in self.called_macros}

    def _build_calledmacros(self) -> str:
        """Generate calledmacros.xml content from self.called_macros.

        格式对齐 LP_XMLConverter 输出：tab 缩进 + CDATA 包裹的带引号名称。
        新建对象（无宏）生成空 <CalledMacros>（行为不变）。
        """
        entries = "".join(
            f'\t<Macro>\n\t\t<MName><![CDATA["{name}"]]></MName>\n'
            f"\t\t<MainGUID>{guid}</MainGUID>\n\t</Macro>\n"
            for name, guid in self.called_macros
        )
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<CalledMacros>
{entries}</CalledMacros>
'''

    def _build_libpartdocs(self) -> str:
        """Generate libpartdocs.xml content.

        加载过的项目原文透传（OpenBrep 不建模版权/关键字内容）；新建项目用
        默认模板。
        """
        if self._libpartdocs_raw is not None:
            return self._libpartdocs_raw
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


# ── 导入后规范化（P3-e） ────────────────────────────────────
# 只在 GSM 导入落盘后调用一次（见 project_session_service.import_gsm_file）。

_IMPORT_NORMALIZE_XML_FILES = (
    "libpartdata.xml",
    "paramlist.xml",
    "ancestry.xml",
    "calledmacros.xml",
    "libpartdocs.xml",
)


def _snapshot_hsf_files(root: Path) -> dict[str, bytes]:
    """快照 5 个 XML + scripts/** 的原始字节（只含当前存在的文件，相对路径为键）。"""
    snapshot: dict[str, bytes] = {}
    for name in _IMPORT_NORMALIZE_XML_FILES:
        path = root / name
        if path.is_file():
            snapshot[name] = path.read_bytes()
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*")):
            if path.is_file():
                snapshot[str(path.relative_to(root))] = path.read_bytes()
    return snapshot


def _restore_hsf_snapshot(root: Path, before: dict[str, bytes], after: dict[str, bytes]) -> None:
    """回滚：恢复 before 中的字节，并删除规范化新建的文件（after 有而 before 无）。"""
    for rel, data in before.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for rel in after:
        if rel not in before:
            path = root / rel
            if path.exists():
                path.unlink()


def _compare_loaded_hsf_projects(
    first: HSFProject,
    after: HSFProject,
    present_before: set[str] | None = None,
) -> list[str]:
    """无损校验比对清单：guid / version / identification 标志 / 完整 ancestry /
    calledmacros 名称—GUID 表 / libpartdocs 原文 / 参数（数量+name/type/value）/ 脚本全文。

    present_before：规范化前磁盘上实际存在的文件（_snapshot_hsf_files 的键）。
    某 section 文件原本不存在时，save 按默认值补建不属于"丢失"——ancestry 链、
    calledmacros、libpartdocs 三项只在该文件原本存在时才比对。None = 全量比对。
    """
    def _had(rel: str) -> bool:
        return present_before is None or rel in present_before

    problems: list[str] = []
    if first.guid != after.guid:
        problems.append(f"libpartdata MainGUID 变化: {first.guid} → {after.guid}")
    if first.version != after.version:
        problems.append(f"libpartdata Version 变化: {first.version} → {after.version}")
    if first.subtype_guid != after.subtype_guid:
        problems.append(f"ancestry MainGUID 变化: {first.subtype_guid} → {after.subtype_guid}")
    if _had("ancestry.xml") and (first.subtype_guids or []) != (after.subtype_guids or []):
        problems.append(
            f"ancestry GUID 链变化: {first.subtype_guids} → {after.subtype_guids}"
        )
    for flag in ("is_placeable", "is_archivable", "is_template", "migration_value"):
        if getattr(first, flag) != getattr(after, flag):
            problems.append(
                f"libpartdata {flag} 变化: {getattr(first, flag)!r} → {getattr(after, flag)!r}"
            )
    if _had("calledmacros.xml") and first.called_macros != after.called_macros:
        problems.append(
            f"calledmacros 宏映射变化: {len(first.called_macros)} 条 → {len(after.called_macros)} 条"
        )
    if _had("libpartdocs.xml") and (first._libpartdocs_raw or "") != (after._libpartdocs_raw or ""):
        problems.append("libpartdocs.xml 内容变化")
    if len(first.parameters) != len(after.parameters):
        problems.append(f"参数数量变化: {len(first.parameters)} → {len(after.parameters)}")
    else:
        for index, (p1, p2) in enumerate(zip(first.parameters, after.parameters)):
            if (p1.name, p1.type_tag, p1.value) != (p2.name, p2.type_tag, p2.value):
                problems.append(
                    f"第 {index + 1} 个参数不一致: {p1.name!r} → {p2.name!r} "
                    f"(type {p1.type_tag}→{p2.type_tag}, value {p1.value!r}→{p2.value!r})"
                )
                break
    for st in sorted(set(first.scripts) | set(after.scripts), key=lambda st: st.value):
        if first.scripts.get(st) != after.scripts.get(st):
            problems.append(f"脚本 {st.value} 内容变化")
    return problems


def normalize_project_after_import(project_root: str | Path) -> dict[str, Any]:
    """GSM 导入落盘后做一次规范化重写（load→save 无损守卫 + 回滚）。

    纪律：规范化只在导入时做一次；之后的保存不该再产生格式 diff，若产生说明
    写器/解析器不对称，是 bug（先修写器/解析器，而不是再叠加一次规范化）。

    流程：
      a. 快照原始字节：5 个 XML（libpartdata/paramlist/ancestry/calledmacros/
         libpartdocs，存在的才算）+ scripts/*；
      b. HSFProject.load_from_disk → save_to_disk（规范重写，utf-8-sig）；
      c. 无损校验：重新 load_from_disk，与第一次加载的对象逐项比对——guid、
         version、subtype_guid、完整 ancestry GUID 链、Identification 标志、
         calledmacros 名称—GUID 表、libpartdocs 原文、参数列表（数量 + 每个
         参数的 name/type/value）、各脚本全文（utf-8-sig 读入后文本相等）；
      d. 有损 → 用 a 的字节快照回滚全部文件，返回 {ok:False, lossless:False,
         warning}；无损 → {ok:True, lossless:True, changed_files:[字节级变化
         的相对路径]}；
      e. 任何异常 → 同样回滚 + warning，绝不把导入搞失败。

    Returns:
        {"ok": bool, "lossless": bool, "changed_files": list[str] | None,
         "warning": str | None}
    """
    root = Path(project_root).expanduser().resolve()
    before = _snapshot_hsf_files(root)

    try:
        first = HSFProject.load_from_disk(str(root))
        first.save_to_disk()
        after = HSFProject.load_from_disk(str(root))
    except Exception as exc:
        _restore_hsf_snapshot(root, before, _snapshot_hsf_files(root))
        return {
            "ok": False,
            "lossless": False,
            "changed_files": None,
            "warning": f"GSM 导入规范化失败，已回滚原始文件: {exc}",
        }

    problems = _compare_loaded_hsf_projects(first, after, present_before=set(before))
    if problems:
        _restore_hsf_snapshot(root, before, _snapshot_hsf_files(root))
        return {
            "ok": False,
            "lossless": False,
            "changed_files": None,
            "warning": (
                "GSM 导入规范化检测到内容有损，已回滚原始文件（保留原始文件未规范化）: "
                + "; ".join(problems)
            ),
        }

    now = _snapshot_hsf_files(root)
    changed = sorted(
        rel for rel in set(before) | set(now) if before.get(rel) != now.get(rel)
    )
    return {"ok": True, "lossless": True, "changed_files": changed, "warning": None}
