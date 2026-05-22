from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from benchmark.schema import SemanticAssertion, SuccessCriteria

if TYPE_CHECKING:
    from openbrep.hsf_project import HSFProject


@dataclass(frozen=True)
class CriteriaResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


SCRIPT_ALIASES = {
    "1d.gdl": "1d.gdl",
    "master": "1d.gdl",
    "master.gdl": "1d.gdl",
    "2d.gdl": "2d.gdl",
    "3d.gdl": "3d.gdl",
    "vl.gdl": "vl.gdl",
    "parameter": "vl.gdl",
    "param": "vl.gdl",
    "paramlist.xml": "paramlist.xml",
    "ui.gdl": "ui.gdl",
    "pr.gdl": "pr.gdl",
}


def assert_success_criteria(project: "HSFProject | None", criteria: SuccessCriteria) -> CriteriaResult:
    """Check deterministic benchmark success criteria against a generated HSF project."""
    if project is None:
        return CriteriaResult(False, ["project: missing generated HSF project"])

    failures: list[str] = []
    failures.extend(_assert_required_params(project, criteria.required_params))
    failures.extend(_assert_required_scripts(project, criteria.required_scripts))
    failures.extend(_assert_geometry_hints(project, criteria.geometry_check, criteria.required_scripts))
    failures.extend(_assert_semantic_assertions(project, criteria.semantic_assertions))
    return CriteriaResult(passed=not failures, failures=failures)


def _assert_required_params(project: "HSFProject", required_params: list[str]) -> list[str]:
    declared = {param.name for param in project.parameters}
    return [
        f"required_param: missing {param_name}"
        for param_name in required_params
        if param_name not in declared
    ]


def _assert_required_scripts(project: "HSFProject", required_scripts: list[str]) -> list[str]:
    failures: list[str] = []
    for script_name in required_scripts:
        normalized = _normalize_script_name(script_name)
        if normalized == "paramlist.xml":
            if not project.parameters:
                failures.append("required_script: paramlist.xml has no parameters")
            continue
        content = _script_text(project, normalized)
        if not content.strip():
            failures.append(f"required_script: missing or empty scripts/{normalized}")
    return failures


def _assert_geometry_hints(
    project: "HSFProject",
    geometry_check: str,
    required_scripts: list[str],
) -> list[str]:
    failures: list[str] = []
    check = geometry_check.upper()
    script_text = _all_script_text(project)
    script_text_upper = script_text.upper()

    for command in _commands_implied_by_geometry_check(check):
        if not _contains_command(script_text_upper, command):
            failures.append(f"geometry_command: missing {command}")

    if "FOR" in check and not _contains_command(script_text_upper, "NEXT"):
        failures.append("geometry_command: missing NEXT")

    if any(_normalize_script_name(name) == "2d.gdl" for name in required_scripts):
        two_d = _script_text(project, "2d.gdl").upper()
        if not (_contains_command(two_d, "PROJECT2") or _contains_command(two_d, "HOTSPOT2")):
            failures.append("2d_representation: scripts/2d.gdl must contain PROJECT2 or HOTSPOT2")

    return failures


def _commands_implied_by_geometry_check(check: str) -> list[str]:
    commands: list[str] = []
    for command in ("BLOCK", "CYLIND", "PROJECT2"):
        if command in check:
            commands.append(command)
    if "FOR" in check or "循环" in check or "数量" in check or "分格" in check or "螺栓排列" in check:
        commands.append("FOR")
    return commands


def _assert_semantic_assertions(
    project: "HSFProject",
    assertions: list[SemanticAssertion],
) -> list[str]:
    failures: list[str] = []
    for assertion in assertions:
        script_name = _normalize_script_name(assertion.script or "3d.gdl")
        script = _script_text(project, script_name)
        script_upper = script.upper()
        assertion_type = assertion.type

        if assertion_type == "command_present":
            if not assertion.command:
                failures.append("semantic_assertion: command_present missing command")
            elif not _contains_command(script_upper, assertion.command):
                failures.append(f"semantic_assertion: scripts/{script_name} missing command {assertion.command}")
        elif assertion_type == "param_used":
            if not assertion.param:
                failures.append("semantic_assertion: param_used missing param")
            elif not re.search(rf"\b{re.escape(assertion.param)}\b", script):
                failures.append(f"semantic_assertion: scripts/{script_name} does not use param {assertion.param}")
        elif assertion_type == "expression_present":
            if not assertion.contains:
                failures.append("semantic_assertion: expression_present missing contains")
            elif _compact(assertion.contains) not in _compact(script):
                failures.append(
                    f"semantic_assertion: scripts/{script_name} missing expression containing {assertion.contains!r}"
                )
        elif assertion_type == "transform_balanced":
            failures.extend(_assert_transform_balanced(script, script_name))
        else:
            failures.append(f"semantic_assertion: unsupported type {assertion_type!r}")
    return failures


def _assert_transform_balanced(script: str, script_name: str) -> list[str]:
    if re.search(r"\bDELALL\b", script, re.IGNORECASE):
        return []
    push_count = len(re.findall(r"\b(ADD(?:[XYZ])?|MUL|ROT[XYZ]?)\b", script, re.IGNORECASE))
    pop_count = sum(int(match.group(1) or "1") for match in re.finditer(r"\bDEL\s*(\d+)?\b", script, re.IGNORECASE))
    if push_count == pop_count:
        return []
    return [f"semantic_assertion: scripts/{script_name} transform stack unbalanced push={push_count} pop={pop_count}"]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _contains_command(script_text_upper: str, command: str) -> bool:
    return re.search(rf"\b{re.escape(command.upper())}\b", script_text_upper) is not None


def _normalize_script_name(script_name: str) -> str:
    key = str(script_name).strip().lower()
    return SCRIPT_ALIASES.get(key, key)


def _script_text(project: "HSFProject", script_name: str) -> str:
    from openbrep.hsf_project import ScriptType

    normalized = _normalize_script_name(script_name)
    for script_type in ScriptType:
        if script_type.value == normalized:
            return project.get_script(script_type) or ""
    return ""


def _all_script_text(project: "HSFProject") -> str:
    from openbrep.hsf_project import ScriptType

    return "\n".join(project.get_script(script_type) or "" for script_type in ScriptType)
