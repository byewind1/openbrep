---
type: concept
status: stable
tags: [3d, geometry, sweep, extrusion, path]
aliases: [SWEEP, sweep, extrude along path]
source: official:gdl.graphisoft.com/reference-guide/index
---

# SWEEP

`SWEEP` generates a surface or body by sweeping a 2D profile polyline along a 3D path polyline. Unlike [[PRISM_]], which extrudes straight along Z, `SWEEP` can follow a path in space, making it useful for railings, frames, mouldings, and profile-based details.

OpenBrep should treat `SWEEP` as advanced geometry. Use it when the object genuinely needs a profile along a path; otherwise prefer [[PRISM_]], [[BLOCK]], or [[CYLIND]].

## Syntax

```gdl
SWEEP n, m, alpha, scale, mask,
        u1, v1, s1, ..., un, vn, sn,
        x1, y1, z1, ..., xm, ym, zm
```

| Param     | Type    | Description                                      |
|-----------|---------|--------------------------------------------------|
| `n`       | integer | Number of profile polyline nodes                 |
| `m`       | integer | Number of path polyline nodes                    |
| `alpha`   | angle   | Twist/rotation control                           |
| `scale`   | numeric | Profile scaling along the path                   |
| `mask`    | integer | Edge/surface visibility controls                 |
| `u, v, s` | mixed   | 2D profile nodes and status codes                |
| `x, y, z` | length  | 3D path nodes                                    |

## How It Works

1. Write the 2D cross-section nodes inline as `u, v, s` triplets.
2. Write the 3D path nodes inline as `x, y, z` triplets.
3. Choose a conservative `mask` and keep the profile simple until the object compiles.

The cross-section is swept along the path, maintaining its orientation relative to the path tangent.

## Example

### Simple moulding along a straight path

```gdl
SWEEP 4, 2, 0, 1, 63,
    0.00, 0.00, 0,
    0.05, 0.00, 0,
    0.05, 0.10, 0,
    0.00, 0.10, 0,
    0.00, 0.00, 0.00,
    0.00, 0.00, 2.00
```

### Curved handrail

```gdl
! For curved handrails, generate a small circular profile and path points inline.
! Keep segment counts conservative for performance.
SWEEP nProfile, nPath, 0, 1, 63,
    ! profile u, v, s...
    ! path x, y, z...
```

## Orientation Rules

The sweep maintains **minimal twist**: the section is oriented relative to the path tangent and a reference vector. For precise control, use [[Transformation_Stack]] to position the sweep result rather than relying on automatic orientation.

## Edge Cases & Traps

- **Advanced non-default command**: require an explicit generation-plan reason before using `SWEEP`.
- **Self-intersecting path**: if the path bends so sharply that the cross-section overlaps itself, the result may produce malformed geometry.
- **Parallel tangents**: when path segments form a perfectly straight line, orientation calculation may produce unexpected rotations — insert small micro-kinks to avoid ambiguity.
- **Inline syntax**: do not generate `POLY2 id` + `POLY id` + `SWEEP path_id, section_id`; that is not the official `SWEEP` syntax.
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
