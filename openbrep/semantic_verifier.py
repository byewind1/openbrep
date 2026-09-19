"""
GDL semantic verifier — geometry-level sanity checks beyond compile success.

"编译通过 ≠ 任务完成": LP_XMLConverter only proves the GDL text parses. It says
nothing about whether the resulting geometry is the object the user asked for.
This module runs the lightweight gdl_previewer interpreter against a project's
3D script and checks two things machine-verifiable without ArchiCAD:

  1. mesh_health   — the script contains at least one recognized geometry
                      command (BLOCK/CYLIND/PRISM_/...) but produced zero
                      meshes, or produced meshes whose combined bounding box
                      is degenerate (collapsed to a point/line/plane on at
                      least one axis). Catches "compiles fine, draws nothing".
  2. bbox_vs_dims  — the combined bounding box (sorted dimensions, to tolerate
                      axis rotation) roughly matches the object's declared
                      A/B/ZZYZX — GDL's reserved width/depth/height params.
                      Catches "geometry exists but ignores the parameters".
  3. param_sweep   — perturb each declared numeric parameter one at a time and
                      re-preview. A parameter that makes the mesh vanish
                      entirely (sweep_mesh_vanished) is a blocking failure; a
                      parameter whose change leaves the mesh byte-identical
                      (sweep_unresponsive) is a non-blocking hint — many
                      legitimate parameters (material, angle, text) don't
                      move geometry the MVP previewer models.

verify_semantics(project) never raises. A previewer crash (the interpreter is
a pragmatic MVP subset, not a full GDL engine) is reported as a non-blocking
`preview_error` issue, not a failure — false negatives here are acceptable,
false positives that block a correct CREATE are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from openbrep.gdl_ast import ControlBlock, GeometryCall, parse_gdl_script
from openbrep.static_checker import RESERVED_PARAMS

if TYPE_CHECKING:
    from openbrep.contracts.stair import StairContractReport
    from openbrep.gdl_previewer import Preview3DResult
    from openbrep.hsf_project import HSFProject

__all__ = [
    "SemanticIssue",
    "SemanticVerificationResult",
    "check_mesh_health",
    "check_bounding_box_against_dimensions",
    "sweep_parameter_observations",
    "sweep_parameters",
    "verify_semantics",
]

DEFAULT_BBOX_TOLERANCE = 0.5
DEFAULT_SWEEP_DELTA_RATIO = 0.5
DEFAULT_SWEEP_MAX_PARAMS = 12
_DEGENERATE_EPS = 1e-6
_BBOX_ROUND_NDIGITS = 6


@dataclass
class SemanticIssue:
    check_type: str
    # "mesh_empty" | "mesh_degenerate" | "bbox_mismatch" | "preview_error"
    # | "sweep_mesh_vanished" | "sweep_unresponsive" | "sweep_preview_error"
    detail: str
    blocking: bool = True


@dataclass
class SemanticVerificationResult:
    passed: bool
    issues: list[SemanticIssue] = field(default_factory=list)
    sweep: "ParameterSweepReport | None" = None
    project_contract: "StairContractReport | None" = None

    @property
    def blocking_issues(self) -> list[SemanticIssue]:
        result = [issue for issue in self.issues if issue.blocking]
        if self.project_contract is None:
            return result
        result.extend(
            SemanticIssue(
                check_type=f"project_contract:{check.check_id}",
                detail=check.detail,
                blocking=True,
            )
            for check in self.project_contract.checks
            if check.status == "fail" and check.blocking
        )
        return result


@dataclass
class ParameterSweepSample:
    name: str
    role: str
    kind: str
    status: str
    source_value: Any = None
    candidate_value: Any = None
    effective_value: Any = None
    geometry_changed: bool | None = None
    reason: str | None = None
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "kind": self.kind,
            "status": self.status,
            "source_value": self.source_value,
            "candidate_value": self.candidate_value,
            "effective_value": self.effective_value,
            "geometry_changed": self.geometry_changed,
            "reason": self.reason,
            "depends_on": list(self.depends_on),
        }


@dataclass
class ParameterSweepReport:
    issues: list[SemanticIssue] = field(default_factory=list)
    samples: list[ParameterSweepSample] = field(default_factory=list)
    eligible: int = 0
    total_parameters: int = 0

    @property
    def tested_names(self) -> list[str]:
        return [
            sample.name for sample in self.samples
            if sample.kind == "driver" and sample.status in {"tested", "unknown"}
        ]

    @property
    def tested(self) -> int:
        return sum(
            sample.kind == "driver" and sample.status == "tested"
            for sample in self.samples
        )

    @property
    def skipped(self) -> int:
        return sum(
            sample.kind == "driver" and sample.status == "skipped"
            for sample in self.samples
        )

    @property
    def unknown(self) -> int:
        return sum(
            sample.kind == "driver" and sample.status == "unknown"
            for sample in self.samples
        )

    @property
    def failed(self) -> int:
        return sum(
            sample.kind == "driver" and sample.status == "failed"
            for sample in self.samples
        )

    @property
    def coverage(self) -> float:
        attempted = len(self.tested_names)
        return attempted / self.eligible if self.eligible else 1.0

    @property
    def total_parameter_coverage(self) -> float:
        return len(self.tested_names) / self.total_parameters if self.total_parameters else 1.0

    def sample_for(self, name: str) -> ParameterSweepSample:
        normalized = name.upper()
        for sample in self.samples:
            if sample.name.upper() == normalized:
                return sample
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tested": self.tested,
            "skipped": self.skipped,
            "unknown": self.unknown,
            "failed": self.failed,
            "eligible": self.eligible,
            "total_parameters": self.total_parameters,
            "coverage": self.coverage,
            "total_parameter_coverage": self.total_parameter_coverage,
            "samples": [sample.to_dict() for sample in self.samples],
        }


def _scene_bbox(meshes: list) -> Optional[tuple[float, float, float, float, float, float]]:
    """Combined (min_x, max_x, min_y, max_y, min_z, max_z) across all meshes, or None if empty."""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for mesh in meshes:
        xs.extend(mesh.x)
        ys.extend(mesh.y)
        zs.extend(mesh.z)
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _has_geometry_call(controls: list[ControlBlock]) -> bool:
    for cb in controls:
        for child in cb.children + cb.else_children:
            if isinstance(child, GeometryCall):
                return True
            if isinstance(child, ControlBlock) and _has_geometry_call([child]):
                return True
    return False


def _script_has_geometry_command(script_3d: str) -> bool:
    script = parse_gdl_script(script_3d)
    return bool(script.geometry) or _has_geometry_call(script.controls)


def _mesh_signature(result: "Preview3DResult") -> tuple:
    """Cheap fingerprint of a preview result: vertex count + scene bbox +
    sum of each individual mesh's own extents.

    The scene-level bbox alone misses "internal-only" changes — e.g. a
    shelf's thickness growing without the outer case's envelope moving — so
    per-mesh extents are summed in too. Still not a full geometry diff (two
    different meshes could in theory land on the same sum), but enough to
    catch "this parameter changed and literally nothing moved".
    """
    vertex_count = sum(len(mesh.x) for mesh in result.meshes)
    scene_bbox = _scene_bbox(result.meshes)
    rounded_scene_bbox = (
        tuple(round(v, _BBOX_ROUND_NDIGITS) for v in scene_bbox) if scene_bbox else None
    )
    extent_sum = 0.0
    for mesh in result.meshes:
        mesh_bbox = _scene_bbox([mesh])
        if mesh_bbox is not None:
            min_x, max_x, min_y, max_y, min_z, max_z = mesh_bbox
            extent_sum += (max_x - min_x) + (max_y - min_y) + (max_z - min_z)
    return (len(result.meshes), vertex_count, rounded_scene_bbox, round(extent_sum, _BBOX_ROUND_NDIGITS))


def _perturb_value(value: float, delta_ratio: float, *, is_boolean: bool = False) -> float:
    """Return a different value to sweep a parameter to.

    Boolean parameters (0/1 hasXxx flags) are toggled rather than scaled,
    since 0 * anything is still 0. The toggle must only apply when the
    parameter is *semantically* Boolean (caller passes is_boolean=True from
    the parameter's type_tag): a Length/RealNum whose current value happens to
    be exactly 1.0 is NOT a flag, and flipping it to 0.0 collapses the bbox
    and corrupts sweep evidence (P1-b+d regression, sweeping A=1.0).

    Callers that cannot resolve the parameter type fall back to the minimal
    assumption (is_boolean=False, numeric scaling): a wrong toggle destroys
    the geometry, while a slightly-off scale only distorts it, so scaling is
    the safer default.
    """
    if is_boolean:
        return 1.0 - value
    return value * (1 + delta_ratio)


def sweep_parameter_observations(
    project: Optional["HSFProject"],
    *,
    delta_ratio: float = DEFAULT_SWEEP_DELTA_RATIO,
    max_params: int = DEFAULT_SWEEP_MAX_PARAMS,
    baseline_result: "Preview3DResult | None" = None,
    baseline_effective: dict[str, Any] | None = None,
) -> ParameterSweepReport:
    """Sweep eligible inputs and verify proven derived relationships."""
    report = ParameterSweepReport()
    if project is None:
        return report

    from openbrep.hsf_project import ScriptType
    from openbrep.gdl_previewer import evaluate_parameter_environment, preview_3d_script
    from openbrep.parameter_observation import (
        classify_parameter_roles,
        select_alternative_value,
    )
    from openbrep.workbench.project_parameter_service import (
        parameter_values,
        parse_values_declarations,
    )

    script_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
    if not script_3d.strip():
        return report

    setup_script = project.get_script(ScriptType.MASTER) or ""
    source_params = parameter_values(project)
    report.total_parameters = len(project.parameters)
    if not source_params:
        return report

    parameter_by_name = {parameter.name.upper(): parameter for parameter in project.parameters}
    roles = classify_parameter_roles(
        parameter_by_name,
        setup_script,
        parameter_types={name: parameter.type_tag for name, parameter in parameter_by_name.items()},
    ).roles
    domains = {
        name.upper(): value
        for name, value in parse_values_declarations(
            project.get_script(ScriptType.PARAM) or ""
        ).items()
    }

    baseline_environment = None
    if baseline_effective is None:
        evaluated_baseline = evaluate_parameter_environment(setup_script, source_params)
        baseline_effective = evaluated_baseline.values
        baseline_environment = evaluated_baseline.runtime_values
    if baseline_result is None:
        try:
            baseline_result = preview_3d_script(
                script_3d, parameters=baseline_environment or baseline_effective,
                unknown_command_policy="warn", quality="fast",
            )
        except Exception:
            return report

    baseline_bbox = _scene_bbox(baseline_result.meshes)
    if baseline_bbox is None:
        return report

    baseline_sig = _mesh_signature(baseline_result)
    eligible: list[tuple[str, Any, Any]] = []
    derived_roles = {name: role for name, role in roles.items() if role.role == "derived"}

    for name in sorted(parameter_by_name):
        parameter = parameter_by_name[name]
        role = roles[name]
        baseline_value = source_params.get(name)
        if role.role == "derived":
            continue
        if not isinstance(baseline_value, (int, float)):
            report.samples.append(ParameterSweepSample(
                name=name, role=role.role, kind="driver", status="skipped",
                source_value=baseline_value, reason="non_numeric_parameter",
            ))
            continue
        domain = domains.get(name, {})
        selection = select_alternative_value(
            baseline_value,
            parameter.type_tag,
            options=domain.get("options"),
            value_range=domain.get("range"),
            delta_ratio=delta_ratio,
        )
        if selection.value is None:
            report.samples.append(ParameterSweepSample(
                name=name, role=role.role, kind="driver", status="skipped",
                source_value=baseline_value, reason=selection.reason,
            ))
            continue
        eligible.append((name, baseline_value, selection.value))

    report.eligible = len(eligible)
    for name, baseline_value, candidate in eligible[:max_params]:
        role = roles[name]
        perturbed_source = dict(source_params)
        perturbed_source[name] = candidate

        try:
            perturbed_environment = evaluate_parameter_environment(
                setup_script, perturbed_source,
            )
            perturbed_result = preview_3d_script(
                script_3d, parameters=perturbed_environment.runtime_values,
                unknown_command_policy="warn", quality="fast",
            )
        except Exception as exc:
            report.issues.append(SemanticIssue(
                check_type="sweep_preview_error",
                detail=f"参数 {name} 扫描时预览异常（不计入失败）：{exc}",
                blocking=False,
            ))
            report.samples.append(ParameterSweepSample(
                name=name, role=role.role, kind="driver", status="failed",
                source_value=baseline_value, candidate_value=candidate,
                reason="preview_error",
            ))
            continue

        perturbed_bbox = _scene_bbox(perturbed_result.meshes)
        changed = _mesh_signature(perturbed_result) != baseline_sig if perturbed_bbox else True
        report.samples.append(ParameterSweepSample(
            name=name,
            role=role.role,
            kind="driver",
            status="unknown" if role.role == "unknown" else "tested",
            source_value=baseline_value,
            candidate_value=candidate,
            effective_value=perturbed_environment.values.get(name),
            geometry_changed=changed,
            reason=role.reason,
        ))
        if perturbed_bbox is None:
            is_boolean = parameter_by_name[name].type_tag == "Boolean"
            is_expected_toggle_off = is_boolean and baseline_value == 1.0
            report.issues.append(SemanticIssue(
                check_type="sweep_mesh_vanished",
                detail=f"参数 {name} 从 {baseline_value:.3g} 改为 "
                       f"{candidate:.3g} 后，几何完全消失",
                blocking=not is_expected_toggle_off,
            ))
        elif not changed:
            report.issues.append(SemanticIssue(
                check_type="sweep_unresponsive",
                detail=f"参数 {name} 从 {baseline_value:.3g} 改为 "
                       f"{candidate:.3g} 后，几何完全无变化",
                blocking=False,
            ))

        for derived_name, derived_role in derived_roles.items():
            if name not in derived_role.depends_on:
                continue
            before_value = baseline_effective.get(derived_name)
            after_value = perturbed_environment.values.get(derived_name)
            relationship_changed = before_value != after_value
            report.samples.append(ParameterSweepSample(
                name=derived_name,
                role="derived",
                kind="relationship",
                status="tested",
                source_value=before_value,
                candidate_value=after_value,
                effective_value=after_value,
                geometry_changed=changed,
                reason=None if relationship_changed else "derived_value_unchanged",
                depends_on=(name,),
            ))
            if not relationship_changed:
                report.issues.append(SemanticIssue(
                    check_type="sweep_relationship_unresponsive",
                    detail=f"上游参数 {name} 改变后，派生参数 {derived_name} 未变化",
                    blocking=False,
                ))

    return report


def sweep_parameters(
    project: Optional["HSFProject"],
    *,
    delta_ratio: float = DEFAULT_SWEEP_DELTA_RATIO,
    max_params: int = DEFAULT_SWEEP_MAX_PARAMS,
) -> list[SemanticIssue]:
    """Backward-compatible issue-only view of the structured sweep report."""
    return sweep_parameter_observations(
        project, delta_ratio=delta_ratio, max_params=max_params,
    ).issues


def check_mesh_health(script_3d: str, result: "Preview3DResult") -> list[SemanticIssue]:
    """Flag scripts that declare geometry commands but render nothing, or render
    geometry collapsed onto a point/line/plane."""
    issues: list[SemanticIssue] = []
    if not _script_has_geometry_command(script_3d):
        # Nothing to check: script has no recognized geometry primitive (e.g.
        # a pure control/attribute script, or one built entirely from macros
        # the MVP previewer doesn't model). Not this checker's job.
        return issues

    bbox = _scene_bbox(result.meshes)
    if bbox is None:
        issues.append(SemanticIssue(
            check_type="mesh_empty",
            detail="3d.gdl 中检测到几何命令，但预览渲染出 0 个 mesh（几何可能未实际生效）",
        ))
        return issues

    min_x, max_x, min_y, max_y, min_z, max_z = bbox
    extents = {"x": max_x - min_x, "y": max_y - min_y, "z": max_z - min_z}
    collapsed = [axis for axis, extent in extents.items() if extent <= _DEGENERATE_EPS]
    if collapsed:
        issues.append(SemanticIssue(
            check_type="mesh_degenerate",
            detail=f"整体包围盒在 {'/'.join(collapsed)} 轴上跨度为 0，几何可能坍缩成点/线/面",
        ))
    return issues


def check_bounding_box_against_dimensions(
    result: "Preview3DResult",
    dims: dict[str, float],
    tolerance: float = DEFAULT_BBOX_TOLERANCE,
) -> list[SemanticIssue]:
    """Compare the scene's combined bounding box against declared A/B/ZZYZX.

    Dimensions are compared sorted (largest-to-smallest) rather than per-axis,
    since a rotated object's local X/Y/Z does not necessarily line up with
    width/depth/height. This tolerates rotation but still catches gross
    scale mismatches (parameter not wired to geometry, wrong unit, etc.).
    """
    expected = sorted(v for v in dims.values() if v > _DEGENERATE_EPS)
    if not expected:
        return []

    bbox = _scene_bbox(result.meshes)
    if bbox is None:
        return []  # mesh_empty already reported by check_mesh_health

    min_x, max_x, min_y, max_y, min_z, max_z = bbox
    actual = sorted([max_x - min_x, max_y - min_y, max_z - min_z], reverse=True)
    expected_sorted = sorted(expected, reverse=True)

    issues: list[SemanticIssue] = []
    for i, expected_dim in enumerate(expected_sorted):
        actual_dim = actual[i] if i < len(actual) else 0.0
        low, high = expected_dim * (1 - tolerance), expected_dim * (1 + tolerance)
        if not (low <= actual_dim <= high):
            issues.append(SemanticIssue(
                check_type="bbox_mismatch",
                detail=(
                    f"包围盒第 {i + 1} 大尺寸为 {actual_dim:.3g}，"
                    f"与声明尺寸 {expected_dim:.3g}（容差 ±{tolerance:.0%}）不匹配"
                ),
            ))
    return issues


def verify_semantics(
    project: Optional["HSFProject"],
    *,
    tolerance: float = DEFAULT_BBOX_TOLERANCE,
    sweep: bool = True,
    sweep_delta_ratio: float = DEFAULT_SWEEP_DELTA_RATIO,
    sweep_max_params: int = DEFAULT_SWEEP_MAX_PARAMS,
) -> SemanticVerificationResult:
    """Preview the project's 3D script and run geometry-level sanity checks.

    Safe no-op (passed=True, no issues) when project is None. An empty 3D
    script still evaluates an explicit project contract. Never raises: a
    previewer crash is reported as a non-blocking ``preview_error`` while
    contract checks without geometry evidence remain unknown.
    """
    if project is None:
        return SemanticVerificationResult(passed=True)

    from openbrep.hsf_project import ScriptType

    script_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
    if not script_3d.strip():
        from openbrep.contracts.stair import CONTRACT_RELATIVE_PATH

        if not (project.root / CONTRACT_RELATIVE_PATH).is_file():
            return SemanticVerificationResult(passed=True)
    try:
        from openbrep.gdl_previewer import evaluate_parameter_environment
        from openbrep.workbench.project_parameter_service import parameter_values

        setup_script = project.get_script(ScriptType.MASTER) or ""
        source_params = parameter_values(project)
        evaluated = evaluate_parameter_environment(setup_script, source_params)
        params = evaluated.values
    except Exception as exc:
        from openbrep.contracts.stair import evaluate_stair_contract

        project_contract = evaluate_stair_contract(
            project,
            evaluated=None,
            preview=None,
        )
        verification = SemanticVerificationResult(
            passed=False,
            issues=[SemanticIssue(
                check_type="preview_error",
                detail=f"语义参数求值异常（不计入失败）：{exc}",
                blocking=False,
            )],
            project_contract=project_contract,
        )
        verification.passed = not verification.blocking_issues
        return verification

    preview_ok = True
    preview_issues: list[SemanticIssue] = []
    try:
        from openbrep.gdl_previewer import preview_3d_script

        result = preview_3d_script(
            script_3d,
            parameters=evaluated.runtime_values,
            unknown_command_policy="warn",
            quality="fast",
        )
    except Exception as exc:
        preview_ok = False
        result = None
        preview_issues.append(SemanticIssue(
            check_type="preview_error",
            detail=f"语义预览执行异常（不计入失败）：{exc}",
            blocking=False,
        ))

    try:
        from openbrep.contracts.stair import evaluate_stair_contract
        project_contract = evaluate_stair_contract(
            project,
            evaluated=evaluated,
            preview=result,
        )
    except Exception as exc:
        return SemanticVerificationResult(
            passed=False,
            issues=[SemanticIssue(
                check_type="project_contract_error",
                detail=f"项目合同检查异常：{exc}",
                blocking=True,
            )],
        )

    dims = {name: params[name] for name in RESERVED_PARAMS if name in params}

    issues = list(preview_issues)
    if result is not None:
        issues.extend(check_mesh_health(script_3d, result))
        issues.extend(check_bounding_box_against_dimensions(result, dims, tolerance=tolerance))
    sweep_report = None
    if sweep and preview_ok and result is not None:
        sweep_report = sweep_parameter_observations(
            project,
            delta_ratio=sweep_delta_ratio,
            max_params=sweep_max_params,
            baseline_result=result,
            baseline_effective=params,
        )
        issues.extend(sweep_report.issues)

    verification = SemanticVerificationResult(
        passed=False,
        issues=issues,
        sweep=sweep_report,
        project_contract=project_contract,
    )
    verification.passed = not verification.blocking_issues
    return verification
