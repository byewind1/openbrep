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
from typing import Optional, TYPE_CHECKING

from openbrep.gdl_ast import ControlBlock, GeometryCall, parse_gdl_script
from openbrep.static_checker import RESERVED_PARAMS

if TYPE_CHECKING:
    from openbrep.gdl_previewer import Preview3DResult
    from openbrep.hsf_project import HSFProject

__all__ = [
    "SemanticIssue",
    "SemanticVerificationResult",
    "check_mesh_health",
    "check_bounding_box_against_dimensions",
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


def sweep_parameters(
    project: Optional["HSFProject"],
    *,
    delta_ratio: float = DEFAULT_SWEEP_DELTA_RATIO,
    max_params: int = DEFAULT_SWEEP_MAX_PARAMS,
) -> list[SemanticIssue]:
    """Perturb each declared numeric parameter one at a time and re-preview.

    Requires a working baseline preview to sweep against; if the baseline
    itself has no mesh, mesh_health already reports that and sweeping adds no
    signal, so this returns [] early instead of piling on.
    """
    if project is None:
        return []

    from openbrep.hsf_project import ScriptType
    from openbrep.gdl_previewer import preview_3d_script
    from openbrep.workbench.project_parameter_service import parameter_values

    script_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
    if not script_3d.strip():
        return []

    setup_script = project.get_script(ScriptType.MASTER) or ""
    baseline_params = parameter_values(project)
    if not baseline_params:
        return []

    # Boolean-ness is a property of the parameter's declared type, never of
    # its current numeric value. Only genuinely Boolean params get toggled by
    # _perturb_value; a Length/RealNum sitting at exactly 1.0 must scale.
    boolean_param_names = {
        p.name.upper() for p in project.parameters if p.type_tag == "Boolean"
    }

    try:
        baseline_result = preview_3d_script(
            script_3d, parameters=baseline_params, setup_script=setup_script,
            unknown_command_policy="warn", quality="fast",
        )
    except Exception:
        return []  # can't sweep from a baseline that doesn't even preview

    baseline_bbox = _scene_bbox(baseline_result.meshes)
    if baseline_bbox is None:
        return []  # mesh_empty already covers this case

    baseline_sig = _mesh_signature(baseline_result)
    issues: list[SemanticIssue] = []

    for name in sorted(baseline_params.keys())[:max_params]:
        baseline_value = baseline_params[name]
        is_boolean = name in boolean_param_names
        perturbed_params = dict(baseline_params)
        perturbed_params[name] = _perturb_value(
            baseline_value, delta_ratio, is_boolean=is_boolean
        )

        try:
            perturbed_result = preview_3d_script(
                script_3d, parameters=perturbed_params, setup_script=setup_script,
                unknown_command_policy="warn", quality="fast",
            )
        except Exception as exc:
            issues.append(SemanticIssue(
                check_type="sweep_preview_error",
                detail=f"参数 {name} 扫描时预览异常（不计入失败）：{exc}",
                blocking=False,
            ))
            continue

        perturbed_bbox = _scene_bbox(perturbed_result.meshes)
        if perturbed_bbox is None:
            # Toggling a Boolean "hasXxx" flag off (1 → 0) is *expected* to
            # remove optional geometry — that's what the flag is for. Only
            # treat vanishing as a real bug when it wasn't an intentional
            # off-switch: scaled params, or a flag turning ON that erases
            # everything. The exemption is type-gated: a Length/RealNum whose
            # value happens to be 1.0 is not a toggle, so its vanishing is
            # still treated as a real bug.
            is_expected_toggle_off = is_boolean and baseline_value == 1.0
            issues.append(SemanticIssue(
                check_type="sweep_mesh_vanished",
                detail=f"参数 {name} 从 {baseline_value:.3g} 改为 "
                       f"{perturbed_params[name]:.3g} 后，几何完全消失",
                blocking=not is_expected_toggle_off,
            ))
            continue

        if _mesh_signature(perturbed_result) == baseline_sig:
            issues.append(SemanticIssue(
                check_type="sweep_unresponsive",
                detail=f"参数 {name} 从 {baseline_params[name]:.3g} 改为 "
                       f"{perturbed_params[name]:.3g} 后，几何完全无变化",
                blocking=False,
            ))

    return issues


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

    Safe no-op (passed=True, no issues) when project is None or has no 3D
    script. Never raises: a previewer crash is reported as a non-blocking
    `preview_error` issue rather than propagating.
    """
    if project is None:
        return SemanticVerificationResult(passed=True)

    from openbrep.hsf_project import ScriptType

    script_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
    if not script_3d.strip():
        return SemanticVerificationResult(passed=True)

    try:
        from openbrep.gdl_previewer import preview_3d_script
        from openbrep.workbench.project_parameter_service import parameter_values

        setup_script = project.get_script(ScriptType.MASTER) or ""
        params = parameter_values(project)
        result = preview_3d_script(
            script_3d,
            parameters=params,
            setup_script=setup_script,
            unknown_command_policy="warn",
            quality="fast",
        )
    except Exception as exc:
        return SemanticVerificationResult(
            passed=True,
            issues=[SemanticIssue(
                check_type="preview_error",
                detail=f"语义预览执行异常（不计入失败）：{exc}",
                blocking=False,
            )],
        )

    dims = {name: params[name] for name in RESERVED_PARAMS if name in params}

    issues = check_mesh_health(script_3d, result)
    issues.extend(check_bounding_box_against_dimensions(result, dims, tolerance=tolerance))
    if sweep:
        issues.extend(sweep_parameters(
            project, delta_ratio=sweep_delta_ratio, max_params=sweep_max_params,
        ))

    passed = not any(issue.blocking for issue in issues)
    return SemanticVerificationResult(passed=passed, issues=issues)
