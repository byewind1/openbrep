---
type: concept
status: stable
tags: [projection, plan, 2d, drawing, 3d-to-2d, view]
aliases: [PROJECT2, projection, 2d projection, plan projection, gdl projection]
source: raw/ccgdl_dev_doc/docs/GDL_11_2D_Advanced.md
---

# PROJECT2

`PROJECT2` generates a 2D plan-view projection from the 3D geometry of a GDL object. It's the standard way to create the floor plan representation of a 3D object automatically.

## Why PROJECT2?

Manually drawing the 2D plan with `RECT2`, `LINE2`, `CIRCLE2`, etc. is tedious and error-prone — and it drifts out of sync when the 3D geometry changes. `PROJECT2` solves this by deriving the 2D drawing directly from the 3D script output, guaranteeing consistency.

## Syntax

```gdl
PROJECT2 [options]
```

`PROJECT2` has no arguments in its simplest form — it automatically projects the 3D geometry onto the XY plane with the current cut plane height.

```gdl
PROJECT2
```

## Cut Plane Behavior

`PROJECT2` respects the ArchiCAD floor plan cut plane. Elements intersected by the cut plane are shown with cut lines; elements below appear as projected outlines.

To control the cut plane from GDL:

```gdl
CUTPLANE x, y, z, angle    ! set cut plane position
```

This is rarely needed — ArchiCAD's automatic cut plane usually produces correct results.

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
| Objects with custom cut planes | `PROJECT2` — automatic |
| Complex plan annotations needed | Manual 2D + [[HOTSPOT2]] |
| Hidden line removal | Manual 2D |
| Very complex 3D (slow projection) | Manual 2D for performance |

## Edge Cases & Traps

- **Performance**: `PROJECT2` must evaluate the full 3D script internally. For objects with complex 3D (loops, many primitives), the projection can be slow. Consider caching the result or using manual 2D.
- **Only XY projection**: `PROJECT2` works on the XY plane only. It does not produce elevation or section views.
- **No style control**: you cannot control line type, pen, or fill from `PROJECT2` directly. Use `PEN`, `LINE_TYPE`, `FILL` before calling it.
- **Interaction with HOTSPOT2**: `PROJECT2` draws lines, not hotspots. You must place [[HOTSPOT2]] separately for interactive editing.
- **Empty 2D script**: if the 2D script is empty and `PROJECT2` is missing, the object has no floor plan display.

## Related

- [[HOTSPOT2]] — interactive hotspots for the 2D plan
- [[PRISM_]] — 3D geometry that PROJECT2 projects from
- [[BLOCK]] — simple primitive with automatic projection
