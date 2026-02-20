# Skill: Create GDL Object from Scratch

Concrete rules derived from real compilation failures and ArchiCAD runtime errors.
Follow these exactly — violations cause either compile failure or silent 3D display failure.

---

## 1. Parameter Naming & Calculation

**Built-in size parameters (always available, never redeclare):**
```
A      = X-size (width)
B      = Y-size (depth)
ZZYZX  = Z-size (height)
```

**Derived intermediate variables — MUST be calculated at the top of the 3D script (or Master), before any geometry:**
```gdl
! Always calculate these before use
_insideW = A - 2 * frameW
_insideD = B - 2 * frameW
_insideH = ZZYZX - 2 * frameW
_rodSpacing  = _insideW / (iHangingRods + 1)
_shelfSpacing = _insideH / (iShelves + 1)
```
**PITFALL:** Variables starting with `_` that are used but never assigned cause ArchiCAD to silently fail (no 3D shown, no error message). This is the #1 cause of "compiled OK but nothing displays."

**Parameter naming convention:**
- `A`, `B`, `ZZYZX` → built-in sizes (use directly)
- `i` prefix → Integer count (e.g. `iShelves`, `iDrawers`)
- `b` prefix → Boolean toggle (e.g. `bDoors`, `bShowClothes`)
- `r` prefix → Ratio 0–1 (e.g. `rDoorSlide`)
- `mat` prefix → Material index (e.g. `matBody`, `matDoor`)
- `_` prefix → Local intermediate variable (not in paramlist, compute in script)

---

## 2. 3D Script Structure

### Simple object (no subroutines):
```gdl
! 3D Script
TOLER 0.001
MATERIAL matBody

! All geometry here
BLOCK A, B, ZZYZX

END
```
**RULE:** Last line of the entire 3D script MUST be `END`. Nothing after it.

### Complex object with subroutines (GOSUB pattern):
```gdl
! 3D Script
TOLER 0.001

! Pre-calculate intermediate variables
_insideW = A - 2 * frameW
_insideD = B - frameW

! Main body — GOSUB calls only, then END
GOSUB "DrawFrame"
GOSUB "DrawShelves"
IF bDoors THEN
  GOSUB "DrawDoors"
ENDIF
END              ! ← END here = end of main body, NOT end of file

! Subroutines below END — each ends with RETURN, NOT END
"DrawFrame":
  MATERIAL matBody
  BLOCK A, B, frameW
RETURN           ! ← RETURN, never END inside subroutine

"DrawShelves":
  FOR i = 1 TO iShelves
    ADDZ frameW + _shelfSpacing * i
      BLOCK _insideW, B, 0.02
    DEL 1
  NEXT i
RETURN

"DrawDoors":
  MATERIAL matDoor
  ADDZ frameW
  ADDY B - 0.02
    BLOCK A, 0.02, _insideH
  DEL 2
RETURN           ! ← Last subroutine also ends with RETURN
```
**PITFALL:** Putting `END` instead of `RETURN` in a subroutine terminates all geometry rendering at that point. Objects after the bad `END` are never drawn.

---

## 3. ADD / DEL — Coordinate Transform Rules

Every ADD variant pushes one level onto the transform stack. `DEL n` pops n levels.

| Command | Axes moved | Stack levels pushed |
|---------|-----------|-------------------|
| `ADD x, y, z` | all 3 | 1 |
| `ADDX x` | X only | 1 |
| `ADDY y` | Y only | 1 |
| `ADDZ z` | Z only | 1 |
| `ROTZ angle` | rotation | 1 |
| `ROTX angle` | rotation | 1 |
| `ROTY angle` | rotation | 1 |
| `MUL x, y, z` | scale | 1 |
| `DEL n` | — | pops n |

**RULE:** Total ADD+ADDX+ADDY+ADDZ+ROT+MUL in any execution path MUST equal total DEL values. Use `DEL n` to pop multiple levels at once:
```gdl
ADDX 0.5
ADDY 0.3
ADDZ 1.2
  BLOCK 0.1, 0.1, 0.1
DEL 3        ! pops all 3 in one statement — correct
```

**PITFALL — FOR loop:** DEL inside a loop must match ADDs inside that same loop iteration:
```gdl
FOR i = 1 TO 5
  ADDZ i * 0.3    ! 1 push per iteration
    BLOCK A, B, 0.02
  DEL 1           ! 1 pop — balanced per iteration ✓
NEXT i
```

**PITFALL — IF branch:** If one branch has ADD, both branches must have matching DEL:
```gdl
IF bExtra THEN
  ADDZ 0.5
    BLOCK A, B, 0.05
  DEL 1
ENDIF
! No DEL outside IF — correct. DEL inside IF only when ADD is inside IF.
```

---

## 4. Geometry Commands — Correct Syntax

### BLOCK (rectangular box):
```gdl
BLOCK width, depth, height    ! x, y, z sizes
! At current origin, extends in +X, +Y, +Z direction
BLOCK A, B, ZZYZX             ! full-size box
```

### PRISM_ (extruded polygon):
```gdl
! PRISM_ n, height, x1,y1, x2,y2, ..., xn,yn
! n = number of UNIQUE vertices (DO NOT repeat first point to close)
PRISM_ 4, 0.02, 0,0, A,0, A,B, 0,B    ! rectangle — 4 vertices, no closure
PRISM_ 3, 0.1,  0,0, 1,0, 0.5,1       ! triangle — 3 vertices
```
**PITFALL:** `PRISM_ 5, h, 0,0, A,0, A,B, 0,B, 0,0` has a degenerate 5th face (zero area). Use `PRISM_ 4` for rectangles, or just use `BLOCK`.

### CYLIND (cylinder):
```gdl
! CYLIND height, radius
CYLIND 1.2, 0.015     ! height=1.2m, radius=15mm
! Cylinder axis is along current Z direction
```
**PITFALL:** Parameters are often swapped. Height comes FIRST, radius SECOND.

### SPHERE:
```gdl
SPHERE radius          ! centered at current origin
```

### CONE:
```gdl
CONE height, r_bottom, r_top    ! tapered cylinder
```

---

## 5. MATERIAL Usage

```gdl
MATERIAL matBody          ! use parameter variable (Integer/Material type)
MATERIAL "Wood - Oak"     ! use string name — must match ArchiCAD library exactly
```
**PITFALL:** String material names that don't exist in ArchiCAD's loaded libraries cause geometry to render with error material (magenta). Always prefer parameter variables over hardcoded strings.
**PITFALL:** `MATERIAL` must come BEFORE the geometry command it applies to. It persists until changed.

---

## 6. 2D Script — Mandatory Minimum

The 2D script MUST contain at least one projection or drawing command. Empty 2D = invisible in floor plan.

**Minimum (auto-projection of 3D):**
```gdl
! 2D Script
PROJECT2 3, 270, 2
```
`PROJECT2 3, 270, 2` = top-down projection, 270° rotation, show cut lines.

**Custom 2D symbol:**
```gdl
! 2D Script
LINE2 0, 0, A, 0
LINE2 A, 0, A, B
LINE2 A, B, 0, B
LINE2 0, B, 0, 0
```

---

## 7. Control Flow

### FOR loop:
```gdl
FOR i = 1 TO nItems        ! i goes 1, 2, ..., nItems
  ! body
NEXT i
```
**PITFALL:** `FOR i = 0 TO n-1` vs `FOR i = 1 TO n` — choose one pattern consistently. Mixing causes off-by-one in geometry placement.

### IF / ENDIF:
```gdl
! Single-line IF (no ENDIF needed):
IF bDoors THEN GOSUB "DrawDoors"

! Multi-line IF (ENDIF required):
IF iDrawers > 0 THEN
  ! geometry
ENDIF

IF bExtra THEN
  BLOCK A, B, 0.05
ELSE
  ADDZ 0.05
    BLOCK A, B, 0.02
  DEL 1
ENDIF
```

### GOSUB / RETURN:
```gdl
GOSUB "SubName"      ! call subroutine

"SubName":
  ! body
RETURN               ! always RETURN to exit subroutine
```

---

## 8. Common Silent Failures (compiled OK, nothing displays)

| Symptom | Likely Cause |
|---------|-------------|
| Nothing in 3D view | `_var` used but never assigned |
| Nothing in 3D view | ADD/DEL imbalance causes runtime crash |
| Nothing in 3D view | MATERIAL string not in loaded libraries |
| Partial display | Subroutine has `END` instead of `RETURN` |
| Geometry at wrong position | ADDX/ADDY/ADDZ not DEL'd after use |
| 2D view empty | No PROJECT2 or drawing commands in 2D script |
| Compile OK, ArchiCAD error | Parameter referenced in script not in paramlist |

---

## 9. Full Minimal Example

```gdl
! 3D Script — simple shelf unit
TOLER 0.001

! Pre-calculate
_innerW = A - 0.036    ! 2 × 18mm panels
_innerH = ZZYZX - 0.036

! Carcass
MATERIAL matBody
BLOCK A, B, 0.018                          ! bottom
ADDZ ZZYZX - 0.018
  BLOCK A, B, 0.018                        ! top
DEL 1
BLOCK 0.018, B, ZZYZX                      ! left
ADDX A - 0.018
  BLOCK 0.018, B, ZZYZX                    ! right
DEL 1

! Shelves
FOR i = 1 TO iShelves
  ADDZ 0.018 + (_innerH / (iShelves + 1)) * i
    ADDX 0.018
      BLOCK _innerW, B, 0.018
    DEL 1
  DEL 1
NEXT i

END
```
