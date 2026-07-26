"""
BS2G Converter — end-to-end Blender script → HSF project.

Ties together the parser, mapper, and generator to produce a
complete HSF project directory from a Blender Python script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.importers.blender_script.generator import (
    generate_fallback_2d,
    generate_gdl_3d,
    generate_paramlist,
)
from openbrep.importers.blender_script.ir import IRScript
from openbrep.importers.blender_script.parser import parse_blender_script


def convert_blender_script(
    code: str,
    output_dir: str,
    function_name: Optional[str] = None,
    object_name: Optional[str] = None,
) -> tuple[HSFProject, IRScript]:
    """
    Convert a Blender Python script to an HSF project on disk.

    Args:
        code: Blender Python source code.
        output_dir: Parent directory for the HSF project folder.
        function_name: Target function to convert (None → first).
        object_name: HSF object name (None → derived from function).

    Returns:
        (project, ir) — the saved HSFProject and the IRScript.
    """
    ir = parse_blender_script(code, target_function=function_name)

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
