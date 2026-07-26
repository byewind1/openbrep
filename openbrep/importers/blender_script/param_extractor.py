"""
BS2G parameter type inference.

Maps Python function parameters to GDL paramlist types using
name-based heuristics and default-value inspection.
"""

from __future__ import annotations

import ast

from openbrep.importers.blender_script.ir import IRParameter

# ── Keyword lists for name-based inference ──────────────────

LENGTH_KEYWORDS = [
    "width", "height", "depth", "length", "thickness",
    "size", "radius", "diameter", "spacing", "offset",
    "gap", "margin", "padding", "inset", "overhang",
    "宽", "高", "深", "厚", "长", "半径", "间距",
]

ANGLE_KEYWORDS = [
    "angle", "rotation", "rot", "tilt", "degree", "slope",
    "角度", "旋转", "倾斜",
]

COUNT_KEYWORDS = [
    "count", "num", "number", "segments", "divisions",
    "steps", "levels", "rows", "cols", "columns",
    "数量", "个数", "层数", "段数",
]

BOOL_KEYWORDS = [
    "has_", "show_", "enable_", "is_", "use_", "with_",
    "include_", "exclude_", "toggle_",
]


def infer_gdl_type(param_name: str, default_node: ast.expr | None) -> tuple[str, str]:
    """
    Infer GDL type and unit from a Python parameter.

    Returns (gdl_type, inferred_unit).
    """
    name_lower = param_name.lower()

    # Bool by name prefix
    if any(name_lower.startswith(kw) for kw in BOOL_KEYWORDS):
        return "Boolean", ""

    # Bool by default value
    if default_node is not None:
        if isinstance(default_node, ast.Constant) and isinstance(default_node.value, bool):
            return "Boolean", ""

    # Angle by name
    if any(kw in name_lower for kw in ANGLE_KEYWORDS):
        return "Angle", "deg"

    # Integer count by name + int default
    if any(kw in name_lower for kw in COUNT_KEYWORDS):
        return "Integer", ""

    # Length by name
    if any(kw in name_lower for kw in LENGTH_KEYWORDS):
        return "Length", "m"

    # Numeric fallback
    if default_node is not None and isinstance(default_node, ast.Constant):
        val = default_node.value
        if isinstance(val, bool):
            return "Boolean", ""
        if isinstance(val, int):
            return "Integer", ""
        if isinstance(val, float):
            return "RealNum", ""

    return "String", ""


def extract_parameters(func_def: ast.FunctionDef) -> list[IRParameter]:
    """
    Extract IRParameter list from a FunctionDef's arguments.

    Only parameters with defaults are included (Blender scripts
    typically use defaults for all user-facing knobs).
    """
    args = func_def.args
    params: list[IRParameter] = []

    # defaults align to the *last* N positional args
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    default_offset = num_args - num_defaults

    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue

        default_idx = i - default_offset
        default_node: ast.expr | None = None
        default_str = ""

        if default_idx >= 0:
            default_node = args.defaults[default_idx]
            default_str = ast.unparse(default_node)

        gdl_type, unit = infer_gdl_type(arg.arg, default_node)

        # Python type annotation (if present)
        python_type = "float"
        if arg.annotation is not None:
            python_type = ast.unparse(arg.annotation)
        elif default_node is not None and isinstance(default_node, ast.Constant):
            val = default_node.value
            if isinstance(val, bool):
                python_type = "bool"
            elif isinstance(val, int):
                python_type = "int"
            elif isinstance(val, str):
                python_type = "str"

        params.append(IRParameter(
            name=arg.arg,
            python_type=python_type,
            default_value=default_str,
            gdl_type=gdl_type,
            inferred_unit=unit,
        ))

    return params
