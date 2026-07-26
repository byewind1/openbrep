"""
BS2G Mapper — IR nodes → GDL command fragments.

Pure mapping rules with no LLM.  Each primitive / transform kind
has a deterministic GDL equivalent.  The generator (generator.py)
is responsible for assembling these fragments into a complete,
stack-balanced 3d.gdl script.
"""

from __future__ import annotations

import math

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
    We offset by -half-extent before drawing.
    """
    kind = node.kind
    args = node.args

    if kind == "cube":
        s = args.get("size", "1")
        half = f"({s})/2"
        return half, half, half

    if kind == "cylinder":
        depth = args.get("depth", "1")
        radius = args.get("radius", "1")
        return f"({radius})", f"({radius})", f"({depth})/2"

    if kind == "sphere":
        radius = args.get("radius", "1")
        return f"({radius})", f"({radius})", f"({radius})"

    if kind == "cone":
        depth = args.get("depth", "1")
        r1 = args.get("radius1", "1")
        return f"({r1})", f"({r1})", f"({depth})/2"

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
        # Blender uses radians; GDL uses degrees.
        # Decompose (rx, ry, rz) into individual ROTX/ROTY/ROTZ.
        # For now emit ROTZ with the z-component; full 3-axis
        # rotation decomposition is a future enhancement.
        try:
            rad = float(z)
            deg = math.degrees(rad)
            return f"ROTZ {deg:.6g}", "DEL 1"
        except (ValueError, TypeError):
            pass
        return f"ROTZ ({z}) * 180 / PI", "DEL 1"

    return f"! [BS2G] Unknown transform: {node.kind}", ""
