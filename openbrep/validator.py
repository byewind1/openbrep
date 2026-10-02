"""Hard-rule validator for generated GDL scripts."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Optional

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.paramlist_builder import validate_paramlist


@dataclass
class ValidationIssue:
    level: Literal["error", "warning", "info"]
    category: Literal["param", "structure", "heuristic", "cross_script"]
    file: str
    message: str
    line: Optional[int] = None
    auto_fix_safe: bool = False


class GDLValidator:
    """Validate generated GDL content with strict hard rules."""

    _PARAM_LINE_RE = re.compile(
        r'^(Length|Angle|RealNum|Integer|Boolean|String|Material|'
        r'FillPattern|LineType|PenColor)\s+'
        r'(\w+)\s*=\s*("[^"]*"|\S+)'
        r'(?:\s+!\s*(.+))?$',
        re.IGNORECASE,
    )

    def validate_params(self, paramlist_text: str) -> list[str]:
        """Validate line-based paramlist text using existing validate_paramlist()."""
        params = self._parse_paramlist_text(paramlist_text or "")
        if not params:
            return ["paramlist为空或无法解析"]
        return validate_paramlist(params)

    def validate_2d(self, script_text: str) -> list[str]:
        return []

    def validate_3d(self, script_text: str) -> list[str]:
        issues: list[str] = []
        text = script_text or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        if not lines or lines[-1].upper() != "END":
            issues.append("末尾缺少END")

        command_counts = self._count_commands(text)

        for_count = command_counts["FOR"]
        next_count = command_counts["NEXT"]
        if for_count != next_count:
            issues.append(f"⚠️ 建议检查：FOR/NEXT不配对，FOR={for_count} NEXT={next_count}")

        if_count = command_counts["IF_BLOCK"]
        endif_count = command_counts["ENDIF"]
        if if_count != endif_count:
            issues.append(f"⚠️ 建议检查：IF/ENDIF不配对，IF={if_count} ENDIF={endif_count}")

        return issues

    def validate_all_issues(self, project: HSFProject) -> list[ValidationIssue]:
        """Validate all supported parts of an HSFProject with structured issues."""
        issues: list[ValidationIssue] = []

        param_text = "\n".join(
            f"{p.type_tag} {p.name} = {p.value}"
            + (f" ! {p.description}" if p.description else "")
            for p in (project.parameters or [])
        )
        for issue in self.validate_params(param_text):
            is_error = (
                issue == "paramlist为空或无法解析"
                or issue.startswith("Duplicate parameter name:")
                or issue.startswith("Invalid type ")
            )
            issues.append(ValidationIssue(
                level="error" if is_error else "warning",
                category="param",
                file="paramlist.xml",
                message=issue,
            ))

        script_3d = project.get_script(ScriptType.SCRIPT_3D)
        for issue in self.validate_3d(script_3d):
            issues.append(ValidationIssue(
                level="error" if issue == "末尾缺少END" else "warning",
                category="structure" if issue == "末尾缺少END" else "heuristic",
                file="3d.gdl",
                message=issue,
            ))

        from openbrep.cross_script_checker import CrossScriptChecker
        issues += CrossScriptChecker().check(project)

        return issues

    def validate_all(self, project: HSFProject) -> list[str]:
        """Compatibility wrapper returning string messages."""
        return [
            f"{i.file}: {'❌' if i.level == 'error' else '⚠️'} {i.message}"
            for i in self.validate_all_issues(project)
        ]

    def _parse_paramlist_text(self, text: str) -> list[GDLParameter]:
        params: list[GDLParameter] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("!"):
                continue
            match = self._PARAM_LINE_RE.match(line)
            if not match:
                continue

            type_tag = match.group(1)
            name = match.group(2)
            value = match.group(3).strip('"')
            desc = (match.group(4) or "").strip()
            is_fixed = name in ("A", "B", "ZZYZX")

            try:
                params.append(GDLParameter(
                    name=name,
                    type_tag=type_tag,
                    description=desc,
                    value=value,
                    is_fixed=is_fixed,
                ))
            except Exception:
                # Invalid type/name should be surfaced by validate_paramlist stage when possible.
                continue
        return params

    @staticmethod
    def _count_commands(text: str) -> Counter:
        counts: Counter = Counter()
        for raw_line in text.splitlines():
            line = raw_line.split("!", 1)[0].strip()
            if not line:
                continue
            up = line.upper()

            if re.match(r'^FOR\b', up):
                counts["FOR"] += 1
            if re.match(r'^NEXT\b', up):
                counts["NEXT"] += 1
            if re.match(r'^ENDIF\b', up):
                counts["ENDIF"] += 1
            if re.match(r'^IF\b', up):
                # Single-line IF ... THEN <stmt> does not require ENDIF.
                m = re.match(r'^IF\b.*?\bTHEN\b(.*)$', up)
                if m and m.group(1).strip():
                    continue
                counts["IF_BLOCK"] += 1

        return counts
