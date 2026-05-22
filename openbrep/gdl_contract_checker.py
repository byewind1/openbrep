"""GDL/HSF contract checks beyond basic syntax.

These checks are deterministic guardrails for benchmark and release harnesses.
They intentionally avoid full GDL interpretation; the goal is to catch obvious
role/structure violations with clear issue types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbrep.hsf_project import HSFProject


@dataclass(frozen=True)
class GDLContractIssue:
    check_type: str
    file: str
    detail: str
    severity: str = "error"


@dataclass(frozen=True)
class GDLContractResult:
    passed: bool
    issues: list[GDLContractIssue] = field(default_factory=list)


class GDLContractChecker:
    """Check script-role and HSF-structure contracts for a library part."""

    _ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", re.MULTILINE)
    _PARAM_REF_RE = re.compile(
        r"\b(?:VALUES|PARAMETERS|LOCK)\b\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
        re.IGNORECASE,
    )
    _PUSH_RE = re.compile(r"\b(ADD(?:[XYZ])?|MUL|ROT[XYZ]?)\b", re.IGNORECASE)
    _POP_RE = re.compile(r"\bDEL\s*(\d+)?\b", re.IGNORECASE)
    _DELALL_RE = re.compile(r"\bDELALL\b", re.IGNORECASE)

    def check(self, project: "HSFProject | None") -> GDLContractResult:
        if project is None:
            return GDLContractResult(False, [
                GDLContractIssue("missing_project", "project", "HSF project is missing")
            ])

        issues: list[GDLContractIssue] = []
        issues.extend(self._check_required_in_memory_parts(project))
        issues.extend(self._check_parameter_script_refs(project))
        issues.extend(self._check_2d_representation(project))
        issues.extend(self._check_transform_stack(project, "3d.gdl"))
        issues.extend(self._check_derived_variable_role(project))
        return GDLContractResult(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
        )

    def check_hsf_directory(self, hsf_dir: str | Path) -> GDLContractResult:
        root = Path(hsf_dir)
        issues: list[GDLContractIssue] = []
        required = [
            "libpartdata.xml",
            "paramlist.xml",
            "ancestry.xml",
            "calledmacros.xml",
            "libpartdocs.xml",
            "scripts",
        ]
        for rel_path in required:
            path = root / rel_path
            if not path.exists():
                issues.append(GDLContractIssue("missing_hsf_file", rel_path, f"Missing required HSF file: {rel_path}"))
        scripts_dir = root / "scripts"
        if scripts_dir.exists() and not (scripts_dir / "3d.gdl").exists():
            issues.append(GDLContractIssue("missing_hsf_file", "scripts/3d.gdl", "Missing required 3D script"))
        return GDLContractResult(passed=not issues, issues=issues)

    def _check_required_in_memory_parts(self, project: "HSFProject") -> list[GDLContractIssue]:
        issues: list[GDLContractIssue] = []
        if not project.parameters:
            issues.append(GDLContractIssue("missing_paramlist", "paramlist.xml", "Project has no parameters"))
        if not self._script(project, "3d.gdl").strip():
            issues.append(GDLContractIssue("missing_script", "scripts/3d.gdl", "3D script is missing or empty"))
        return issues

    def _check_parameter_script_refs(self, project: "HSFProject") -> list[GDLContractIssue]:
        script = self._strip_comments(self._script(project, "vl.gdl"))
        if not script.strip():
            return []

        declared = {param.name for param in project.parameters}
        issues: list[GDLContractIssue] = []
        seen: set[str] = set()
        for match in self._PARAM_REF_RE.finditer(script):
            name = match.group(1)
            if name in seen or name in declared:
                continue
            seen.add(name)
            issues.append(GDLContractIssue(
                "parameter_script_unknown_param",
                "scripts/vl.gdl",
                f"Parameter script references unknown parameter '{name}'",
            ))
        return issues

    def _check_2d_representation(self, project: "HSFProject") -> list[GDLContractIssue]:
        script = self._strip_comments(self._script(project, "2d.gdl"))
        if not script.strip():
            return [GDLContractIssue("empty_2d_script", "scripts/2d.gdl", "2D script is missing or empty")]
        if not re.search(r"\b(PROJECT2|HOTSPOT2)\b", script, re.IGNORECASE):
            return [GDLContractIssue(
                "missing_2d_anchor",
                "scripts/2d.gdl",
                "2D script should contain PROJECT2 or HOTSPOT2 so the object is visible/selectable in plan",
            )]
        return []

    def _check_transform_stack(self, project: "HSFProject", script_name: str) -> list[GDLContractIssue]:
        script = self._strip_comments(self._script(project, script_name))
        if not script.strip() or self._DELALL_RE.search(script):
            return []

        depth = 0
        issues: list[GDLContractIssue] = []
        for line_no, line in enumerate(script.splitlines(), start=1):
            depth += len(self._PUSH_RE.findall(line))
            for match in self._POP_RE.finditer(line):
                pop_count = int(match.group(1) or "1")
                if pop_count > depth:
                    issues.append(GDLContractIssue(
                        "transform_stack_underflow",
                        f"scripts/{script_name}",
                        f"Line {line_no}: DEL {pop_count} exceeds local transform stack depth {depth}",
                    ))
                    depth = 0
                else:
                    depth -= pop_count
        if depth:
            issues.append(GDLContractIssue(
                "transform_stack_unclosed",
                f"scripts/{script_name}",
                f"Transform stack leaves {depth} unclosed operation(s)",
            ))
        return issues

    def _check_derived_variable_role(self, project: "HSFProject") -> list[GDLContractIssue]:
        master_assigned = self._assigned_names(self._script(project, "1d.gdl"))
        two_d_assigned = self._assigned_names(self._script(project, "2d.gdl"))
        three_d_assigned = self._assigned_names(self._script(project, "3d.gdl"))
        duplicated = sorted((two_d_assigned & three_d_assigned) - master_assigned)
        return [
            GDLContractIssue(
                "derived_var_not_in_master",
                "scripts/1d.gdl",
                f"Derived variable '{name}' is assigned in both 2D and 3D scripts; move shared derivation to Master script",
                severity="warning",
            )
            for name in duplicated
        ]

    @staticmethod
    def _script(project: "HSFProject", script_name: str) -> str:
        from openbrep.hsf_project import ScriptType

        for script_type in ScriptType:
            if script_type.value == script_name:
                return project.get_script(script_type) or ""
        return ""

    @staticmethod
    def _strip_comments(script: str) -> str:
        lines = []
        for line in script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("!"):
                continue
            clean = line.split("!", 1)[0]
            if clean.strip():
                lines.append(clean)
        return "\n".join(lines)

    def _assigned_names(self, script: str) -> set[str]:
        return set(self._ASSIGN_RE.findall(self._strip_comments(script)))
