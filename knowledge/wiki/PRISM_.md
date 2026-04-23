---
type: concept
status: stable
tags: [prism, 3d, geometry, extrusion, polygon, polyhedron]
aliases: [PRISM, prism command, gdl prism]
source: raw/ccgdl_dev_doc/docs/GDL_02_Shapes.md
---

# PRISM_

`PRISM_` is the primary GDL command for creating prismatic (extruded) 3D bodies from a 2D polygon profile. It is the most versatile and commonly used 3D geometry command in GDL.

## Why PRISM_?

Building geometry is rarely just boxes and cylinders. Walls have openings, columns have profiles, roof eaves have complex cross-sections. `PRISM_` handles all of these: you define a 2D polygon (with holes if needed), extrude it to a height, and control edge visibility per segment.

## Syntax

```gdl
PRISM_ n, h, x1, y1, s1, ..., xn, yn, sn
```

| Param | Type   | Description                                      |
|-------|--------|--------------------------------------------------|
| `n`   | int    | Number of vertices                                |
| `h`   | length | Extrusion height (Z direction)                    |
| `x, y`| length | Vertex coordinates (2D polygon in XY plane)       |
| `s`   | int    | Status code per vertex (edge visibility + holes)  |

### Status Codes

The `s` parameter controls both edge visibility and contour nesting:

| Code | Meaning                              |
|------|--------------------------------------|
| 1    | Bottom edge visible                   |
| 2    | Vertical edge visible                 |
| 4    | Top edge visible                      |
| 8    | Side face visible                     |
| 15   | All edges + face visible (most common) |
| 64   | Visible only in contour (curved surfaces) |
| -1   | Close contour / start new hole         |

> Use `15` for every vertex in a simple solid polygon. Use `-1` to close a contour and start a hole.

## Examples

### Basic triangular prism

```gdl
! Triangle with all edges visible
PRISM_ 3, 2.0,
    0,   0,    15,
    1.0, 0,    15,
    0.5, 0.866, 15
```

### Rectangle with hole

```gdl
PRISM_ 8, 1.0,
    ! Outer contour (clockwise) — all edges visible
    0,   0,   15,
    2.0, 0,   15,
    2.0, 1.0, 15,
    0,   1.0, 15,
    0,   0,   -1,    ! close outer, begin inner
    ! Inner contour / hole (counter-clockwise)
    0.4, 0.4, 15,
    0.4, 0.6, 15,
    0.6, 0.6, 15,
    0.6, 0.4, 15
```

### With varying edge visibility

```gdl
! Only vertical edges visible (status=2) — hidden-line style
PRISM_ 4, 1.5,
    0,   0,   2,
    2.0, 0,   2,
    2.0, 1.0, 2,
    0,   1.0, 2
```

## Edge Cases & Traps

- **Zero height** (`h = 0`): degenerates to a 2D polygon (no volume).
- **Self-intersecting polygon**: undefined behavior; ArchiCAD may reject it.
- **Vertex count limit**: practical max ~10,000 vertices (depends on polygon complexity).
- **Hole winding**: outer contour clockwise, inner counter-clockwise. Wrong winding flips the boolean operation.
- **Status `-1` is required** between contours; omitting it merges vertices into a single invalid polygon.
- **Coordinate system**: `PRISM_` works in the current [[Transformation_Stack]] context. `ADD`/`DEL` and `ROT` affect where the prism appears.

## Related

- [[BLOCK]] — simpler box primitive (use when the cross-section is a rectangle)
- [[BODY_EDGE_PGON]] — lower-level mesh construction for non-prismatic geometry
- [[Transformation_Stack]] — positioning prisms in 3D space
