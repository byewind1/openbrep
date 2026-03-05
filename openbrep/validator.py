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

        # 跨脚本检查：3D脚本使用的变量是否在参数表中定义
        if script_3d and project.parameters:
            param_names = {p.name.upper() for p in project.parameters}
            GDL_BUILTINS = {
                "ADD","ADDX","ADDY","ADDZ","BLOCK","BRICK","CYLIND","SPHERE",
                "CONE","ELLIPS","PRISM","PRISM_","TUBE","SWEEP","RULED","COONS",
                "FOR","NEXT","IF","THEN","ELSE","ENDIF","WHILE","ENDWHILE",
                "GOTO","GOSUB","RETURN","END","DEL","ROT","ROTX","ROTY","ROTZ",
                "MUL","MULX","MULY","MULZ","PEN","MATERIAL","MODEL","RESOL",
                "TOLER","HOTSPOT","HOTSPOT2","LINE","LINE2","RECT","RECT2",
                "POLY","POLY2","POLY2_","ARC","ARC2","CIRCLE","CIRCLE2",
                "TEXT","TEXT2","RICHTEXT2","PROJECT2","FRAGMENT2","PICTURE2",
                "PRINT","VARDIM1","VARDIM2","REQUEST","IND","INT","ABS","SQR",
                "SQRT","SIN","COS","TAN","ATN","EXP","LOG","A","B","ZZYZX",
                "AND","OR","NOT","MOD","DIV","TRUE","FALSE","PI",
            }
            used_vars = set(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', script_3d))
            used_vars_upper = {v.upper() for v in used_vars}
            missing = used_vars_upper - param_names - GDL_BUILTINS
            missing = {v for v in missing if len(v) > 1}
            if missing:
                issues.append(
                    f"⚠️ 3D脚本使用了未在参数表定义的变量：{', '.join(sorted(missing)[:8])}"
                    + ("..." if len(missing) > 8 else "")
                )

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
