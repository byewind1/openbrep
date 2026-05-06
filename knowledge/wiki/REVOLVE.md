---
type: concept
status: stable
tags: [3d, geometry, revolve, rotation, lathe]
aliases: [REVOLVE, revolve, lathe, rotational sweep]
source: official:gdl.graphisoft.com/reference-guide/index
---

# REVOLVE

`REVOLVE` creates a 3D surface or body by rotating a polyline defined in the x-y plane around the x-axis. This is the GDL equivalent of a lathe operation — useful for balusters, vases, domes, turned columns, and rotationally-symmetric details.

OpenBrep should treat `REVOLVE` as advanced but acceptable when the requested object is clearly rotational: vase, turned leg, knob, dome, baluster, circular trim, or similar.

## Syntax

```gdl
REVOLVE n, alpha, mask, x1, y1, s1, ..., xn, yn, sn
REVOLVE{2} n, alphaOffset, alpha, mask, sideMat,
        x1, y1, s1, mat1, ..., xn, yn, sn, matn
```

| Param   | Type    | Description                                      |
|---------|---------|--------------------------------------------------|
| `n`     | integer | Number of profile polyline nodes                 |
| `alpha` | angle   | Rotation angle in degrees                        |
| `mask`  | integer | Controls closing polygons and edge visibility    |
| `x, y`  | length  | Profile node coordinates in the x-y plane        |
| `s`     | integer | Status of latitudinal arcs from the node         |

## Profile Definition

The profile is written inline in the `REVOLVE` command. It is a polyline in the x-y plane, rotated around the x-axis. The official basic `REVOLVE` profile cannot contain holes.

```gdl
REVOLVE 4, 360, 63,
    0.00, 0.00, 0,
    0.05, 0.00, 0,
    0.04, 0.40, 0,
    0.00, 0.40, 0
```

- **X-axis** = rotation axis.
- **Y** = radius from the rotation axis in the profile plane.
- Points with `Y = 0` lie on the rotation axis.

## Examples

### Simple hemisphere

```gdl
REVOLVE 5, 180, 63,
    0.0, 0.0, 0,
    0.2, 0.5, 0,
    0.4, 0.4, 0,
    0.5, 0.2, 0,
    0.5, 0.0, 0
```

### Full baluster

```gdl
REVOLVE 8, 360, 63,
    0.00, 0.00, 0,
    0.03, 0.05, 0,
    0.05, 0.40, 0,
    0.03, 0.50, 0,
    0.03, 0.70, 0,
    0.06, 0.75, 0,
    0.06, 0.90, 0,
    0.00, 0.95, 0
```

### Partial revolve (contour mode)

```gdl
REVOLVE 4, 90, 63,
    0.0, 0.0, 0,
    0.3, 0.0, 0,
    0.3, 0.5, 0,
    0.0, 0.5, 0
```

## Edge Cases & Traps

- **Advanced non-default command**: require a rotational-shape reason before using `REVOLVE`; use [[CYLIND]] for simple cylinders.
- **Profile is inline**: do not generate `POLY2 id` + `REVOLVE id, angle`; that is not the official `REVOLVE` syntax.
- **Points on axis**: vertices with `Y = 0` lie on the rotation axis. Two adjacent points with `Y = 0` may produce degenerate triangles at the pole — use a single point on the axis, or offset slightly (`Y = 0.001`).
- **Full 360°**: `angle = 360` creates a closed ring. The end faces seal automatically.
- **Concave profiles**: profiles that curve inward (negative slope toward the axis) can self-intersect during revolve. Keep profiles convex or test carefully.
- **Zero radius segments**: a profile segment that lies exactly on the axis (`Y = 0` for its full length) collapses to a line and produces no surface.
- **Large angles**: `angle > 360` is valid but wasteful — the extra rotation overlaps existing geometry.
- **Holes**: the basic `REVOLVE` profile cannot contain holes. Use advanced variants only when the syntax is explicitly supported.

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
