---
type: concept
status: stable
tags: [transform, stack, add, del, mul, rot, coordinate-system]
aliases: [transformation stack, transform stack, gdl transform, coordinate stack]
source: raw/ccgdl_dev_doc/docs/GDL_01_Basics.md
---

# Transformation_Stack

The transformation stack is GDL's mechanism for positioning, rotating, and scaling geometry. It acts like a push-down stack of 4×4 transformation matrices — every `ADD`, `ROT`, `MUL` pushes a new transformation onto the stack, and `DEL` pops them off.

## Why the Stack?

GDL has no object references or scene graph. Every geometric command (`BLOCK`, `PRISM_`, etc.) draws at the origin of the current coordinate system. To place geometry elsewhere, you transform the coordinate system itself. The stack structure lets you:

- Nest transformations hierarchically (a room → furniture → legs)
- Reuse the same coordinates for different contexts
- Apply transformations temporarily and revert cleanly

## Core Commands

### ADD — Translate

```gdl
ADD x, y, z        ! push translation
ADDX x             ! translate X only
ADDY y             ! translate Y only
ADDZ z             ! translate Z only
```

### ROT — Rotate

```gdl
ROT angle          ! rotate around Z axis
ROTX angle         ! rotate around X
ROTY angle         ! rotate around Y
ROTZ angle         ! rotate around Z (same as ROT)
```

All rotation angles are in degrees, counter-clockwise.

### MUL — Scale / Custom Matrix

```gdl
MUL arr            ! apply a 3×3 or 4×4 matrix
MUL sx, sy, sz     ! scale factors
```

### DEL — Pop

```gdl
DEL n              ! pop the top n transformations off the stack
```

## Stack Depth Management

The stack depth defaults to 1 (the identity matrix). Each `ADD`, `ROT`, or `MUL` pushes one level. Each `DEL n` pops `n` levels.

```gdl
! Beginning of 3D script — stack depth = 1

ADD 1, 0, 0        ! depth = 2 (translates to x=1)
    BLOCK 0.5, 0.5, 0.5
    ADD 0, 1, 0    ! depth = 3
        BLOCK 0.3, 0.3, 0.3
    DEL 1           ! depth = 2
DEL 1               ! depth = 1
```

## Common Mistakes

### Missing DEL

```gdl
ADD 0, 1, 0
BLOCK 1, 1, 1
! no DEL — stack depth leaks!
```

Every `ADD`/`ROT`/`MUL` without a matching `DEL` causes the stack to grow unbounded. ArchiCAD will either crash or produce garbled geometry when the stack overflows.

### Wrong DEL count

```gdl
ADD 1, 0, 0
ADD 0, 1, 0
BLOCK 1, 1, 1
DEL 1               ! only pops one — stack stays at depth 3
```

Always track how many transforms you've pushed. Use `DEL 2` to pop both, or better:

```gdl
ADD 1, 0, 0
ADD 0, 1, 0
    BLOCK 1, 1, 1
DEL 2
```

### Transforming after geometry

```gdl
BLOCK 1, 1, 1
ADD 2, 0, 0        ! too late — BLOCK already drew at origin
```

Apply the transform first, then draw.

## Best Practice: Group transforms

```gdl
ADD x, y, z
ROTZ angle
    ! --- group content ---
    BLOCK a, b, c
    PRISM_ 4, h, x1, y1, s1, x2, y2, s2, x3, y3, s3, x4, y4, s4
    ! --- end group ---
DEL 2
```

Aligning `ADD`/`DEL` pairs vertically by indentation makes the stack depth visually obvious. This is the most important readability pattern in GDL.

## Related

- [[ADD_DEL]] — detailed reference on push/pop patterns
- [[BLOCK]] — drawing at the current stack position
- [[PRISM_]] — drawing at the current stack position
