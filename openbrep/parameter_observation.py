"""Read-only parameter roles and effective-value observations.

The analysis in this module is intentionally conservative.  It proves only
top-level, unconditional scalar assignments whose dependency graph is acyclic.
Everything else stays unknown for callers to handle explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, TYPE_CHECKING

from openbrep.gdl_previewer import (
    ParameterEvaluationDiagnostic,
    evaluate_parameter_environment,
)

if TYPE_CHECKING:
    from openbrep.hsf_project import GDLParameter


ParameterRoleName = Literal["input", "derived", "unknown", "material"]

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?!=)(.+)$")
_INLINE_IF_RE = re.compile(r"^\s*IF\b.+?\bTHEN\b\s*(.+)$", re.IGNORECASE)
_CALL_PARAMETER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=", re.IGNORECASE)
_BLOCK_START_RE = re.compile(r"^\s*(IF\b.*\bTHEN\s*$|FOR\b)", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"^\s*(ENDIF|NEXT)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"^\s*(?:\d+|[A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")
_FUNCTION_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_LOCK_RE = re.compile(r"^\s*LOCK\b(.*)$", re.IGNORECASE)
_QUOTED_NAME_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')


@dataclass(frozen=True)
class ParameterRole:
    name: str
    role: ParameterRoleName
    depends_on: tuple[str, ...] = ()
    reason: str | None = None
    evidence_lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class ParameterRoleAnalysis:
    roles: dict[str, ParameterRole] = field(default_factory=dict)


@dataclass(frozen=True)
class AlternativeValue:
    value: int | float | None
    reason: str | None = None


@dataclass(frozen=True)
class ObservedParameter:
    name: str
    role: ParameterRoleName
    source_value: Any
    requested_value: Any
    effective_value: Any
    read_only: bool
    depends_on: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "source_value": self.source_value,
            "requested_value": self.requested_value,
            "effective_value": self.effective_value,
            "read_only": self.read_only,
            "depends_on": list(self.depends_on),
            "sources": list(self.sources),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ParameterObservationResult:
    parameters: tuple[ObservedParameter, ...]
    diagnostics: tuple[ParameterEvaluationDiagnostic, ...] = ()

    @property
    def supported(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class _Assignment:
    target: str
    references: tuple[str, ...]
    line: int
    conditional: bool


def _clean_line(raw: str) -> str:
    return raw.split("!", 1)[0].strip()


def _references(expression: str) -> tuple[str, ...]:
    functions = {name.upper() for name in _FUNCTION_RE.findall(expression)}
    seen: set[str] = set()
    refs: list[str] = []
    for token in _IDENTIFIER_RE.findall(expression):
        normalized = token.upper()
        if normalized in functions or normalized in {"AND", "OR", "NOT", "TRUE", "FALSE"}:
            continue
        if normalized not in seen:
            seen.add(normalized)
            refs.append(normalized)
    return tuple(refs)


def classify_parameter_roles(
    parameter_names: Iterable[str],
    master_script: str,
    *,
    parameter_types: Mapping[str, str] | None = None,
) -> ParameterRoleAnalysis:
    """Classify declared parameters without interpreting GDL expressions."""
    names = [str(name) for name in parameter_names]
    canonical = {name.upper(): name for name in names}
    types = {str(name).upper(): value for name, value in (parameter_types or {}).items()}
    assignments: dict[str, list[_Assignment]] = {}
    macro_unknown: set[str] = set()
    block_depth = 0
    in_subroutine = False

    for line_number, raw in enumerate(master_script.splitlines(), 1):
        line = _clean_line(raw)
        if not line:
            continue
        if _BLOCK_END_RE.match(line):
            block_depth = max(0, block_depth - 1)
            continue
        if re.match(r"^\s*RETURN\b", line, re.IGNORECASE):
            in_subroutine = False
            continue
        if _LABEL_RE.match(line):
            in_subroutine = True
            continue
        if re.match(r"^\s*CALL\b", line, re.IGNORECASE):
            for target in _CALL_PARAMETER_RE.findall(line):
                normalized = target.upper()
                if normalized in canonical:
                    macro_unknown.add(normalized)
            continue

        inline_if = _INLINE_IF_RE.match(line)
        candidate = inline_if.group(1).strip() if inline_if else line
        match = _ASSIGNMENT_RE.match(candidate)
        if match:
            target = match.group(1).upper()
            assignments.setdefault(target, []).append(_Assignment(
                target=target,
                references=_references(match.group(2)),
                line=line_number,
                conditional=bool(inline_if or block_depth or in_subroutine),
            ))

        if _BLOCK_START_RE.match(line) and inline_if is None:
            block_depth += 1

    proven: dict[str, _Assignment] = {}
    reasons: dict[str, str] = {}
    for target, writes in assignments.items():
        last = writes[-1]
        if last.conditional:
            reasons[target] = "conditional_assignment"
        else:
            proven[target] = last
    for target in macro_unknown:
        proven.pop(target, None)
        reasons[target] = "dynamic_macro"

    memo: dict[str, tuple[str, ...] | None] = {}

    def resolve_roots(name: str, stack: tuple[str, ...] = ()) -> tuple[str, ...] | None:
        if name in memo:
            return memo[name]
        if name in stack:
            for cycle_name in stack[stack.index(name):]:
                reasons[cycle_name] = "dependency_cycle"
                memo[cycle_name] = None
            return None
        assignment = proven.get(name)
        if assignment is None:
            if name in canonical:
                return (name,)
            reasons[name] = reasons.get(name, "unresolved_dependency")
            memo[name] = None
            return None

        roots: list[str] = []
        for dependency in assignment.references:
            resolved = resolve_roots(dependency, (*stack, name))
            if resolved is None:
                reasons[name] = reasons.get(name, "unresolved_dependency")
                memo[name] = None
                return None
            for root in resolved:
                if root not in roots:
                    roots.append(root)
        memo[name] = tuple(roots)
        return memo[name]

    roles: dict[str, ParameterRole] = {}
    material_types = {"MATERIAL", "PENCOLOR", "FILLPATTERN", "LINETYPE"}
    for normalized, original in canonical.items():
        lines = tuple(item.line for item in assignments.get(normalized, ()))
        if types.get(normalized, "").upper() in material_types:
            roles[original] = ParameterRole(
                name=original, role="material", reason="material_like_type", evidence_lines=lines,
            )
            continue
        if normalized in reasons and normalized not in proven:
            roles[original] = ParameterRole(
                name=original, role="unknown", reason=reasons[normalized], evidence_lines=lines,
            )
            continue
        if normalized not in proven:
            roles[original] = ParameterRole(name=original, role="input", evidence_lines=lines)
            continue
        roots = resolve_roots(normalized)
        if roots is None or normalized in reasons:
            roles[original] = ParameterRole(
                name=original,
                role="unknown",
                reason=reasons.get(normalized, "unresolved_dependency"),
                evidence_lines=lines,
            )
            continue
        roles[original] = ParameterRole(
            name=original,
            role="derived",
            depends_on=tuple(canonical[root] for root in roots if root in canonical),
            evidence_lines=lines,
        )
    return ParameterRoleAnalysis(roles=roles)


def select_alternative_value(
    value: int | float,
    type_tag: str,
    *,
    options: Iterable[int | float] | None = None,
    value_range: Iterable[int | float] | None = None,
    delta_ratio: float = 0.5,
) -> AlternativeValue:
    """Choose one legal, distinct probe value without crossing declarations."""
    kind = str(type_tag).upper()
    if kind in {"MATERIAL", "PENCOLOR", "FILLPATTERN", "LINETYPE"}:
        return AlternativeValue(None, "non_geometry_parameter")
    if kind == "BOOLEAN":
        return AlternativeValue(0 if bool(value) else 1)
    candidates = list(options or ())
    if not candidates and value_range is not None:
        bounds = list(value_range)
        if len(bounds) >= 2:
            low, high = float(bounds[0]), float(bounds[-1])
            if low == high:
                return AlternativeValue(None, "no_legal_alternative")
            candidates = [low, high]
    if candidates:
        for candidate in candidates:
            if float(candidate) != float(value):
                if kind == "INTEGER":
                    return AlternativeValue(int(round(float(candidate))))
                return AlternativeValue(float(candidate))
        return AlternativeValue(None, "no_legal_alternative")
    if kind == "INTEGER":
        return AlternativeValue(int(value) + 1)
    if kind in {"LENGTH", "ANGLE", "REALNUM"}:
        return AlternativeValue(float(value) * (1 + delta_ratio) if float(value) != 0 else 1.0)
    return AlternativeValue(None, "unsupported_type")


def parse_locked_parameter_names(
    parameter_script: str,
    parameter_names: Iterable[str],
) -> set[str]:
    """Return explicitly observed LOCK targets using normalized names."""
    declared = {str(name).upper() for name in parameter_names}
    locked: set[str] = set()
    for raw in parameter_script.splitlines():
        match = _LOCK_RE.match(_clean_line(raw))
        if not match:
            continue
        tail = match.group(1).strip()
        if tail.upper() == "ALL":
            locked.update(declared)
            continue
        locked.update(
            name.upper()
            for name in _QUOTED_NAME_RE.findall(tail)
            if name.upper() in declared
        )
    return locked


def observe_parameters(
    parameters: Iterable["GDLParameter"],
    master_script: str,
    evaluation_values: Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
    parameter_script: str = "",
) -> ParameterObservationResult:
    """Return source, requested, and Master-effective values without writing."""
    parameter_list = list(parameters)
    roles = classify_parameter_roles(
        [parameter.name for parameter in parameter_list],
        master_script,
        parameter_types={parameter.name: parameter.type_tag for parameter in parameter_list},
    ).roles
    locked_names = parse_locked_parameter_names(
        parameter_script,
        (parameter.name for parameter in parameter_list),
    )
    evaluated = evaluate_parameter_environment(master_script, evaluation_values)
    override_values = {str(name).upper(): value for name, value in (overrides or {}).items()}
    observed: list[ObservedParameter] = []
    for parameter in parameter_list:
        normalized = parameter.name.upper()
        role = roles[parameter.name]
        sources = ["paramlist.xml"]
        sources.extend(f"scripts/1d.gdl:{line}" for line in role.evidence_lines)
        if normalized in locked_names:
            sources.append("scripts/vl.gdl:LOCK")
        observed.append(ObservedParameter(
            name=parameter.name,
            role=role.role,
            source_value=parameter.value,
            requested_value=override_values.get(normalized, parameter.value),
            effective_value=evaluated.values.get(normalized, parameter.value),
            read_only=role.role == "derived" or normalized in locked_names,
            depends_on=role.depends_on,
            sources=tuple(sources),
            reason=role.reason,
        ))
    return ParameterObservationResult(
        parameters=tuple(observed),
        diagnostics=tuple(evaluated.diagnostics),
    )


__all__ = [
    "ParameterRole",
    "ParameterRoleAnalysis",
    "AlternativeValue",
    "ObservedParameter",
    "ParameterObservationResult",
    "classify_parameter_roles",
    "observe_parameters",
    "parse_locked_parameter_names",
    "select_alternative_value",
]
