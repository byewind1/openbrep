"""
BS2G Mapper — IR nodes → GDL command fragments.

Pure mapping rules with no LLM.  Each primitive / transform kind
has a deterministic GDL equivalent.  The generator (generator.py)
is responsible for assembling these fragments into a complete,
stack-balanced 3d.gdl script.
"""

from __future__ import annotations

import ast

from openbrep.importers.blender_script.ir import (
    IRPrimitive,
    IRTransform,
)


# ── Primitive → GDL geometry command ────────────────────────
#
# Blender primitives are centred at the origin; GDL primitives
# grow from the current origin towards +X/+Y/+Z.  The generator
# wraps each primitive in an ADD/DEL pair to centre it.

def map_primitive(node: IRPrimitive) -> str:
    """Return the GDL geometry command for a primitive (no transform)."""
    kind = node.kind
    args = node.args

    if kind == "cube":
        size = args.get("size", "1")
        return f"BLOCK {size}, {size}, {size}"

    if kind == "cylinder":
        depth = args.get("depth", "1")
        radius = args.get("radius", "1")
        return f"CYLIND {depth}, {radius}"

    if kind == "sphere":
        radius = args.get("radius", "1")
        return f"SPHERE {radius}"

    if kind == "cone":
        depth = args.get("depth", "1")
        r1 = args.get("radius1", "1")
        r2 = args.get("radius2", "0")
        return f"CONE {depth}, {r1}, {r2}, 90, 90"

    return f"! [BS2G] Unknown primitive: {kind}"


def primitive_half_extents(node: IRPrimitive) -> tuple[str, str, str]:
    """
    Return half-extent expressions (dx, dy, dz) for centring a
    primitive at the origin.

    Blender primitives are centred; GDL primitives grow from origin.
    We offset by -half-extent before drawing.  Purely numeric args are
    computed directly (``-(1)/2`` becomes ``-0.5``); only parametric
    expressions keep the parenthesised form (``-(width)/2``).
    """
    kind = node.kind
    args = node.args

    if kind == "cube":
        s = args.get("size", "1")
        half = _half_extent(s)
        return half, half, half

    if kind == "cylinder":
        depth = args.get("depth", "1")
        r = _wrap_extent(args.get("radius", "1"))
        return r, r, _half_extent(depth)

    if kind == "sphere":
        r = _wrap_extent(args.get("radius", "1"))
        return r, r, r

    if kind == "cone":
        depth = args.get("depth", "1")
        r = _wrap_extent(args.get("radius1", "1"))
        return r, r, _half_extent(depth)

    return "0", "0", "0"


# ── Transform → GDL push/pop commands ──────────────────────

def map_transform(node: IRTransform) -> tuple[str, str]:
    """
    Return (push_command, pop_command) for a transform.

    Every push has a matching DEL 1 pop so the generator can
    guarantee stack balance.
    """
    x, y, z = node.components

    if node.kind == "translate":
        return f"ADD {x}, {y}, {z}", "DEL 1"

    if node.kind == "scale":
        return f"MUL {x}, {y}, {z}", "DEL 1"

    if node.kind == "rotate":
        # Angle components arrive already in degrees — the unit
        # conversion (radians → degrees) is done parser-side.
        # Decompose (rx, ry, rz): for now emit ROTZ with the z-component;
        # full 3-axis rotation decomposition is a future enhancement.
        return f"ROTZ {z}", "DEL 1"

    return f"! [BS2G] Unknown transform: {node.kind}", ""


def _eval_numeric(expr: str) -> float | None:
    """Evaluate a purely numeric expression (constants, ``+ - * /``, parens).

    Returns ``None`` when the expression contains names or any
    unsupported construct.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def ev(node: ast.AST) -> float | None:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if v is None:
                return None
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return v
            return None
        if isinstance(node, ast.BinOp):
            l, r = ev(node.left), ev(node.right)
            if l is None or r is None:
                return None
            if isinstance(node.op, ast.Add):
                return l + r
            if isinstance(node.op, ast.Sub):
                return l - r
            if isinstance(node.op, ast.Mult):
                return l * r
            if isinstance(node.op, ast.Div):
                if r == 0:
                    return None
                return l / r
            return None
        return None

    return ev(tree)


def _fmt_num(value: float) -> str:
    """Format a number, dropping a trailing ``.0`` when integral."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def _wrap_extent(expr: str) -> str:
    """Full extent: numeric literal → computed number, else ``(expr)``."""
    num = _eval_numeric(expr)
    if num is not None:
        return _fmt_num(num)
    return f"({expr})"


def _half_extent(expr: str) -> str:
    """Half extent: numeric literal → computed number, else ``(expr)/2``."""
    num = _eval_numeric(expr)
    if num is not None:
        return _fmt_num(num / 2)
    return f"({expr})/2"
