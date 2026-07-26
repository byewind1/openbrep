"""
BS2G loft detection — recover the ring structure from a captured mesh.

A lofted mesh (airfoil blade, hull, duct) consists of equal-size planar
vertex rings connected by quad strips, optionally closed by triangle-fan
end caps around a center vertex.  This module recovers that structure
from raw ``CapturedMesh`` data; anything else is rejected with a clear
error instead of producing wrong geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openbrep.importers.blender_script.mesh_capture import CapturedMesh


class LoftDetectError(Exception):
    """The captured mesh is not a recognisable loft structure."""


@dataclass
class LoftModel:
    """Planar rings sorted along the loft axis."""

    rings: list[list[tuple[float, float, float]]] = field(default_factory=list)
    axis: int = 2  # 0=x, 1=y, 2=z


def detect_loft(mesh: CapturedMesh) -> LoftModel:
    """Recover loft rings from *mesh*.

    Raises:
        LoftDetectError: the mesh is not a loft (unequal/non-planar
            rings, non-adjacent quad strips, …).
    """
    if not mesh.verts or not mesh.faces:
        raise LoftDetectError("网格为空")

    ring_vert_ids = _exclude_cap_centers(mesh)
    if len(ring_vert_ids) < 6:
        raise LoftDetectError("有效顶点过少，不是放样结构")

    groups: list[tuple[float, list[int]]] | None = None
    axis_used = 2
    for axis in (2, 1, 0):
        groups = _group_by_axis(mesh.verts, ring_vert_ids, axis)
        if groups is not None:
            axis_used = axis
            break
    if groups is None:
        raise LoftDetectError(
            "找不到等点数的平面截面环（沿 X/Y/Z 任一轴），不是放样结构"
        )

    _validate_quads(mesh, groups)

    return LoftModel(
        rings=[
            [mesh.verts[i] for i in group_ids]
            for _coord, group_ids in groups
        ],
        axis=axis_used,
    )


# ── Cap-center exclusion ────────────────────────────────────


def _exclude_cap_centers(mesh: CapturedMesh) -> list[int]:
    """Return vertex ids that belong to rings (not end-cap centers).

    A cap center (e.g. the blade's root/tip fan center) is referenced
    only by triangle faces, and by many of them; ring vertices are
    referenced by at least one quad.
    """
    quad_refs: set[int] = set()
    tri_count: dict[int, int] = {}
    for face in mesh.faces:
        if len(face) >= 4:
            quad_refs.update(face)
        else:
            for i in face:
                tri_count[i] = tri_count.get(i, 0) + 1
    cap_centers = {
        i for i, count in tri_count.items() if count >= 4 and i not in quad_refs
    }
    return [i for i in range(len(mesh.verts)) if i not in cap_centers]


# ── Axis grouping ───────────────────────────────────────────


def _group_by_axis(
    verts: list[tuple[float, float, float]],
    ids: list[int],
    axis: int,
) -> list[tuple[float, list[int]]] | None:
    """Cluster *ids* into planar groups perpendicular to *axis*.

    Returns sorted (mean_coord, ids) groups when ≥ 2 groups of equal
    size (≥ 3) exist, else ``None``.
    """
    coords = [verts[i][axis] for i in ids]
    span = max(coords) - min(coords)
    tol = max(1e-9, span * 1e-6)

    groups: list[tuple[float, list[int]]] = []
    for i in sorted(ids, key=lambda j: verts[j][axis]):
        c = verts[i][axis]
        if groups and abs(c - groups[-1][0]) <= tol:
            groups[-1][1].append(i)
        else:
            groups.append((c, [i]))

    if len(groups) < 2:
        return None
    size = len(groups[0][1])
    if size < 3 or any(len(g[1]) != size for g in groups):
        return None
    # Replace anchors with group means for robustness
    return [
        (sum(verts[i][axis] for i in g[1]) / len(g[1]), g[1])
        for g in groups
    ]


# ── Quad-strip validation ───────────────────────────────────


def _validate_quads(
    mesh: CapturedMesh,
    groups: list[tuple[float, list[int]]],
) -> None:
    """Every quad must connect 2+2 vertices of adjacent rings."""
    ring_of_vert: dict[int, int] = {}
    for k, (_coord, ids) in enumerate(groups):
        for i in ids:
            ring_of_vert[i] = k

    for face in mesh.faces:
        if len(face) != 4:
            continue
        rings_in_face = [ring_of_vert[i] for i in face if i in ring_of_vert]
        distinct = sorted(set(rings_in_face))
        if len(distinct) == 1:
            raise LoftDetectError(
                "存在不跨环的四边面（同一环内成面），不是放样蒙皮"
            )
        if (
            len(rings_in_face) != 4
            or len(distinct) != 2
            or distinct[1] - distinct[0] != 1
        ):
            raise LoftDetectError(
                "四边面未连接相邻两环（2+2），不是放样蒙皮"
            )
