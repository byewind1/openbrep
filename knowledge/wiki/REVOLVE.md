---
type: concept
status: stable
tags: [3d, geometry, revolve, rotation, lathe]
aliases: [REVOLVE, revolve, lathe, rotational sweep]
source: raw/ccgdl_dev_doc/docs/GDL_10_3D_Commands_Full.md
---

# REVOLVE

`REVOLVE` creates a 3D solid by rotating a 2D cross-section around an axis. This is the GDL equivalent of a lathe operation — ideal for balusters, vases, domes, columns with fluting, and any rotationally-symmetric geometry.

## Syntax

```gdl
REVOLVE id, angle [, contour]
```

| Param    | Type    | Description                                              |
|----------|---------|----------------------------------------------------------|
| `id`     | integer | ID of the 2D profile definition (POLY-based)             |
| `angle`  | numeric | Sweep angle in degrees (positive = CCW, negative = CW)   |
| `contour`| integer | Optional — 0: solid (default), 1: contour/surface only   |

## Profile Definition

The 2D profile must be defined in the **YZ plane** (X is the rotation axis):

```gdl
POLY2 id, n, 1,
    y1, z1, 1,
    y2, z2, 1,
    ...
```

- **X-axis** = rotation axis.
- **Y** = radius from the rotation axis.
- **Z** = height along the rotation axis.
- Points with `Y = 0` lie exactly on the rotation axis.

## Examples

### Simple hemisphere

```gdl
! Quarter-circle profile
POLY2 1, 5, 1,
    0.5, 0,   1,
    0.5, 0.2, 1,
    0.4, 0.4, 1,
    0.2, 0.5, 1,
    0,   0.5, 1

! Sweep 180 degrees
REVOLVE 1, 180
```

### Full baluster

```gdl
! Baluster profile (YZ plane)
POLY2 1, 8, 1,
    0.03, 0,    1,
    0.05, 0.05, 1,
    0.05, 0.4,  1,
    0.03, 0.5,  1,
    0.03, 0.7,  1,
    0.06, 0.75, 1,
    0.06, 0.9,  1,
    0,    0.95, 1

! Full 360-degree revolved solid
REVOLVE 1, 360
```

### Partial revolve (contour mode)

```gdl
POLY2 1, 4, 1,
    0.3, 0,   1,
    0.3, 0.5, 1,
    0,   0.5, 1,
    0,   0,   1

! 90-degree surface (not solid)
REVOLVE 1, 90, 1
```

## Edge Cases & Traps

- **Profile must be in YZ**: GDL uses X as the rotation axis. If you define the profile in XY, the revolve produces unexpected geometry.
- **Points on axis**: vertices with `Y = 0` lie on the rotation axis. Two adjacent points with `Y = 0` may produce degenerate triangles at the pole — use a single point on the axis, or offset slightly (`Y = 0.001`).
- **Full 360°**: `angle = 360` creates a closed ring. The end faces seal automatically.
- **Concave profiles**: profiles that curve inward (negative slope toward the axis) can self-intersect during revolve. Keep profiles convex or test carefully.
- **Zero radius segments**: a profile segment that lies exactly on the axis (`Y = 0` for its full length) collapses to a line and produces no surface.
- **Large angles**: `angle > 360` is valid but wasteful — the extra rotation overlaps existing geometry.
- **Negative angle**: rotates clockwise. Combine with mirror transforms if needed.

## Optimization

- For simple cylinders, use [[CYLIND]] instead (faster rendering).
- For custom prismatic shapes, use [[PRISM_]] (fewer vertices).
- REVOLVE creates many triangles — for visible detail, 24 segments per full rotation is a good default.
- Use `contour=1` for railings, trim rings, and thin shells where a full solid is unnecessary.

## Related

- [[SWEEP]] — general path-based extrusion
- [[CYLIND]] — simple cylinder (special case of revolve)
- [[PRISM_]] — straight extrusion alternative
- [[Transformation_Stack]] — positioning revolved geometry
- [[BODY_EDGE_PGON]] — underlying mesh representation
