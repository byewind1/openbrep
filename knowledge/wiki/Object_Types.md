---
type: reference
status: stable
tags: [object-type, wall, column, beam, slab, door, window, element, symbol]
aliases: [Object_Types, SYMBOL, WALL, COLUMN, DOOR, WINDOW, object types, building elements, element types]
source: raw/ccgdl_dev_doc/docs/GDL_05_Globals_Request.md
---

# Object_Types

Every GDL object inserted into an ArchiCAD project has a **type** determined by the element it's placed in. The type is accessed via the `SYMBOL` global variable and controls default behavior for cut planes, insertion logic, and parameter inheritance.

## Element Type Table

| `SYMBOL` | Element       | Typical Usage                        |
|----------|---------------|--------------------------------------|
| 1        | WALL          | Wall-hosted objects                  |
| 2        | COLUMN        | Free-standing or wall-attached       |
| 3        | BEAM          | Structural beams                     |
| 4        | SLAB          | Floor/roof slabs                     |
| 5        | ROOF          | Roof planes                          |
| 6        | SHELL         | Free-form shells                     |
| 7        | MESH          | Terrain meshes                       |
| 8        | OBJECT        | Generic library part (default)       |
| 9        | LIGHT         | Light fixtures                       |
| 10       | REGION        | 2D regions                           |
| 15       | SKYLIGHT      | Skylights                            |
| 20       | DOOR          | Door objects (auto-embedded in wall) |
| 21       | WINDOW        | Window objects (auto-embedded)       |
| 22       | OPENING       | Wall openings                        |
| 31       | STAIR         | Stair objects                        |
| 32       | RAILING       | Railing objects                      |
| 51       | ZONE          | Zone/room objects                    |
| 61       | CURTAIN_WALL  | Curtain wall systems                 |
| 63       | PANEL         | Curtain wall panels                  |
| 64       | JUNCTION      | Curtain wall junctions               |

## Example: Type-Adaptive Object

```gdl
! Master script
GLOBALS SYMBOL

IF SYMBOL = 1 THEN
  ! In a wall → set depth to wall thickness
  GLOBALS WALLTHICKNESS
  PARAMETERS depth = WALLTHICKNESS
ELSE
  ! Free-standing → use user-specified depth
  PARAMETERS depth = 0.3
ENDIF
```

## Type-Specific Behavior

### Wall-hosted (SYMBOL=1, 20, 21, 22)
- The object inherits wall cut plane behavior.
- Wall-hosted objects (doors, windows) automatically create openings.
- `WALLTHICKNESS` and `WALLH` globals are available.

### Column (SYMBOL=2)
- Columns can be free-standing or embedded in walls.
- Floor plan display includes the column's own cut fill (diagonal cross-hatch by default).
- The cut plane always passes through a column's insertion point.

### Object (SYMBOL=8)
- Generic library part — no special inheritance.
- The object has no built-in relationship to surrounding elements.
- Most custom parametric objects use this type.

### Door / Window (SYMBOL=20, 21)
- **Auto-embedded**: ArchiCAD automatically hosts them in walls.
- **Opening**: the 3D script must include the wall opening geometry (using `CUTFORM` or subtracting geometry).
- **Sill / Header**: additional parameters for sill height, header depth, and reveal.

## Type Detection in UI

```gdl
! 2D script: different display depending on type
GLOBALS SYMBOL

IF SYMBOL = 1 THEN
  ! Show wall connection lines
  LINE2 -0.5, 0, 0.5, 0
ELSE
  ! Show free-standing outline
  CIRCLE 0, 0, 0.1
ENDIF
```

## Edge Cases & Traps

- **Wrong type assignment**: if a door library part is inserted as an OBJECT, it won't create a wall opening automatically — the 3D script must handle opening geometry explicitly.
- **Type overrides via GDL**: you cannot change `SYMBOL` at runtime — it's determined by how the user places the object in ArchiCAD.
- **Unknown types**: future ArchiCAD versions may add new SYMBOL values. Always include an `ELSE` branch for unhandled types.
- **Multiple type support**: a single library part can support multiple SYMBOL types via `IF/ELSIF` branching, but this increases complexity significantly.

## Related

- [[GLOBALS]] — declaring and using SYMBOL and other context variables
- [[CUTPLANE]] — cut plane behavior varies by object type
- [[IF_ENDIF]] — conditional logic based on SYMBOL
- [[Paramlist_XML]] — type-driven parameter defaults
