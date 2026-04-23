---
type: concept
status: stable
tags: [2d, geometry, line, circle, arc, poly, primitives]
aliases: [2D, line, circle, arc, poly, poly2, LINE_TO, CIRCLE, ARC, POLY2]
source: raw/ccgdl_dev_doc/docs/GDL_11_2D_Advanced.md
---

# 2D_Primitives

2D primitives are used in the **2D script** (`2d.gdl`) and **UI script** for floor plan symbols, projections, and dialog previews. They produce flat geometry in the XY plane.

## LINE_TO

Draws a line segment from the current position to a target:

```gdl
LINE_TO x, y [, pen]
```

```gdl
! Draw a rectangle outline
LINE2 0, 0, 1, 0
LINE2 1, 0, 1, 1
LINE2 1, 1, 0, 1
LINE2 0, 1, 0, 0
```

## CIRCLE

```gdl
CIRCLE x, y, r [, pen]
```

Full circle centered at `(x, y)` with radius `r`.

```gdl
CIRCLE 0.5, 0.5, 0.3, 2
```

## ARC

```gdl
ARC x, y, r, start_angle, end_angle [, pen]
```

| Param        | Description                         |
|--------------|-------------------------------------|
| `x, y`       | Center point                        |
| `r`          | Radius                              |
| `start_angle`| Starting angle in degrees           |
| `end_angle`  | Ending angle in degrees (CCW)       |

```gdl
! 90-degree arc
ARC 0.5, 0.5, 0.3, 0, 90
```

## POLY2

General 2D polygon (filled or outlined):

```gdl
POLY2 id, n, status_code,
    x1, y1, mask1,
    x2, y2, mask2,
    ...
```

| Param | Description                                |
|-------|--------------------------------------------|
| `id`  | Polygon ID (for later reference)           |
| `n`   | Number of vertices                         |
| `status_code` | Fill mode (0 = outline, 1 = filled)|
| `mask`| Edge visibility (1 = visible, 0 = hidden)  |

```gdl
! Filled triangle
POLY2 1, 3, 1,
    0,   0,   1,
    0.5, 0.5, 1,
    0,   0.5, 1
```

## Common 2D Patterns

### Cross symbol (center marker)

```gdl
LINE2 -0.1, 0, 0.1, 0
LINE2 0, -0.1, 0, 0.1
```

### Filled circle using polygons

```gdl
! Regular hexagon approximating a circle
n = 6
POLY2 1, n+1, 1,
    0.5 + 0.2*COS(0),   0.5 + 0.2*SIN(0),   1,
    0.5 + 0.2*COS(60),  0.5 + 0.2*SIN(60),  1,
    0.5 + 0.2*COS(120), 0.5 + 0.2*SIN(120), 1,
    0.5 + 0.2*COS(180), 0.5 + 0.2*SIN(180), 1,
    0.5 + 0.2*COS(240), 0.5 + 0.2*SIN(240), 1,
    0.5 + 0.2*COS(300), 0.5 + 0.2*SIN(300), 1,
    0.5 + 0.2*COS(360), 0.5 + 0.2*SIN(360), 1
```

### Rectangular fill

```gdl
RECT2 0, 0, 1, 0.5
```

## Attributes

| Attribute | Effect                                      |
|-----------|---------------------------------------------|
| PEN       | Sets line color and thickness               |
| FILL      | Sets fill pattern (solid, hatched, etc.)    |

```gdl
PEN 3
FILL 71
RECT2 0, 0, 1, 0.5
```

## Edge Cases & Traps

- **Zero-length line**: `LINE_TO` with zero displacement produces nothing — no error.
- **PEN outside range**: pen numbers outside 1–255 are clamped.
- **Negative radius**: `CIRCLE` / `ARC` with negative radius produces nothing.
- **ARC angle wrap**: GDL draws arcs CCW. For CW arcs, swap start/end or use negative angles.
- **POLY2 self-intersection**: self-intersecting polygons may not fill correctly — decompose into convex sub-polygons.
- **Hidden edges**: `mask=0` edges are invisible (coincident with neighboring polygon edges). Use for shared edges in adjacent polygons.

## Related

- [[PROJECT2]] — automatic 3D→2D projection (alternative to manual 2D)
- [[CUTPLANE]] — cut plane influence on 2D display
- [[IF_ENDIF]] — conditional 2D display logic
- [[Paramlist_XML]] — parameters driving 2D symbol dimensions
