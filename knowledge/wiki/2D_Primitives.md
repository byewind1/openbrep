---
type: concept
status: stable
tags: [2d, geometry, line, circle, arc, polygon, primitives]
aliases: [2D, line, circle, arc, poly, poly2, LINE2, RECT2, CIRCLE2, ARC2, POLY2]
source: official:gdl.graphisoft.com/reference-guide/2d-shapes
---

# 2D_Primitives

2D primitives are used in the 2D script to draw plan symbols, annotations, and manual 2D fallback graphics.

OpenBrep should prefer these for explicit 2D symbols and use `PROJECT2` only when the 2D view should be derived from the 3D script.

## Official Core Commands

```gdl
LINE2 x1, y1, x2, y2
RECT2 x1, y1, x2, y2
CIRCLE2 x, y, r
ARC2 x, y, r, start_angle, end_angle
POLY2 n, status, x1, y1, s1, ..., xn, yn, sn
```

## Recommended OpenBrep Use

- Use `LINE2` and `RECT2` for clear plan outlines.
- Use `CIRCLE2` and `ARC2` for round symbols, cutouts, and rotation markers.
- Use `POLY2` for filled or outlined 2D regions when the shape is not a simple rectangle or circle.
- Use `HOTSPOT2` separately when the 2D element should be draggable.
- Keep manual 2D drawings small and intentional; if the 2D symbol mirrors 3D geometry, prefer `PROJECT2`.

## Examples

### Rectangle outline

```gdl
LINE2 0, 0, 1, 0
LINE2 1, 0, 1, 1
LINE2 1, 1, 0, 1
LINE2 0, 1, 0, 0
```

### Circle

```gdl
CIRCLE2 0.5, 0.5, 0.3
```

### Arc

```gdl
ARC2 0.5, 0.5, 0.3, 0, 90
```

### Filled polygon

```gdl
POLY2 3, 1,
    0,   0,   1,
    0.5, 0.5, 1,
    0,   0.5, 1
```

## Edge Cases & Traps

- `LINE2` and `RECT2` are 2D only.
- `CIRCLE2` and `ARC2` do not produce 3D geometry.
- `POLY2` uses polygon point status codes; do not invent 3D-style mask or vertex syntax.
- Self-intersecting 2D polygons may not fill correctly.
- Hidden 2D editing points should be expressed with `HOTSPOT2`, not with pseudo geometry.

## Related

- [[PROJECT2]] — automatic 3D-to-2D projection
- [[HOTSPOT2]] — interactive 2D editing points
- [[IF_ENDIF]] — conditional 2D display logic
