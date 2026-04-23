---
type: concept
status: stable
tags: [globals, request, context, project-info, parameters]
aliases: [GLOBALS, globals, request, global variables, SYMBOL, WALL, COLUMN, OBJECT]
source: raw/ccgdl_dev_doc/docs/GDL_05_Globals_Request.md
---

# GLOBALS

`GLOBALS` declares which **project context variables** a GDL object needs access to. These variables tell the object about its surroundings — what type of building element it's attached to, the current floor plan scale, and other project-level information.

## Syntax

```gdl
GLOBALS list_of_variables
```

A GLOBALS statement is typically placed in the **master script** and lists the context variables the object uses across all scripts.

## Common GLOBALS Variables

### Element Type

```gdl
GLOBALS SYMBOL
```
`SYMBOL` returns an integer identifying the type of element this object is placed in:

| Value | Element Type    |
|-------|-----------------|
| 1     | WALL            |
| 2     | COLUMN          |
| 3     | BEAM            |
| 4     | SLAB            |
| 5     | ROOF            |
| 6     | SHELL           |
| 7     | MESH            |
| 8     | OBJECT          |
| 9     | LIGHT           |
| 10    | REGION          |
| 15    | SKYLIGHT        |
| 20    | DOOR            |
| 21    | WINDOW          |
| 22    | OPENING         |
| 31    | STAIR           |
| 32    | RAILING         |
| 51    | ZONE            |
| 61    | CURTAIN_WALL    |
| 63    | PANEL           |
| 64    | JUNCTION        |

### Story / Floor

```gdl
GLOBALS STORY, STORYLEVEL
```
- `STORY` — story number (0 = ground floor, negative = basement)
- `STORYLEVEL` — absolute Z-coordinate of the story

### Cut Plane / Display

```gdl
GLOBALS CUTPLANE, CUTHEIGHT
```
- `CUTPLANE` — Z-coordinate of the floor plan cut plane
- `CUTHEIGHT` — height above cut plane still shown

### Scale

```gdl
GLOBALS SCALE
```
- `SCALE` — inverse of the drawing scale (e.g., `100` = 1:100)

### Other Common Variables

| Variable       | Returns                         |
|----------------|---------------------------------|
| `REVISION`     | Current revision number         |
| `REVERSION`    | GDL revision (internal version) |
| `DISTANCE`     | Wall thickness (when in wall)   |
| `WALLH`        | Wall height                     |
| `WALLTHICKNESS`| Wall thickness alias            |

## Examples

### Object that adapts to element type

```gdl
! Master script
GLOBALS SYMBOL

IF SYMBOL = 2 OR SYMBOL = 1 THEN
  PARAMETERS A = 0.5, B = 0.5
ELSE
  PARAMETERS A = 0.3, B = 0.3
ENDIF
```

### Wall-dependent parameter

```gdl
GLOBALS WALLTHICKNESS

IF WALLTHICKNESS > 0 THEN
  PARAMETERS depth = WALLTHICKNESS
ELSE
  PARAMETERS depth = 0.3
ENDIF
```

### Multiple global context variables

```gdl
GLOBALS SYMBOL, STORYLEVEL, SCALE, CUTPLANE

! Conditional display based on story
IF STORYLEVEL > 50 THEN
  ! Only on upper floors
  BLOCK 0, 0, 0, 1, 1, 1
ENDIF
```

## GLOBALS in Different Scripts

| Script     | Typical GLOBALS                                |
|------------|------------------------------------------------|
| Master     | `SYMBOL`, `STORYLEVEL`, parameters conditional |
| 1D/Plan    | `SCALE`, `CUTPLANE`                            |
| 2D/Projection | `CUTPLANE`, `SCALE`                        |
| 3D         | None (rarely needed)                           |
| Properties | None                                           |

## Edge Cases & Traps

- **Unused GLOBALS**: declaring a GLOBALS variable that is never referenced has no performance cost but clutters the script — only request what you use.
- **Undeclared access**: reading `SYMBOL` without `GLOBALS SYMBOL` returns 0 or undefined — the value is **not** automatically available.
- **Variable not supported**: requesting a GLOBALS variable that doesn't exist in your ArchiCAD version returns `0` silently — no error or warning.
- **GLOBALS in subroutines**: `GLOBALS` must appear in the **master script** or the calling script's top-level scope, not inside `DEFINE` blocks.

## Related

- [[IF_ENDIF]] — branching on GLOBALS values
- [[CUTPLANE]] — cut plane configuration
- [[Paramlist_XML]] — parameters driven by GLOBALS values
