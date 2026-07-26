"""
BS2G Converter — end-to-end Blender script → HSF project.

Two conversion modes, routed automatically:

- **primitive mode** — scripts built from ``bpy.ops.mesh.primitive_*_add``
  calls; parsed statically into IR and mapped to GDL (parser/mapper/
  generator pipeline).
- **mesh/loft mode** — scripts that build geometry via bmesh; executed
  with stub modules (mesh_capture), the loft ring structure is recovered
  (loft_detect) and emitted as a GDL RULED{2} chain (loft_gdl).

A script with no convertible geometry in either mode raises ValueError
instead of silently producing an empty project.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.importers.blender_script.generator import (
    generate_fallback_2d,
    generate_gdl_3d,
    generate_paramlist,
)
from openbrep.importers.blender_script.ir import (
    IRCondition,
    IRLoop,
    IRNode,
    IRPrimitive,
    IRScript,
)
from openbrep.importers.blender_script.parser import parse_blender_script

_BMESH_MARKER_RE = re.compile(r"\bbmesh\b")
_OBJ_NAME_RE = re.compile(r"^OBJ_NAME\s*=\s*[\"']([^\"']+)", re.MULTILINE)


def convert_blender_script(
    code: str,
    output_dir: str,
    function_name: Optional[str] = None,
    object_name: Optional[str] = None,
    scale: float = 1.0,
) -> tuple[HSFProject, IRScript]:
    """
    Convert a Blender Python script to an HSF project on disk.

    Args:
        code: Blender Python source code.
        output_dir: Parent directory for the HSF project folder.
        function_name: Target function to convert (None → first).
        object_name: HSF object name (None → derived from script).
        scale: Coordinate multiplier for mesh/loft mode (e.g. 0.001
            for millimetre-based scripts). Primitive mode is unaffected.

    Returns:
        (project, ir) — the saved HSFProject and the IRScript.
        Mesh/loft mode returns a minimal IRScript carrying the object
        name (no parameters / body).

    Raises:
        ValueError: no convertible geometry in either mode.
    """
    ir = parse_blender_script(code, target_function=function_name)

    if not _has_primitive(ir.body):
        if _has_bmesh_marker(code):
            return _convert_mesh_loft(code, output_dir, object_name, scale)
        raise ValueError(
            f"函数 {ir.function_name} 中没有可转换的几何操作"
            "（需要 bpy.ops.mesh.primitive_*_add 图元调用，"
            "或 bmesh 放样网格；模块顶层脚本只有图元管道支持函数式写法）"
        )

    name = object_name or ir.function_name
    if name.startswith("<"):
        name = "bs2g_object"

    project = HSFProject(name=name, work_dir=output_dir)

    # Parameters
    project.parameters = generate_paramlist(ir)

    # 3D script
    gdl_3d = generate_gdl_3d(ir)
    project.set_script(ScriptType.SCRIPT_3D, gdl_3d)

    # 2D fallback (LLM completion can replace this later)
    project.set_script(ScriptType.SCRIPT_2D, generate_fallback_2d())

    # Master script with derived variables
    if ir.local_vars:
        master_lines = [
            f"! Derived variables from {ir.function_name}",
        ]
        for var_name, expr in ir.local_vars.items():
            master_lines.append(f"{var_name} = {expr}")
        master_lines.append("")
        project.set_script(ScriptType.MASTER, "\n".join(master_lines) + "\n")

    project.save_to_disk()
    return project, ir


def probe_object_name(code: str, function_name: Optional[str] = None) -> str:
    """Best-effort object name for *code* without full conversion.

    Primitive mode → target function name; mesh/loft mode → the
    script's ``OBJ_NAME`` constant (or ``bs2g_mesh``).
    """
    ir = parse_blender_script(code, target_function=function_name)
    if _has_primitive(ir.body) or not _has_bmesh_marker(code):
        return ir.function_name
    return _mesh_object_name(code) or "bs2g_mesh"


# ── Mode routing helpers ────────────────────────────────────


def _has_primitive(nodes: list[IRNode]) -> bool:
    for node in nodes:
        if isinstance(node, IRPrimitive):
            return True
        if isinstance(node, IRLoop) and _has_primitive(node.body):
            return True
        if isinstance(node, IRCondition) and (
            _has_primitive(node.then_body) or _has_primitive(node.else_body)
        ):
            return True
    return False


def _has_bmesh_marker(code: str) -> bool:
    return bool(_BMESH_MARKER_RE.search(code))


def _mesh_object_name(code: str) -> Optional[str]:
    m = _OBJ_NAME_RE.search(code)
    return m.group(1) if m else None


# ── mesh/loft mode ──────────────────────────────────────────


def _convert_mesh_loft(
    code: str,
    output_dir: str,
    object_name: Optional[str],
    scale: float,
) -> tuple[HSFProject, IRScript]:
    """Convert a bmesh-based script: execute → detect loft → RULED GDL."""
    from openbrep.importers.blender_script.loft_detect import detect_loft
    from openbrep.importers.blender_script.loft_gdl import (
        generate_loft_3d,
        loft_bbox_params,
    )
    from openbrep.importers.blender_script.mesh_capture import run_mesh_capture

    mesh = run_mesh_capture(code)
    model = detect_loft(mesh)

    name = object_name or _mesh_object_name(code) or "bs2g_mesh"
    if name.startswith("<"):
        name = "bs2g_mesh"

    project = HSFProject(name=name, work_dir=output_dir)

    a, b, h = loft_bbox_params(model, scale)
    project.parameters = [
        GDLParameter("A", "Length", "Width", a, is_fixed=True),
        GDLParameter("B", "Length", "Depth", b, is_fixed=True),
        GDLParameter("ZZYZX", "Length", "Height", h, is_fixed=True),
    ]

    project.set_script(
        ScriptType.SCRIPT_3D,
        generate_loft_3d(model, scale=scale, source_name=name),
    )
    project.set_script(ScriptType.SCRIPT_2D, generate_fallback_2d())

    project.save_to_disk()
    # Shape-compatible IR: name only, no parameters/body (mesh mode
    # bakes geometry; parameter extraction is a later phase).
    return project, IRScript(function_name=name)


def convert_blender_file(
    script_path: str,
    output_dir: str,
    function_name: Optional[str] = None,
    object_name: Optional[str] = None,
) -> tuple[HSFProject, IRScript]:
    """Convenience wrapper: read a .py file and convert it."""
    code = Path(script_path).read_text(encoding="utf-8")
    return convert_blender_script(
        code,
        output_dir=output_dir,
        function_name=function_name,
        object_name=object_name,
    )
