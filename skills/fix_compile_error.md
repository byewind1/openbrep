# Skill: Debug & Fix GDL Compile / Runtime Errors

Use this when the user reports a compile error, syntax warning, or "compiled OK but ArchiCAD shows nothing".

---

## Diagnostic Decision Tree

```
Compile FAILED?
  ├─ "unexpected token" / "syntax error"
  │    → Check: markdown ``` fences in code, ENDIF missing, NEXT missing, RETURN vs END
  ├─ "undefined variable"
  │    → Check: _var not calculated, parameter missing from paramlist
  └─ "wrong number of arguments"
       → Check: PRISM_ vertex count, CYLIND/BLOCK argument order

Compile OK but nothing in ArchiCAD 3D?
  ├─ Check: _var used but never assigned (top of script)
  ├─ Check: ADD/DEL imbalance (runtime crash, silent)
  ├─ Check: MATERIAL string not in library
  └─ Check: END inside subroutine instead of RETURN

2D view empty?
  └─ Check: no PROJECT2 / RECT2 / LINE2 in 2D script
```

---

## Fix Patterns

### Markdown fence contamination
**Symptom:** Syntax error on line with ` ``` `
**Fix:** Remove all lines containing only backticks. They are AI formatting artifacts, not GDL.

### ADD/DEL imbalance
**Symptom:** Geometry partially wrong or nothing shown
**Fix:** Count every ADD, ADDX, ADDY, ADDZ, ROTZ, ROTX, ROTY, MUL — sum must equal sum of all DEL values.
```gdl
! Wrong:
ADDX 0.5
ADDY 0.3
  BLOCK 0.1, 0.1, 0.1
DEL 1     ! only pops 1 of 2 — ADDY still on stack

! Fixed:
ADDX 0.5
ADDY 0.3
  BLOCK 0.1, 0.1, 0.1
DEL 2     ! pops both
```

### Subroutine ends with END instead of RETURN
**Symptom:** Only first N subroutines display, rest invisible
**Fix:** Replace `END` with `RETURN` in every subroutine body. Only the main body's terminal statement is `END`.

### _var undefined
**Symptom:** Compiles OK, ArchiCAD shows nothing, no error message
**Fix:** Add assignments at top of 3D script before any geometry:
```gdl
! Add these before first geometry command:
_insideW = A - 2 * frameW
_insideD = B - 2 * frameW
_insideH = ZZYZX - 2 * frameW
```

### CYLIND arguments swapped
**Symptom:** Rod/pipe is fat disk instead of thin cylinder
**Fix:** `CYLIND height, radius` — height first, radius second.
```gdl
! Wrong: CYLIND 0.015, _insideD   (height=15mm, radius=depth)
! Fixed: CYLIND _insideD, 0.015   (height=depth, radius=15mm)
```

### PRISM_ wrong vertex count
**Symptom:** Degenerate face warning or unexpected shape
**Fix:** Count unique vertices only, do NOT repeat first point.
```gdl
! Wrong:  PRISM_ 5, h, 0,0, A,0, A,B, 0,B, 0,0  (5th = 1st, degenerate)
! Fixed:  PRISM_ 4, h, 0,0, A,0, A,B, 0,B        (4 unique points)
! Or use: BLOCK A, B, h                            (simpler for rectangles)
```

### Missing PROJECT2 in 2D script
**Symptom:** Floor plan shows empty symbol box
**Fix:** Add at minimum:
```gdl
PROJECT2 3, 270, 2
```

### IF without ENDIF
**Symptom:** Compile error "ENDIF expected"
**Fix:** Multi-line IF always needs ENDIF. Single-line IF does not.
```gdl
! Multi-line — needs ENDIF:
IF bDoors THEN
  GOSUB "DrawDoors"
ENDIF

! Single-line — no ENDIF:
IF bDoors THEN GOSUB "DrawDoors"
```
