---
type: concept
status: stable
tags: [stack, push, pop, transformation, add, del, mul]
aliases: [ADD, DEL, ADDX, ADDY, ADDZ, push pop, stack management]
source: raw/ccgdl_dev_doc/docs/GDL_01_Basics.md
---

# ADD_DEL

`ADD`/`DEL` is the fundamental push/pop pair for managing the [[Transformation_Stack]] in GDL. Together they define the scope of a local coordinate system: `ADD` pushes a new transform, all drawing commands between `ADD` and `DEL` operate in that local space, and `DEL` restores the previous coordinate system.

## Why ADD/DEL?

Without ADD/DEL, every piece of geometry would need world-space coordinates. With them, you can place a "leg" subroutine at `(0,0,0)`, then call it multiple times — each time wrapped in a different `ADD`/`DEL` — to create a table with four legs without recalculating any coordinates.

This is GDL's only composition mechanism (no object references, no scene graph). Mastering it is essential for writing maintainable code.

## Syntax

```gdl
ADD x, y, z        ! push translation by (x, y, z)
ADDX x             ! push X-axis translation only
ADDY y             ! push Y-axis translation only
ADDZ z             ! push Z-axis translation only
```

```gdl
DEL n              ! pop n transformations off the stack
```

## Basic Pattern

Every `ADD` must be matched by a `DEL`:

```gdl
! Stack depth = 1 (identity)

ADD 2, 0, 0         ! depth = 2
    BLOCK 1, 1, 1   ! draws at x=2
DEL 1               ! depth = 1
```

## Nested Transforms

```gdl
ADD 1, 0, 0
    ADD 0, 1, 0     ! combined: translation (1, 1, 0)
        BLOCK 1, 1, 1
    DEL 1           ! back to depth 2
DEL 1               ! back to depth 1
```

Pro tip: use `DEL n` to pop multiple levels at once:

```gdl
ADD 1, 0, 0
ADD 0, 1, 0
ROTZ 45
    BLOCK 1, 1, 1
DEL 3               ! clean up all three transforms at once
```

## Common Traps

### Stack overflow (forgetting DEL)

```gdl
FOR i = 1 TO 10
    ADD i * 0.1, 0, 0
    BLOCK 0.05, 0.05, 0.05
    ! missing DEL — stack grows every loop iteration!
NEXT i
```

This is the most common GDL bug. The stack has a limited depth (~256 on most ArchiCAD versions). Every iteration leaks one level, and the script crashes before the loop finishes.

### Double counting in loops

```gdl
FOR i = 1 TO 5
    ADD 0.2, 0, 0       ! push
        BLOCK 0.1, 0.1, 0.1
    DEL 1                ! pop
NEXT i                   ! OK — stack stable
```

Always pair inside the loop body, not outside.

### DEL count mismatch

If you push 3 transforms (`ADD`, `ROTZ`, `ADD`) but `DEL 2` instead of `DEL 3`, the leftover transform accumulates silently and shifts everything after it. Track your pushes with indentation:

```gdl
ADD x, y, z         ! 1
ROTZ angle          ! 2
ADD dx, dy, dz      ! 3
    BLOCK a, b, c
DEL 3               ! clear all three
```

## Related

- [[Transformation_Stack]] — the underlying mechanism
- [[BLOCK]] — common use case for ADD/DEL positioning
- [[FOR_NEXT]] — loop patterns with stack safety
