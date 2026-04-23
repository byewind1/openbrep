---
type: concept
status: stable
tags: [3d, geometry, sweep, extrusion, path]
aliases: [SWEEP, sweep, extrude along path]
source: raw/ccgdl_dev_doc/docs/GDL_10_3D_Commands_Full.md
---

# SWEEP

`SWEEP` generates a 3D solid by extruding a 2D cross-section along a **3D path**. Unlike [[PRISM_]] which extrudes straight along Z, SWEEP can follow arbitrary polyline paths in 3D space, making it ideal for railings, frames, mouldings, and curved profiles.

## Syntax

```gdl
SWEEP path_id, sections_id [, contour] [, subdiv]
```

| Param         | Type    | Description                                            |
|---------------|---------|--------------------------------------------------------|
| `path_id`     | integer | ID of the path definition (a POLY-based 3D line)       |
| `sections_id` | integer | ID of the 2D cross-section definition (POLY-based)     |
| `contour`     | integer | Optional — 0: solid (default), 1: contour only         |
| `subdiv`      | integer | Optional — subdivision level for smooth interpolation  |

## How It Works

1. **Define the cross-section** with a 2D `POLY` (or `POLY2`) command, assigned an ID.
2. **Define the path** with a 3D `POLY` command (series of coordinate triplets), assigned another ID.
3. **Call `SWEEP`** referencing both IDs.

The cross-section is swept along the path, maintaining its orientation relative to the path tangent.

## Example

### Simple moulding along a straight path

```gdl
! Cross-section (L-shaped moulding profile)
POLY2 1, 4, 1,
    0,    0,   1,
    0.05, 0,   1,
    0.05, 0.1, 1,
    0,    0.1, 1

! Path (straight 2 m run)
POLY 2, 3,
    0, 0, 0,
    0, 0, 2.0

SWEEP 2, 1
```

### Curved handrail

```gdl
! Circular section, radius 0.02
POLY2 1, 36, 1,
    (SIN(0)*0.02), (COS(0)*0.02), 1,
    (SIN(30)*0.02), (COS(30)*0.02), 1,
    (SIN(60)*0.02), (COS(60)*0.02), 1,
    ...

! Arced path
POLY 2, 10,
    0, 0, 0.9,
    0.2, 0, 0.9,
    0.4, 0, 0.85,
    ...

SWEEP 2, 1
```

## Orientation Rules

The sweep maintains **minimal twist**: the section is oriented relative to the path tangent and a reference vector. For precise control, use [[Transformation_Stack]] to position the sweep result rather than relying on automatic orientation.

## Edge Cases & Traps

- **Self-intersecting path**: if the path bends so sharply that the cross-section overlaps itself, the result may produce malformed geometry.
- **Parallel tangents**: when path segments form a perfectly straight line, orientation calculation may produce unexpected rotations — insert small micro-kinks to avoid ambiguity.
- **Zero-length path**: one or more zero-length path segments causes sweep failure.
- **Closed vs open path**: a closed path (first point = last point) produces a ring sweep.
- **Cross-section closed automatically**: GDL implicitly closes the section if not already closed.
- **Complex sections**: avoid very detailed cross-sections with many vertices — they multiply along every path segment, increasing polygon count dramatically.
- **Contour-only mode**: `contour=1` generates a surface (not a solid), useful for thin shells.

## Related

- [[REVOLVE]] — rotational sweep around an axis
- [[PRISM_]] — straight Z-extrusion (simpler alternative)
- [[CYLIND]] — simple circular extrusion (special case of SWEEP)
- [[Transformation_Stack]] — positioning the sweep result
- [[BODY_EDGE_PGON]] — underlying mesh representation
