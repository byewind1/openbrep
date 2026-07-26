"""
BS2G AST Parser — Blender Python → IR.

Parses a Blender Python script using the stdlib ``ast`` module and
produces an :class:`IRScript`.  Unrecognised statements degrade to
:class:`IRUnsupported` nodes; the parser never raises on unsupported
input.

Supported patterns (v1):
  - ``bpy.ops.mesh.primitive_{cube,cylinder,uv_sphere,cone}_add()``
  - ``obj.location = (x, y, z)``
  - ``obj.scale = (x, y, z)``
  - ``obj.rotation_euler = (rx, ry, rz)``
  - ``for i in range(n):``
  - ``if cond: / else:``
  - simple variable assignments
"""

from __future__ import annotations

import ast
from typing import Optional

from openbrep.importers.blender_script.ir import (
    IRAssignment,
    IRCondition,
    IRLoop,
    IRNode,
    IRPrimitive,
    IRScript,
    IRTransform,
    IRUnsupported,
)
from openbrep.importers.blender_script.param_extractor import extract_parameters
from openbrep.importers.blender_script.warnings import WarningCollector

# ── Primitive name mapping ──────────────────────────────────

_PRIMITIVE_KINDS = {
    "primitive_cube_add": "cube",
    "primitive_cylinder_add": "cylinder",
    "primitive_uv_sphere_add": "sphere",
    "primitive_cone_add": "cone",
}

# ── Transform attribute mapping ─────────────────────────────

_TRANSFORM_ATTRS = {
    "location": "translate",
    "scale": "scale",
    "rotation_euler": "rotate",
}


def parse_blender_script(
    code: str,
    target_function: Optional[str] = None,
) -> IRScript:
    """
    Parse a Blender Python script into an IRScript.

    Args:
        code: Blender Python source.
        target_function: Name of the function to convert.
            ``None`` → use the first function definition found.

    Returns:
        IRScript with parameters, body nodes, and warnings.
        Never raises on unsupported syntax — degrades to IRUnsupported.
    """
    warnings = WarningCollector()

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return IRScript(
            function_name="<syntax_error>",
            warnings=[IRUnsupported(
                operation="ast.parse",
                reason=f"Python syntax error: {exc}",
                line=exc.lineno or 0,
                source_line=exc.text or "",
            )],
        )

    func_def = _find_function(tree, target_function)
    if func_def is None:
        name = target_function or "<first>"
        return IRScript(
            function_name=name,
            warnings=[IRUnsupported(
                operation="find_function",
                reason=f"No function definition found (target={name!r})",
                line=0,
            )],
        )

    params = extract_parameters(func_def)
    body = _parse_body(func_def.body, code, warnings)

    # Collect local variable assignments for the local_vars dict
    local_vars: dict[str, str] = {}
    _collect_locals(body, local_vars)

    return IRScript(
        function_name=func_def.name,
        parameters=params,
        body=body,
        local_vars=local_vars,
        warnings=warnings.warnings,
    )


# ── Internal helpers ────────────────────────────────────────


def _find_function(
    tree: ast.Module,
    target: Optional[str],
) -> Optional[ast.FunctionDef]:
    """Find the target function (or first function) in the module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if target is None or node.name == target:
                return node
    return None


def _parse_body(
    stmts: list[ast.stmt],
    source: str,
    warnings: WarningCollector,
) -> list[IRNode]:
    """Parse a list of statements into IR nodes."""
    nodes: list[IRNode] = []
    for stmt in stmts:
        node = _parse_stmt(stmt, source, warnings)
        if node is not None:
            nodes.append(node)
    return nodes


def _parse_stmt(
    stmt: ast.stmt,
    source: str,
    warnings: WarningCollector,
) -> Optional[IRNode]:
    """Parse a single statement into an IR node."""

    # ── Expression statement (function calls) ───────────────
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _parse_call_stmt(stmt.value, source, warnings, stmt.lineno)

    # ── Assignment ──────────────────────────────────────────
    if isinstance(stmt, ast.Assign):
        return _parse_assign(stmt, source, warnings)

    # ── For loop ────────────────────────────────────────────
    if isinstance(stmt, ast.For):
        return _parse_for(stmt, source, warnings)

    # ── If / else ───────────────────────────────────────────
    if isinstance(stmt, ast.If):
        return _parse_if(stmt, source, warnings)

    # ── Fallback: unsupported ───────────────────────────────
    src_line = _get_source_line(source, stmt.lineno)
    return warnings.add(
        operation=type(stmt).__name__,
        reason=f"Unsupported statement type: {type(stmt).__name__}",
        line=stmt.lineno,
        source_line=src_line,
    )


def _parse_call_stmt(
    call: ast.Call,
    source: str,
    warnings: WarningCollector,
    lineno: int,
) -> IRNode:
    """Parse a call expression statement."""
    dotted = _dotted_name(call.func)

    # bpy.ops.mesh.primitive_*_add(...)
    if dotted:
        parts = dotted.split(".")
        func_name = parts[-1]

        if func_name in _PRIMITIVE_KINDS:
            return _parse_primitive(call, _PRIMITIVE_KINDS[func_name], lineno)

        # bpy.ops.object.modifier_add(...) and other bpy.ops calls
        if "bpy" in parts and "ops" in parts:
            src_line = _get_source_line(source, lineno)
            op_name = ".".join(parts[parts.index("ops") + 1:])
            return warnings.add(
                operation=f"bpy.ops.{op_name}",
                reason="Unsupported bpy.ops call — cannot map to GDL",
                line=lineno,
                source_line=src_line,
            )

    # Unrecognised call
    src_line = _get_source_line(source, lineno)
    return warnings.add(
        operation=ast.unparse(call.func),
        reason="Unrecognised function call",
        line=lineno,
        source_line=src_line,
    )


def _parse_primitive(
    call: ast.Call,
    kind: str,
    lineno: int,
) -> IRPrimitive:
    """Extract primitive kind and keyword arguments."""
    args: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg is not None:
            args[kw.arg] = ast.unparse(kw.value)
    # Positional args (rare but possible)
    for i, pos in enumerate(call.args):
        args[f"_pos{i}"] = ast.unparse(pos)
    return IRPrimitive(kind=kind, args=args, line=lineno)


def _parse_assign(
    stmt: ast.Assign,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse an assignment statement."""
    if len(stmt.targets) != 1:
        src_line = _get_source_line(source, stmt.lineno)
        return warnings.add(
            operation="multi_target_assign",
            reason="Multiple assignment targets not supported",
            line=stmt.lineno,
            source_line=src_line,
        )

    target = stmt.targets[0]

    # obj.location = (...) / obj.scale = (...) / obj.rotation_euler = (...)
    if isinstance(target, ast.Attribute) and target.attr in _TRANSFORM_ATTRS:
        kind = _TRANSFORM_ATTRS[target.attr]
        value = ast.unparse(stmt.value)
        return IRTransform(kind=kind, axis=None, value=value, line=stmt.lineno)

    # obj.attr = value (non-transform attribute)
    if isinstance(target, ast.Attribute):
        src_line = _get_source_line(source, stmt.lineno)
        return warnings.add(
            operation=f"attr_assign({target.attr})",
            reason=f"Unsupported attribute assignment: {target.attr}",
            line=stmt.lineno,
            source_line=src_line,
        )

    # Simple variable: name = expr
    if isinstance(target, ast.Name):
        return IRAssignment(
            name=target.id,
            value=ast.unparse(stmt.value),
            line=stmt.lineno,
        )

    # Subscript or other complex target
    src_line = _get_source_line(source, stmt.lineno)
    return warnings.add(
        operation="complex_assign",
        reason=f"Unsupported assignment target: {ast.unparse(target)}",
        line=stmt.lineno,
        source_line=src_line,
    )


def _parse_for(
    stmt: ast.For,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse a for loop."""
    # Loop variable
    if isinstance(stmt.target, ast.Name):
        var_name = stmt.target.id
    else:
        var_name = ast.unparse(stmt.target)

    # range(n) / range(start, end) / range(start, end, step)
    start, end = _extract_range(stmt.iter)

    body = _parse_body(stmt.body, source, warnings)

    return IRLoop(
        var_name=var_name,
        start=start,
        end=end,
        body=body,
        line=stmt.lineno,
    )


def _extract_range(iter_node: ast.expr) -> tuple[str, str]:
    """Extract (start, end) from a range() call or other iterable."""
    if isinstance(iter_node, ast.Call):
        func_name = _dotted_name(iter_node.func)
        if func_name == "range":
            args = iter_node.args
            if len(args) == 1:
                return "1", ast.unparse(args[0])
            if len(args) >= 2:
                return ast.unparse(args[0]), ast.unparse(args[1])

    # Fallback: use the expression as-is for the end
    return "1", ast.unparse(iter_node)


def _parse_if(
    stmt: ast.If,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse an if/else block."""
    condition = ast.unparse(stmt.test)
    then_body = _parse_body(stmt.body, source, warnings)
    else_body = _parse_body(stmt.orelse, source, warnings) if stmt.orelse else []

    return IRCondition(
        condition=condition,
        then_body=then_body,
        else_body=else_body,
        line=stmt.lineno,
    )


# ── Utilities ───────────────────────────────────────────────


def _dotted_name(node: ast.expr) -> Optional[str]:
    """Reconstruct a dotted name like ``bpy.ops.mesh.primitive_cube_add``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _get_source_line(source: str, lineno: int) -> str:
    """Get a source line by 1-based line number."""
    lines = source.splitlines()
    if 0 < lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""


def _collect_locals(nodes: list[IRNode], out: dict[str, str]) -> None:
    """Recursively collect IRAssignment nodes into a dict."""
    for node in nodes:
        if isinstance(node, IRAssignment):
            out[node.name] = node.value
        elif isinstance(node, IRLoop):
            _collect_locals(node.body, out)
        elif isinstance(node, IRCondition):
            _collect_locals(node.then_body, out)
            _collect_locals(node.else_body, out)
