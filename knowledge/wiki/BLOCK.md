---
type: concept
status: stable
tags: [block, box, cube, 3d, geometry, primitive]
aliases: [BLOCK, brick, BRICK, box command, gdl block]
source: raw/ccgdl_dev_doc/docs/GDL_10_3D_Commands_Full.md
---

# BLOCK

`BLOCK` creates an axis-aligned rectangular box (cuboid) in 3D space. Its synonym `BRICK` is identical in every respect.

## Why BLOCK?

When your geometry is a simple rectangle extruded along the Z axis — a wall segment, a table top, a door panel — `BLOCK` is the most efficient choice. It requires no vertex plumbing or status codes. For non-rectangular footprints, use [[PRISM_]] instead.

## Syntax

```gdl
BLOCK a, b, c
BRICK a, b, c   ! identical
```

| Param | Type   | Description                    |
|-------|--------|--------------------------------|
| `a`   | length | X dimension                    |
| `b`   | length | Y dimension                    |
| `c`   | length | Z dimension (height)           |

The box is placed with one corner at the origin `(0, 0, 0)` and extends to `(a, b, c)` in positive X, Y, Z.

## Examples

### Simple cube

```gdl
BLOCK 1, 1, 1   ! a 1×1×1 cube at the origin
```

### Slab

```gdl
BLOCK 6.0, 4.0, 0.2   ! a 6m × 4m slab, 20cm thick
```

### Column (square)

```gdl
ADD 2.0, 3.0, 0       ! position first
BLOCK 0.4, 0.4, 3.0   ! 40cm square column, 3m tall
DEL 1
```

### Using BRICK

```gdl
BRICK 1.5, 0.8, 2.0   ! same as BLOCK
```

## Edge Cases & Traps

- **Zero dimension**: if any of `a`, `b`, `c` is zero, the box degenerates (face, edge, or point). ArchiCAD will render it but it may be invisible or cause selection issues.
- **All dimensions zero**: no geometry produced.
- **Negative dimensions**: treats as absolute value (GDL normalizes to positive).
- **Cannot be hollow**: use [[PRISM_]] with holes or boolean operations with [[ADD_DEL]] for hollow boxes.
- **Always axis-aligned**: rotated boxes need `ROT` in the [[Transformation_Stack]] before `BLOCK`.
- **Performance**: `BLOCK` is the fastest 3D primitive in ArchiCAD — prefer it over [[PRISM_]] when the shape is rectangular.

## Related

- [[PRISM_]] — arbitrary polygon extrusion (for non-rectangular footprints)
- [[Transformation_Stack]] — positioning and orienting blocks in 3D
- [[BODY_EDGE_PGON]] — mesh-level primitives for complex subdivision
