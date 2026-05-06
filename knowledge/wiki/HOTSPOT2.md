---
type: concept
status: stable
tags: [hotspot, stretch, plan, 2d, drawing, interactive, editing]
aliases: [HOTSPOT2, hotspot, stretch hotspot, 2d hotspot]
source: official:gdl.graphisoft.com/reference-guide/graphical-editing-using-hotspots
---

# HOTSPOT2

`HOTSPOT2` defines a 2D interactive hotspot. It is used in the 2D script to support graphical editing of length and angle parameters.

## Why HOTSPOT2?

Parameters are powerful, but changing a value in the dialog box is slow. With `HOTSPOT2`, users can click and drag geometry directly in the plan view. The hotspot movement is automatically linked to a parameter, making the object feel like a native ArchiCAD element.

## Official Syntax

```gdl
HOTSPOT2 x, y [, unID [, paramReference [, flags [, displayParam [, "customDescription"]]]]]
```

| Param               | Type    | Description                                                   |
|---------------------|---------|---------------------------------------------------------------|
| `x, y`              | length  | Position in 2D plan coordinates                               |
| `unID`              | integer | Unique hotspot identifier in the 2D Script                    |
| `paramReference`    | param   | Parameter edited by graphical hotspot editing                 |
| `flags`             | integer | Hotspot type and attributes                                   |
| `displayParam`      | param   | Parameter displayed in the information palette while editing  |
| `customDescription` | string  | Custom label for the displayed parameter                      |

When only `x, y` are given, the hotspot is a fixed selection/reference point. Editable hotspots require the graphical-editing pattern from the GDL manual: length editing uses base, moving, and reference hotspots with matching `paramReference` and suitable `flags`.

## Examples

### Rectangle with editable width and depth

```gdl
HOTSPOT2 0, 0, 1
HOTSPOT2 A, 0, 2
HOTSPOT2 A, -0.1, 3, A, 3
HOTSPOT2 A, 0, 4, A, 2
```

### Corner hotspot

```gdl
! A single corner stretch for a rectangular column
HOTSPOT2 0, 0, 1
HOTSPOT2 A, 0, 2
HOTSPOT2 A, B, 3
HOTSPOT2 0, B, 4
```

## Edge Cases & Traps

- **HOTSPOT2 in 3D script**: hotspots only work in the 2D script. Placing them in the 3D script has no effect.
- **Overlapping hotspots**: if two hotspots share the same position, dragging becomes ambiguous. Avoid duplicates.
- **Parameter type**: the linked `paramReference` must be compatible with the edited parameter.
- **Editable hotspots require a set**: for length editing, define base, moving, and reference hotspots. A single `HOTSPOT2 x, y, "A"` is not the official editable pattern.
- **Angle hotspots**: for angle parameters, use the graphical editing pattern for angle-type hotspots.
- **No visual feedback**: hotspots themselves are invisible interaction points. Draw visible geometry ([[PROJECT2]], `RECT2`, etc.) separately.
- **Coordinate system**: hotspot positions are in the 2D script's coordinate space.

## Convention: Complete set

A well-designed GDL object provides hotspots for every primary dimension parameter. At minimum, include:

```gdl
! Origin (always)
HOTSPOT2 0, 0
! Width stretch: base/reference/moving pattern
HOTSPOT2 A, 0, 11, A, 1
HOTSPOT2 A, -0.1, 12, A, 3
HOTSPOT2 A, 0, 13, A, 2
```

## Related

- [[PROJECT2]] — drawing 2D representations of 3D geometry
- [[Paramlist_XML]] — defining parameters that hotspots link to
- [[BLOCK]] — 3D geometry that hotspots control indirectly
