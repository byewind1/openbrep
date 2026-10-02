"""Evaluate an explicitly bound spiral-stair project contract.

This module consumes evidence produced by the existing Master interpreter and
3D previewer. It does not infer applicability from project names, parse GDL
expressions, or mutate project source.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    from openbrep.gdl_previewer import ParameterEvaluationResult, Preview3DResult, PreviewMesh3D
    from openbrep.hsf_project import HSFProject


ContractCheckStatus = Literal["pass", "fail", "unknown"]
ContractApplicability = Literal["not_applicable", "applicable", "invalid"]
CONTRACT_RELATIVE_PATH = Path(".openbrep/contracts/stair.json")
NUMERIC_TOLERANCE = 1e-6


@dataclass(frozen=True)
class StairContractCheck:
    check_id: str
    status: ContractCheckStatus
    observed: Any = None
    expected: Any = None
    tolerance: float | None = None
    evidence_source: str = "unavailable"
    blocking: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "observed": self.observed,
            "expected": self.expected,
            "tolerance": self.tolerance,
            "evidence_source": self.evidence_source,
            "blocking": self.blocking,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StairContractReport:
    applicability: ContractApplicability
    contract_hash: str | None = None
    checks: list[StairContractCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(check.status == "fail" and check.blocking for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        counts = {status: 0 for status in ("pass", "fail", "unknown")}
        for check in self.checks:
            counts[check.status] += 1
        return {
            "applicability": self.applicability,
            "contract_hash": self.contract_hash,
            "passed": self.passed,
            "counts": counts,
            "checks": [check.to_dict() for check in self.checks],
        }


def evaluate_stair_contract(
    project: "HSFProject",
    *,
    evaluated: "ParameterEvaluationResult | None",
    preview: "Preview3DResult | None",
) -> StairContractReport:
    """Evaluate ``.openbrep/contracts/stair.json`` from supplied evidence."""
    path = project.root / CONTRACT_RELATIVE_PATH
    if not path.is_file():
        return StairContractReport(applicability="not_applicable")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _invalid_report(None, f"合同无法读取：{exc}")

    contract_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _invalid_report(contract_hash, f"合同 JSON 损坏：{exc}")

    validation_error = _validate_contract(payload)
    if validation_error:
        return _invalid_report(contract_hash, validation_error)

    bindings: Mapping[str, str] = payload["parameter_bindings"]
    constraints: Mapping[str, Any] = payload.get("constraints", {})
    geometry: Mapping[str, Any] = payload.get("geometry_bindings", {})
    runtime_values = (
        {str(name).upper(): value for name, value in evaluated.runtime_values.items()}
        if evaluated is not None
        else {}
    )
    evaluation_supported = evaluated is not None and not evaluated.diagnostics

    checks: list[StairContractCheck] = []
    checks.append(_positive_integer_check(bindings, runtime_values, evaluation_supported))
    checks.append(_riser_relation_check(bindings, runtime_values, evaluation_supported))
    checks.extend(_range_checks(bindings, constraints, runtime_values, evaluation_supported))
    checks.extend(_radial_checks(bindings, constraints, runtime_values, evaluation_supported))
    checks.extend(
        _geometry_checks(bindings, geometry, runtime_values, preview, evaluation_supported)
    )
    checks.extend(_unchecked_professional_constraints())
    return StairContractReport(
        applicability="applicable",
        contract_hash=contract_hash,
        checks=checks,
    )


def _validate_contract(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "合同根节点必须是对象"
    if payload.get("schema_version") != 1:
        return "仅支持 schema_version=1"
    if payload.get("type") != "spiral_stair":
        return "stair.json 的 type 必须是 spiral_stair"
    if not isinstance(payload.get("parameter_bindings"), dict):
        return "parameter_bindings 必须是对象"
    if not isinstance(payload.get("constraints", {}), dict):
        return "constraints 必须是对象"
    if not isinstance(payload.get("geometry_bindings", {}), dict):
        return "geometry_bindings 必须是对象"
    provenance = payload.get("provenance")
    if (
        not isinstance(provenance, dict)
        or not isinstance(provenance.get("source"), str)
        or not provenance["source"].strip()
    ):
        return "provenance.source 必须声明合同来源"
    for name, constraint in payload.get("constraints", {}).items():
        if not isinstance(constraint, dict):
            return f"约束 {name} 必须是对象"
        if constraint.get("source") not in {"project_choice", "test_profile"}:
            return f"约束 {name} 必须声明 source=project_choice/test_profile"
    return None


def _invalid_report(contract_hash: str | None, detail: str) -> StairContractReport:
    return StairContractReport(
        applicability="invalid",
        contract_hash=contract_hash,
        checks=[
            StairContractCheck(
                check_id="contract_valid",
                status="fail",
                expected="valid schema_version=1 spiral_stair contract",
                evidence_source="contract_file",
                blocking=True,
                detail=detail,
            )
        ],
    )


def _value(bindings: Mapping[str, str], values: Mapping[str, Any], role: str) -> float | None:
    name = bindings.get(role)
    if not isinstance(name, str) or not name:
        return None
    value = values.get(name.upper())
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _unknown(
    check_id: str, expected: Any, detail: str, *, blocking: bool = False
) -> StairContractCheck:
    return StairContractCheck(
        check_id=check_id,
        status="unknown",
        expected=expected,
        evidence_source="unavailable",
        blocking=blocking,
        detail=detail,
    )


def _positive_integer_check(
    bindings: Mapping[str, str],
    values: Mapping[str, Any],
    evaluation_supported: bool,
) -> StairContractCheck:
    steps = _value(bindings, values, "num_steps") if evaluation_supported else None
    if steps is None:
        return _unknown(
            "positive_integer_steps",
            "positive integer",
            "缺少可用的 num_steps 求值证据",
            blocking=True,
        )
    passed = steps > 0 and steps.is_integer()
    return StairContractCheck(
        check_id="positive_integer_steps",
        status="pass" if passed else "fail",
        observed=steps,
        expected="positive integer",
        tolerance=0.0,
        evidence_source="master_parameter_environment",
        blocking=True,
        detail="级数必须是正整数",
    )


def _riser_relation_check(
    bindings: Mapping[str, str],
    values: Mapping[str, Any],
    evaluation_supported: bool,
) -> StairContractCheck:
    height = _value(bindings, values, "height") if evaluation_supported else None
    steps = _value(bindings, values, "num_steps") if evaluation_supported else None
    riser = _value(bindings, values, "step_riser") if evaluation_supported else None
    if height is None or steps is None or riser is None or steps <= 0:
        return _unknown(
            "step_riser_relation",
            "height / num_steps",
            "绑定缺失或 Master 求值不受支持",
            blocking=True,
        )
    expected = height / steps
    return StairContractCheck(
        check_id="step_riser_relation",
        status="pass"
        if math.isclose(riser, expected, abs_tol=NUMERIC_TOLERANCE, rel_tol=0.0)
        else "fail",
        observed=riser,
        expected=expected,
        tolerance=NUMERIC_TOLERANCE,
        evidence_source="master_parameter_environment",
        blocking=True,
        detail="step_riser 必须等于 height / num_steps",
    )


def _range_checks(
    bindings: Mapping[str, str],
    constraints: Mapping[str, Any],
    values: Mapping[str, Any],
    evaluation_supported: bool,
) -> list[StairContractCheck]:
    result: list[StairContractCheck] = []
    for check_id, role in (
        ("step_riser_range", "step_riser"),
        ("handrail_height_range", "handrail_height"),
    ):
        constraint = constraints.get(check_id)
        if constraint is None:
            continue
        observed = _value(bindings, values, role) if evaluation_supported else None
        expected = {"min": constraint.get("min"), "max": constraint.get("max")}
        blocking = bool(constraint.get("blocking", False))
        if observed is None or not all(
            isinstance(expected[key], (int, float)) for key in ("min", "max")
        ):
            result.append(
                _unknown(check_id, expected, "绑定、求值或范围边界不可用", blocking=blocking)
            )
            continue
        passed = float(expected["min"]) <= observed <= float(expected["max"])
        result.append(
            StairContractCheck(
                check_id=check_id,
                status="pass" if passed else "fail",
                observed=observed,
                expected=expected,
                evidence_source=f"contract:{constraint['source']}+master_parameter_environment",
                blocking=blocking,
                detail=f"{role} 必须在合同范围内",
            )
        )
    return result


def _radial_checks(
    bindings: Mapping[str, str],
    constraints: Mapping[str, Any],
    values: Mapping[str, Any],
    evaluation_supported: bool,
) -> list[StairContractCheck]:
    specs = (
        ("rail_center_inset", "tread_outer_radius", "rail_center_radius"),
        ("tread_pole_overlap", "pole_radius", "tread_inner_radius"),
    )
    result: list[StairContractCheck] = []
    for check_id, outer_role, inner_role in specs:
        constraint = constraints.get(check_id)
        if constraint is None:
            continue
        outer = _value(bindings, values, outer_role) if evaluation_supported else None
        inner = _value(bindings, values, inner_role) if evaluation_supported else None
        expected = constraint.get("expected")
        tolerance = constraint.get("tolerance", NUMERIC_TOLERANCE)
        blocking = bool(constraint.get("blocking", False))
        if (
            outer is None
            or inner is None
            or not isinstance(expected, (int, float))
            or not isinstance(tolerance, (int, float))
        ):
            result.append(
                _unknown(check_id, expected, "绑定、求值或合同期望不可用", blocking=blocking)
            )
            continue
        observed = outer - inner
        result.append(
            StairContractCheck(
                check_id=check_id,
                status="pass"
                if math.isclose(observed, float(expected), abs_tol=float(tolerance), rel_tol=0.0)
                else "fail",
                observed=observed,
                expected=float(expected),
                tolerance=float(tolerance),
                evidence_source=f"contract:{constraint['source']}+master_parameter_environment",
                blocking=blocking,
                detail=f"{outer_role} - {inner_role} 必须符合项目合同",
            )
        )
    return result


def _matching_meshes(
    preview: "Preview3DResult | None", binding: Any
) -> list["PreviewMesh3D"] | None:
    if preview is None or not isinstance(binding, dict):
        return None
    command = binding.get("command")
    source_line = binding.get("source_line")
    if not isinstance(command, str) or not isinstance(source_line, int):
        return None
    return [
        mesh
        for mesh in preview.meshes
        if mesh.source_ref is not None
        and mesh.source_ref.command.upper() == command.upper()
        and mesh.source_ref.line == source_line
    ]


def _geometry_checks(
    bindings: Mapping[str, str],
    geometry: Mapping[str, Any],
    values: Mapping[str, Any],
    preview: "Preview3DResult | None",
    evaluation_supported: bool,
) -> list[StairContractCheck]:
    treads = _matching_meshes(preview, geometry.get("treads"))
    balusters = _matching_meshes(preview, geometry.get("balusters"))
    steps = _value(bindings, values, "num_steps") if evaluation_supported else None
    show_top = _value(bindings, values, "show_top_tread") if evaluation_supported else None
    height = _value(bindings, values, "height") if evaluation_supported else None
    riser = _value(bindings, values, "step_riser") if evaluation_supported else None

    checks: list[StairContractCheck] = []
    if not treads or steps is None or show_top is None:
        checks.append(
            _unknown(
                "top_tread_count",
                "N when shown, N-1 when hidden",
                "缺少踏步组件身份或参数证据",
                blocking=True,
            )
        )
    else:
        expected_count = int(steps) if show_top != 0 else max(0, int(steps) - 1)
        checks.append(
            StairContractCheck(
                check_id="top_tread_count",
                status="pass" if len(treads) == expected_count else "fail",
                observed=len(treads),
                expected=expected_count,
                tolerance=0.0,
                evidence_source="preview_mesh_source_ref",
                blocking=True,
                detail="顶部开关必须控制实际踏步数量",
            )
        )

    if treads is None or not treads or height is None or riser is None or show_top is None:
        checks.append(
            _unknown(
                "top_tread_elevation",
                "height or height-step_riser",
                "缺少踏步组件身份或参数证据",
                blocking=True,
            )
        )
    else:
        observed_top = max(max(mesh.z) for mesh in treads if mesh.z)
        expected_top = height if show_top != 0 else height - riser
        checks.append(
            StairContractCheck(
                check_id="top_tread_elevation",
                status="pass"
                if math.isclose(observed_top, expected_top, abs_tol=NUMERIC_TOLERANCE, rel_tol=0.0)
                else "fail",
                observed=observed_top,
                expected=expected_top,
                tolerance=NUMERIC_TOLERANCE,
                evidence_source="preview_mesh_source_ref",
                blocking=True,
                detail="最高踏步顶标高必须匹配顶部开关状态",
            )
        )

    if treads is None or balusters is None or not treads or not balusters:
        checks.append(
            _unknown(
                "baluster_tread_alignment",
                "each baluster base equals matching tread top",
                "缺少踏步或立柱组件身份",
                blocking=True,
            )
        )
    else:
        tread_tops = sorted(max(mesh.z) for mesh in treads if mesh.z)
        baluster_bases = sorted(min(mesh.z) for mesh in balusters if mesh.z)
        aligned = len(tread_tops) == len(baluster_bases) and all(
            math.isclose(base, top, abs_tol=NUMERIC_TOLERANCE, rel_tol=0.0)
            for base, top in zip(baluster_bases, tread_tops)
        )
        checks.append(
            StairContractCheck(
                check_id="baluster_tread_alignment",
                status="pass" if aligned else "fail",
                observed={"baluster_bases": baluster_bases, "tread_tops": tread_tops},
                expected="pairwise equal elevations",
                tolerance=NUMERIC_TOLERANCE,
                evidence_source="preview_mesh_source_ref",
                blocking=True,
                detail="每根立柱底点必须落在对应踏步顶面",
            )
        )
    return checks


def _unchecked_professional_constraints() -> list[StairContractCheck]:
    return [
        _unknown("headroom", "project-specific verified requirement", "首版未检查净高"),
        _unknown("walking_line", "project-specific verified requirement", "首版未检查行走线宽度"),
        _unknown(
            "structural_and_regional_codes",
            "authoritative external verification",
            "首版不推断结构承载或地区规范合规",
        ),
    ]
