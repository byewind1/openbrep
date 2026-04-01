from __future__ import annotations

import re

from openbrep.hsf_project import ScriptType
from openbrep.validator import ValidationIssue


_IDENT_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')
_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)', re.MULTILINE)


class CrossScriptChecker:
    GDL_BUILTINS = {
        "ADD", "ADDX", "ADDY", "ADDZ", "BLOCK", "BRICK", "CYLIND", "SPHERE",
        "CONE", "ELLIPS", "PRISM", "PRISM_", "TUBE", "SWEEP", "RULED", "COONS",
        "FOR", "NEXT", "IF", "THEN", "ELSE", "ENDIF", "WHILE", "ENDWHILE",
        "GOTO", "GOSUB", "RETURN", "END", "DEL", "ROT", "ROTX", "ROTY", "ROTZ",
        "MUL", "MULX", "MULY", "MULZ", "PEN", "MATERIAL", "MODEL", "RESOL",
        "TOLER", "HOTSPOT", "HOTSPOT2", "LINE", "LINE2", "RECT", "RECT2",
        "POLY", "POLY2", "POLY2_", "ARC", "ARC2", "CIRCLE", "CIRCLE2",
        "TEXT", "TEXT2", "RICHTEXT2", "PROJECT2", "FRAGMENT2", "PICTURE2",
        "PRINT", "VARDIM1", "VARDIM2", "REQUEST", "IND", "INT", "ABS", "SQR",
        "SQRT", "SIN", "COS", "TAN", "ATN", "EXP", "LOG", "A", "B", "ZZYZX",
        "AND", "OR", "NOT", "MOD", "DIV", "TRUE", "FALSE", "PI",
    }

    @staticmethod
    def _strip_comments(code: str) -> str:
        lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("[FILE:") or stripped.startswith("!"):
                continue
            idx = line.find("!")
            clean = line[:idx] if idx >= 0 else line
            if clean.strip():
                lines.append(clean)
        return "\n".join(lines)

    @staticmethod
    def _assigned_names(code: str) -> set[str]:
        return {m.group(1).upper() for m in _ASSIGN_RE.finditer(code)}

    def check(self, project) -> list[ValidationIssue]:
        issues = []
        script_3d = self._strip_comments(project.get_script(ScriptType.SCRIPT_3D) or "")
        if script_3d and project.parameters:
            param_names = {p.name.upper() for p in project.parameters}
            master_script = self._strip_comments(project.get_script(ScriptType.MASTER) or "")
            known_names = param_names | self.GDL_BUILTINS | self._assigned_names(master_script)
            used_vars = {m.group(1).upper() for m in _IDENT_RE.finditer(script_3d)}
            missing = used_vars - known_names
            missing = {v for v in missing if len(v) > 1}
            if missing:
                issues.append(ValidationIssue(
                    level="warning",
                    category="cross_script",
                    file="3d.gdl",
                    message=f"使用了未在参数表定义的变量：{', '.join(sorted(missing)[:8])}",
                ))
        return issues
