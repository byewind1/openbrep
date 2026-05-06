---
type: concept
status: stable
tags: [projection, plan, 2d, drawing, 3d-to-2d, view]
aliases: [PROJECT2, projection, 2d projection, plan projection, gdl projection]
source: official:gdl.graphisoft.com/reference-guide/3d-projections-in-2d
---

# PROJECT2

`PROJECT2` generates a 2D projection from the 3D geometry of a GDL object. OpenBrep uses it as the default way to derive a plan symbol from 3D script output when manual 2D drawing is not needed.

## Why PROJECT2?

Manually drawing the 2D plan with `RECT2`, `LINE2`, `CIRCLE2`, etc. is tedious and error-prone — and it drifts out of sync when the 3D geometry changes. `PROJECT2` solves this by deriving the 2D drawing directly from the 3D script output, guaranteeing consistency.

## Official Syntax

```gdl
PROJECT2 projection_code, angle, method
PROJECT2{2} projection_code, angle, method [, backgroundColor, fillOrigoX, fillOrigoY, filldirection]
PROJECT2{3} projection_code, angle, method, parts [, backgroundColor, fillOrigoX, fillOrigoY, filldirection] [[,] PARAMETERS name1=value1, ..., namen=valuen]
```

Recommended default for a top-view plan projection:

```gdl
PROJECT2 3, 270, 2
```

`projection_code = 3` means top view. `method = 2` means hidden lines.

## Cut Plane Behavior

`PROJECT2` projects the 3D script in the same library part and appends the generated lines to the 2D parametric symbol.

It does not define the cut plane itself. Use `CUTPLANE` / `CUTEND` only when you need an actual 3D cutaway.

## Manual 2D Drawing

When `PROJECT2` doesn't produce the right result (e.g., you need dashed hidden lines, special hatching, or simplified outlines), fall back to manual 2D commands:

```gdl
! Manual 2D plan of a rectangular column
LINE2 0, 0, A, 0
LINE2 A, 0, A, B
LINE2 A, B, 0, B
LINE2 0, B, 0, 0

! Add diagonal cross for center mark
LINE2 0, 0, A, B
LINE2 A, 0, 0, B
```

## When to Use PROJECT2

| Situation | Recommendation |
|-----------|----------------|
| Simple objects (boxes, prisms) | `PROJECT2` — automatic |
| Objects with standard plan display | `PROJECT2` — automatic |
| Objects with custom plan annotations | Manual 2D + [[HOTSPOT2]] |
| Hotspot-driven editing | `PROJECT2` + [[HOTSPOT2]] |
| Very complex 3D (slow projection) | Manual 2D for performance |

## Edge Cases & Traps

- **Performance**: `PROJECT2` must evaluate the full 3D script internally. For objects with complex 3D (loops, many primitives), the projection can be slow. Consider caching the result or using manual 2D.
- **Arguments are required**: do not emit bare `PROJECT2`; use `PROJECT2 3, 270, 2` for the common top-view hidden-line plan.
- **Projection code matters**: `3` is top view, `-3` is bottom view, and other official codes produce side or axonometric views.
- **No style control**: line type, pen, and fill still need to be set with normal 2D commands before calling it.
- **Interaction with HOTSPOT2**: `PROJECT2` draws lines, not hotspots. You must place [[HOTSPOT2]] separately for interactive editing.
- **Empty 2D script**: if the 2D script is empty and `PROJECT2` is missing, the object has no floor plan display.

## Related

- [[HOTSPOT2]] — interactive hotspots for the 2D plan
- [[PRISM_]] — 3D geometry that PROJECT2 projects from
- [[BLOCK]] — simple primitive with automatic projection
