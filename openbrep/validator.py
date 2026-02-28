"""Hard-rule validator for generated GDL scripts."""

from __future__ import annotations

import re
from collections import Counter

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.paramlist_builder import validate_paramlist


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
        issues: list[str] = []
        if "PROJECT2" not in (script_text or "").upper():
            issues.append("缺少PROJECT2")
        return issues

    def validate_3d(self, script_text: str) -> list[str]:
        issues: list[str] = []
        text = script_text or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        if not lines or lines[-1].upper() != "END":
            issues.append("末尾缺少END")

        command_counts = self._count_commands(text)
        add_count = command_counts["ADD"]
        del_count = command_counts["DEL"]
        if add_count != del_count:
            issues.append(f"ADD/DEL不配对，ADD={add_count} DEL={del_count}")

        for_count = command_counts["FOR"]
        next_count = command_counts["NEXT"]
        if for_count != next_count:
            issues.append(f"FOR/NEXT不配对，FOR={for_count} NEXT={next_count}")

        if_count = command_counts["IF_BLOCK"]
        endif_count = command_counts["ENDIF"]
        if if_count != endif_count:
            issues.append(f"IF/ENDIF不配对，IF={if_count} ENDIF={endif_count}")

        return issues

    def validate_all(self, project: HSFProject) -> list[str]:
        """Validate all supported parts of an HSFProject."""
        issues: list[str] = []

        param_text = "\n".join(
            f"{p.type_tag} {p.name} = {p.value}"
            + (f" ! {p.description}" if p.description else "")
            for p in (project.parameters or [])
        )
        for issue in self.validate_params(param_text):
            issues.append(f"paramlist.xml: {issue}")

        script_2d = project.get_script(ScriptType.SCRIPT_2D)
        for issue in self.validate_2d(script_2d):
            issues.append(f"2d.gdl: {issue}")

        script_3d = project.get_script(ScriptType.SCRIPT_3D)
        for issue in self.validate_3d(script_3d):
            issues.append(f"3d.gdl: {issue}")

        return issues

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

            if re.match(r'^ADD(?:\b|X\b|Y\b|Z\b|2\b)', up):
                counts["ADD"] += 1
            if re.match(r'^DEL\b', up):
                counts["DEL"] += 1
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
