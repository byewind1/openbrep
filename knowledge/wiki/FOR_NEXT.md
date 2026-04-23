---
type: concept
status: stable
tags: [loop, iteration, for, next, control-flow, repetition]
aliases: [FOR, NEXT, for loop, loop, iteration, gdl for]
source: raw/ccgdl_dev_doc/docs/GDL_03_Attributes.md
---

# FOR_NEXT

`FOR`/`NEXT` is GDL's looping construct. It repeats a block of code with a counter variable stepping through a numeric range.

## Why Loops?

Repetitive geometry — stair steps, balusters, louver slats, arrayed columns — is impractical to write vertex-by-vertex. `FOR`/`NEXT` lets you generate any number of repeated elements from a compact description.

## Syntax

```gdl
FOR var = start TO end [STEP increment]
    ! repeated code block
NEXT var
```

| Part      | Description                                    |
|-----------|------------------------------------------------|
| `var`     | Counter variable name (any valid GDL name)     |
| `start`   | Starting value (numeric expression)            |
| `end`     | Ending value (numeric expression)              |
| `STEP`    | Optional step; defaults to +1 (or -1 if end < start) |

## Examples

### Simple repetition

```gdl
! Draw 5 balusters spaced 0.4m apart
FOR i = 1 TO 5
    ADD (i - 1) * 0.4, 0, 0
    BLOCK 0.05, 0.05, 0.9
    DEL 1
NEXT i
```

### Descending loop with STEP

```gdl
! From top to bottom
FOR row = 10 TO 1 STEP -1
    ADD 0, 0, row * 0.2
    BLOCK 0.3, 0.3, 0.02
    DEL 1
NEXT row
```

### Nested loops

```gdl
! 3×4 grid of small blocks
FOR col = 1 TO 4
    FOR row = 1 TO 3
        ADD (col - 1) * 0.3, (row - 1) * 0.3, 0
        BLOCK 0.2, 0.2, 0.2
        DEL 1
    NEXT row
NEXT col
```

## Critical: Stack Safety in Loops

Every `ADD`/`ROT`/`MUL` inside a loop body **must** have a matching `DEL` — also inside the loop body. This is the most common GDL bug.

### ❌ Wrong — stack grows every iteration

```gdl
FOR i = 1 TO 10
    ADD i * 0.1, 0, 0
    BLOCK 0.05, 0.05, 0.05
    ! DEL is missing — stack grows by 1 each loop!
NEXT i
```

### ✅ Correct — stack balanced

```gdl
FOR i = 1 TO 10
    ADD i * 0.1, 0, 0
        BLOCK 0.05, 0.05, 0.05
    DEL 1              ! pop inside the loop
NEXT i
```

### ✅ Using indentation to spot imbalance

```gdl
FOR i = 1 TO 10
    ADD 0, 0, i * 0.1
        BLOCK 0.3, 0.3, 0.02
    DEL 1               ! ← if this line were missing, indentation makes it obvious
NEXT i
```

## Loop Variables and Parameters

Loop variables are regular GDL numeric variables. They persist after the loop ends:

```gdl
FOR i = 1 TO 5
    ! ...
NEXT i
! i = 6 here (first value past the end)
```

You can use parameters as bounds:

```gdl
nLegs = 4
FOR leg = 1 TO nLegs
    GOSUB "DrawLeg"
NEXT leg
```

## Edge Cases & Traps

- **STEP defaults**: if `start > end` and no STEP is given, the loop auto-detects and steps by -1. But STEP direction must match: if `start < end` and `STEP -1`, the loop body never executes.
- **Empty loop**: if `start = end`, the loop executes once.
- **Modifying the counter**: avoid modifying the loop variable inside the body — behavior is undefined.
- **Performance**: loops in the 3D script run on every view refresh. For high counts (>100), consider if the geometry can be simplified.
- **NEXT variable**: the variable after `NEXT` is optional in some GDL versions, but always writing it (e.g., `NEXT i`) catches mismatches.

## Related

- [[IF_ENDIF]] — conditional branching (the decision counterpart)
- [[ADD_DEL]] — stack management essential for loops
- [[BLOCK]] — simple geometry to place in loops
