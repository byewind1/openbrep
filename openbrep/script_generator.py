"""
Script-level independent generation (Context Surgery).

Replaces the single "generate all scripts in one LLM call" with
per-script calls, each receiving only the minimal relevant context.

ScriptGenerator is standalone — depends only on a llm_caller callable
and an HSFProject. Does NOT depend on GDLAgent internals.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openbrep.hsf_project import HSFProject

logger = logging.getLogger(__name__)


class ScriptType(Enum):
    MASTER       = "scripts/1d.gdl"
    SCRIPT_3D    = "scripts/3d.gdl"
    SCRIPT_2D    = "scripts/2d.gdl"
    PARAM_SCRIPT = "scripts/vl.gdl"
    UI_SCRIPT    = "scripts/ui.gdl"
    PARAMS       = "paramlist.xml"


# ScriptType.value → HSFProject ScriptType.value (strips "scripts/" prefix)
_TO_HSF_VALUE: dict[str, str] = {
    "scripts/1d.gdl": "1d.gdl",
    "scripts/3d.gdl": "3d.gdl",
    "scripts/2d.gdl": "2d.gdl",
    "scripts/vl.gdl": "vl.gdl",
    "scripts/ui.gdl": "ui.gdl",
}

# Keyword sets for detect_affected_scripts
_KW_ALL    = frozenset({"创建", "create", "新建", "生成", "全部", "all"})
_KW_PARAMS = frozenset({"参数", "parameter", "param", "属性", "property"})
_KW_3D     = frozenset({"三维", "3d", "geometry", "几何", "模型", "model"})
_KW_2D     = frozenset({"二维", "2d", "plan", "平面", "俯视"})
_KW_MASTER = frozenset({"master", "1d", "计算", "calc", "赋值"})
_KW_VL     = frozenset({"vl", "values", "lock", "约束", "constraint"})
_KW_UI     = frozenset({"ui", "interface", "界面", "交互"})

# Parse [FILE: path]\ncontent from LLM responses
_FILE_BLOCK_RE = re.compile(
    r'\[FILE:\s*([^\]]+?)\]\s*\n(.*?)(?=\[FILE:|$)',
    re.DOTALL | re.IGNORECASE,
)
# Strip markdown fences
_FENCE_RE = re.compile(r'^```[a-z]*\n?|\n?```$', re.MULTILINE)

# Minimal set of GDL built-ins for cross-script warning (kept small on purpose)
_WARN_BUILTINS: frozenset[str] = frozenset({
    "IF", "THEN", "ELSE", "ENDIF", "FOR", "TO", "NEXT", "WHILE", "ENDWHILE",
    "REPEAT", "UNTIL", "RETURN", "END", "BLOCK", "ADD", "ADDX", "ADDY", "ADDZ",
    "DEL", "ROT", "MUL", "A", "B", "ZZYZX", "PI", "EPS", "SIN", "COS", "MAX",
    "MIN", "ABS", "INT", "SQR", "TRUE", "FALSE", "AND", "OR", "NOT",
    "LINE", "RECT", "ARC", "CIRCLE", "TEXT", "HOTSPOT2", "PROJECT2",
    "MATERIAL", "PEN", "RESOL", "TOLER", "FILL", "FILTER", "UNID",
})

_METADATA_WARN_BUILTINS: frozenset[str] = frozenset({
    "FILE", "scripts", "gdl", "paramlist", "xml", "Length", "Integer",
    "Boolean", "Material", "RealNum", "Angle", "String", "PenColor",
    "FillPattern", "LineType",
})


def _filter_cross_script_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("[FILE:") or stripped.startswith("!"):
            continue
        comment_idx = line.find("!")
        clean = line[:comment_idx] if comment_idx >= 0 else line
        if clean.strip():
            lines.append(clean)
    return "\n".join(lines)


@dataclass
class ScriptResult:
    script_type: ScriptType
    content: str
    success: bool
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


class ScriptGenerator:
    """
    Generates GDL scripts one file at a time (Context Surgery).

    llm_caller: callable(messages: list[dict]) -> str
    Safe when project is None — returns empty results without raising.
    """

    def __init__(self, llm_caller: Callable[[list[dict]], str]):
        self.llm_caller = llm_caller

    # ── public API ────────────────────────────────────────────────────────────

    def detect_affected_scripts(self, instruction: str) -> list[ScriptType]:
        """
        Determine which scripts need to be generated or updated.

        Rules (in priority order):
          - "创建"/"create"/... → all four main scripts
          - keyword match       → specific scripts
          - default             → PARAMS + MASTER + SCRIPT_3D + SCRIPT_2D
        """
        low = instruction.lower()

        if any(kw in low for kw in _KW_ALL):
            return [
                ScriptType.PARAMS,
                ScriptType.MASTER,
                ScriptType.SCRIPT_3D,
                ScriptType.SCRIPT_2D,
            ]

        affected: list[ScriptType] = []

        if any(kw in low for kw in _KW_PARAMS):
            affected.append(ScriptType.PARAMS)
            affected.append(ScriptType.MASTER)

        if any(kw in low for kw in _KW_3D):
            affected.append(ScriptType.SCRIPT_3D)

        if any(kw in low for kw in _KW_2D):
            affected.append(ScriptType.SCRIPT_2D)

        if any(kw in low for kw in _KW_MASTER) and ScriptType.MASTER not in affected:
            affected.append(ScriptType.MASTER)

        if any(kw in low for kw in _KW_VL):
            affected.append(ScriptType.PARAM_SCRIPT)

        if any(kw in low for kw in _KW_UI):
            affected.append(ScriptType.UI_SCRIPT)

        if not affected:
            # Default: generate the four core scripts
            return [
                ScriptType.PARAMS,
                ScriptType.MASTER,
                ScriptType.SCRIPT_3D,
                ScriptType.SCRIPT_2D,
            ]

        # Deduplicate, preserve order
        seen: set[ScriptType] = set()
        result: list[ScriptType] = []
        for st in affected:
            if st not in seen:
                seen.add(st)
                result.append(st)
        return result

    def generate_script(
        self,
        script_type: ScriptType,
        instruction: str,
        project_context: dict,   # {file_path: content_str}
        knowledge: str = "",
        skills: str = "",
    ) -> ScriptResult:
        """Generate a single GDL script via one focused LLM call."""
        logger.debug(f"ScriptGenerator: generating {script_type.value}")
        if self.llm_caller is None:
            return ScriptResult(
                script_type=script_type,
                content="",
                success=False,
                error="No LLM caller configured",
            )

        try:
            messages = self._build_messages(
                script_type, instruction, project_context, knowledge, skills
            )
            response = self.llm_caller(messages)
            content = self._extract_content(response, script_type)
            return ScriptResult(
                script_type=script_type,
                # Fallback: use whole response if no [FILE:] block found
                content=content if content is not None else response.strip(),
                success=True,
            )

        except Exception as exc:
            logger.warning(f"generate_script failed for {script_type}: {exc}")
            return ScriptResult(
                script_type=script_type,
                content="",
                success=False,
                error=str(exc),
            )

    def merge_results(
        self,
        results: list[ScriptResult],
        project: Optional["HSFProject"],
    ) -> dict[str, str]:
        """
        Apply generated scripts to project.

        Returns {file_path: content} for all successfully generated scripts.
        Cross-script consistency issues are logged as warnings — not blocking.
        """
        logger.debug(f"ScriptGenerator: merging {len(results)} scripts")
        merged: dict[str, str] = {}

        if project is None:
            return merged

        from openbrep.hsf_project import ScriptType as HSFScriptType

        for result in results:
            if not result.success or not result.content:
                continue

            file_path = result.script_type.value
            merged[file_path] = result.content

            if result.script_type == ScriptType.PARAMS:
                # Params parsed and applied by the caller (GDLAgent)
                continue

            hsf_val = _TO_HSF_VALUE.get(file_path)
            if hsf_val is None:
                continue

            for hsf_st in HSFScriptType:
                if hsf_st.value == hsf_val:
                    project.set_script(hsf_st, result.content)
                    break

        self._warn_cross_script(merged, project)
        return merged

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_messages(
        script_type: ScriptType,
        instruction: str,
        project_context: dict,
        knowledge: str,
        skills: str,
    ) -> list[dict]:
        target = script_type.value
        system = (
            "You are an expert ArchiCAD GDL developer.\n"
            "Generate or update ONE specific script file.\n"
            "Output ONLY that file using [FILE: path] format. "
            "No markdown fences, no prose inside the file block.\n"
        )
        if knowledge:
            system += f"\n## Reference\n{knowledge}\n"
        if skills:
            system += f"\n## Strategy\n{skills}\n"

        ctx_lines: list[str] = []
        for fpath, content in project_context.items():
            body = content.strip() if content and content.strip() else "(empty)"
            ctx_lines.append(f"[{fpath}]\n{body}")
        context_text = "\n\n".join(ctx_lines)

        user = (
            f"## Project context\n{context_text}\n\n"
            f"## Instruction\n{instruction}\n\n"
            f"Generate [{target}] using [FILE: {target}] format."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]

    @staticmethod
    def _extract_content(response: str, script_type: ScriptType) -> Optional[str]:
        """Pull the [FILE: target] block from response. Returns None if absent."""
        target_filename = script_type.value.split("/")[-1]  # e.g. "3d.gdl"
        for m in _FILE_BLOCK_RE.finditer(response):
            path = m.group(1).strip()
            if (path.lower() == script_type.value.lower()
                    or path.lower().endswith(target_filename.lower())):
                content = _FENCE_RE.sub("", m.group(2)).strip()
                return content
        return None

    @staticmethod
    def _warn_cross_script(merged: dict[str, str], project: "HSFProject") -> None:
        """Log warnings for identifiers used in 3d/2d but not declared anywhere."""
        param_names: set[str] = {p.name for p in project.parameters}

        # Collect names assigned in 1d.gdl (merged or existing)
        master_text = merged.get("scripts/1d.gdl", "")
        if not master_text:
            from openbrep.hsf_project import ScriptType as HSFScriptType
            for hsf_st in HSFScriptType:
                if hsf_st.value == "1d.gdl":
                    master_text = project.get_script(hsf_st) or ""
                    break
        master_text = _filter_cross_script_text(master_text)
        assigned_in_master: set[str] = {
            m.group(1)
            for m in re.finditer(r'^\s*([A-Za-z_]\w*)\s*=(?!=)', master_text, re.MULTILINE)
        }
        known = param_names | assigned_in_master

        ident_re = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')

        for gdl_file in ("scripts/3d.gdl", "scripts/2d.gdl"):
            content = _filter_cross_script_text(merged.get(gdl_file, ""))
            if not content:
                continue
            warned: set[str] = set()
            for m in ident_re.finditer(content):
                name = m.group(1)
                if name in warned or name.upper() in _WARN_BUILTINS or name in _METADATA_WARN_BUILTINS:
                    continue
                if name.startswith("_") or name.startswith("gs_") or name.startswith("ac_"):
                    continue
                if len(name) == 1:
                    continue
                if name not in known:
                    warned.add(name)
                    logger.warning(
                        f"[cross-script] '{name}' used in {gdl_file} "
                        "but not declared in paramlist or 1d.gdl"
                    )
