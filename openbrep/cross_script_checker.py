from __future__ import annotations

import re

from openbrep.gdl_keywords import GDL_BUILTINS as SHARED_GDL_BUILTINS, GLOBAL_PREFIXES
from openbrep.hsf_project import ScriptType
from openbrep.validator import ValidationIssue


_IDENT_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')
_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)', re.MULTILINE)

class CrossScriptChecker:
    GDL_BUILTINS = SHARED_GDL_BUILTINS

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
            # Filter out GDL global/system variable prefixes (gs_, ac_, GLOB_, SYMB_)
            missing = {v for v in missing
                       if not any(v.upper().startswith(p.upper().rstrip("_"))
                                  for p in GLOBAL_PREFIXES)}
            if missing:
                issues.append(ValidationIssue(
                    level="warning",
                    category="cross_script",
                    file="3d.gdl",
                    message=f"使用了未在参数表定义的变量：{', '.join(sorted(missing)[:8])}",
                ))
        return issues
