---
type: concept
status: stable
tags: [3d, geometry, cylinder, cone, primitives]
aliases: [CYLIND, cylinder, cone, tube, pipe, cyl]
source: raw/ccgdl_dev_doc/docs/GDL_10_3D_Commands_Full.md
---

# CYLIND

`CYLIND` creates a cylinder, cone, or truncated cone in 3D space. It is one of the basic GDL solid primitives alongside [[PRISM_]] and [[BLOCK]].

## Syntax

```gdl
CYLIND x, y, r1, r2, h [, segments]
```

| Param      | Type    | Description                            |
|------------|---------|----------------------------------------|
| `x`, `y`   | numeric | Center of base in the working plane    |
| `r1`       | numeric | Radius at bottom (base)                |
| `r2`       | numeric | Radius at top                          |
| `h`        | numeric | Height (extrusion direction = Z)       |
| `segments` | integer | Optional — circumference resolution    |

## Geometry Rules

- The cylinder base is on the current working plane (XY at `h=0`), centered at `(x, y)`.
- Extrudes along the **Z axis** by `h` units (positive or negative).
- `r1 = r2` → straight cylinder.
- `r1 ≠ r2` → cone or truncated cone.
- `segments` controls the number of **segments per full circle** for the triangulation:
  - Default: **24 segments** (enough for most architectural details).
  - Minimum: **3** (renders as a triangular prism).
  - Higher values (48–64) for smooth curved surfaces visible up close.

## Examples

### Simple cylinder

```gdl
CYLIND 0, 0, 0.5, 0.5, 3.0
```
A cylinder of radius 0.5 m and height 3.0 m, centered at the origin.

### Cone

```gdl
CYLIND 0, 0, 0.6, 0.0, 2.0
```
A cone with 0.6 m base radius tapering to a point at 2.0 m height.

### Truncated cone with segment control

```gdl
CYLIND 0, 0, 0.5, 0.3, 1.5, 32
```
A 32-segment truncated cone (smoother than default).

## Combined with transforms

```gdl
ADD 1.5, 0, 0
  CYLIND 0, 0, 0.3, 0.3, 2.0
DEL 1
```
A column shifted 1.5 m along X.

## Edge Cases & Traps

- **Zero radius**: `r1 = 0` or `r2 = 0` produces a cone (valid). Both zero → invisible geometry.
- **Negative height**: `h < 0` extrudes downward. The cylinder is still solid.
- **Zero height**: no geometry generated (GDL ignores it silently).
- **Too few segments**: `segments < 3` — GDL clamps to 3, producing a triangular shape.
- **Working plane orientation**: use [[Transformation_Stack]] (ROT/ADD) to orient cylinders arbitrarily; alone, `CYLIND` always grows along the **local Z**.
- **Intersection with other primitives**: CYLIND is a **solid** — it performs CSG union with other solids in the same body group.

## Related

- [[PRISM_]] — extruded polygon for non-circular columns
- [[BLOCK]] — axis-aligned box primitive
- [[Transformation_Stack]] — positioning and orienting cylinders
- [[HOTSPOT2]] — adding hotspots to cylinder-based geometry
