# GDL Parameters and Script Types

## Script Types in GDL Objects

### 1D Script (Master Script)
- Runs first, validates all parameters
- Calculates derived values
- Called from: MASTER SCRIPT

```gdl
! Validation
IF A < 0.3 THEN A = 0.3
IF B < 0.3 THEN B = 0.3

! Calculate derived values
_legInset = 0.03
_seatH = ZZYZX * 0.5
```

### 2D Script
- Draws plan (top) view
- Must include HOTSPOT2 for resize handles
- Called from: 2D SCRIPT

```gdl
HOTSPOT2 0, 0
HOTSPOT2 A, 0
HOTSPOT2 A, B
HOTSPOT2 0, B

RECT2 0, 0, A, B
```

### 3D Script
- Draws 3D geometry
- Main creative content
- Called from: 3D SCRIPT

```gdl
TOLER 0.001

GOSUB "DrawLegs"
GOSUB "DrawSeat"

END

"DrawLegs":
    ! Leg geometry
RETURN
```

### Parameter Script (Values Script)
- Defines parameter ranges and constraints
- Sets VALUES and LOCK
- Called from: PARAMETER SCRIPT

```gdl
VALUES "A" RANGE [0.3, 2.0]
VALUES "B" RANGE [0.3, 1.5]
VALUES "bHasBack" 0, 1
LOCK "A", "B"
```

## Parameter Types in HSF

| Type | HSF Name | Example | Usage |
|------|----------|---------|-------|
| Dimension | Length | 0.6 | Sizes: A, B, ZZYZX, seatH |
| Number | Integer | 4 | Counts: nLegs, nSegments |
| Yes/No | Boolean | 0/1 | Flags: hasBack, isVisible |
| Decimal | RealNum | 0.5 | Angles: rRotate, rScale |
| Color | Pencolor | 1-255 | Pen numbers |
| Material | Material | "Wood" | Material names |
| Text | String | "Label" | Text parameters |

## Reserved Parameters

**MUST NOT BE MODIFIED:**
- `A` — Object width (reserved)
- `B` — Object depth (reserved)
- `ZZYZX` — Object height (reserved)

## Naming Conventions

```gdl
A, B, ZZYZX       ! Reserved dimensions

seatH, legW       ! Dimensions: descriptive names
nLegs, nShelves   ! Integer: count/number
bHasBack          ! Boolean: has/is prefix
rAngle            ! Real: decimal angles
sMaterial         ! String: descriptive
gs_tempValue      ! Global: gs_ prefix
_derived          ! Internal: underscore prefix
```

## Parameter Ranges

Always set sensible ranges in Parameter Script:

```gdl
VALUES "A" RANGE [0.3, 2.0]    ! Min, Max
VALUES "B" RANGE [0.3, 1.5]
VALUES "nLegs" RANGE [2, 6]
VALUES "bHasBack" 0, 1          ! Discrete values
```

## Important Notes

- Parameter validation goes in **1D Script** (Master)
- Range definition goes in **Parameter Script** (Values)
- All parameters must be declared before any script uses them
- Boolean parameters must be 0 (FALSE) or 1 (TRUE)

## UNIT SYSTEM — CRITICAL
GDL scripts use METERS (m) as the internal unit. This is fixed and cannot be changed.

Rules:
- User says "600mm wide" → write A = 0.600 in paramlist, use A in script
- User says "12mm thick" → write thk = 0.012
- User says "1200mm height" → write ZZYZX = 1.200
- NEVER write values like 600, 1200, 450 as default lengths — these would mean 600m, 1200m
- NEVER write "mm" as a variable name or comment variable

Common reference values (meters):
- Door width: 0.900 | Door height: 2.100
- Column: 0.300~0.500 | Beam height: 0.400~0.600
- Wall thickness: 0.200~0.300 | Floor slab: 0.120~0.200
- Bolt diameter: 0.016~0.024 | Plate thickness: 0.010~0.020
- Furniture height: 0.750~0.900 | Stair riser: 0.150~0.180
