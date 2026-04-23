---
type: concept
status: stable
tags: [control-flow, conditional, branch, if, endif, elsif, else]
aliases: [IF, ENDIF, ELSIF, ELSE, conditional, branch, gdl if]
source: raw/ccgdl_dev_doc/docs/GDL_03_Attributes.md
---

# IF_ENDIF

`IF`/`ENDIF` is GDL's conditional branching construct. It evaluates a numeric expression and executes the corresponding block when the result is non-zero (true).

## Why Conditionals?

Parametric objects need to adapt. A window might have 2 or 3 panels depending on a parameter. A chair might or might not have armrests. `IF`/`ENDIF` lets a single GDL object produce multiple variants from one set of parameters.

Unlike general-purpose languages, GDL conditionals evaluate to **numeric true/false** (0 = false, anything else = true). There is no boolean type — every expression resolves to a number.

## Syntax

```gdl
IF expression THEN
    ! code to execute when expression ≠ 0
ENDIF
```

With alternatives:

```gdl
IF expression THEN
    ! expression ≠ 0
ELSE
    ! expression = 0
ENDIF
```

Chained:

```gdl
IF expr1 THEN
    ! expr1 ≠ 0
ELSIF expr2 THEN
    ! expr1 = 0 and expr2 ≠ 0
ELSE
    ! both = 0
ENDIF
```

## Examples

### Parameter-based visibility

```gdl
! Show armrests only when the parameter says so
IF hasArms THEN
    ADD 0, 0, seatH
    BLOCK A, 0.05, 0.15
    DEL 1
ENDIF
```

### Multiple cases

```gdl
IF nPanels = 1 THEN
    GOSUB "DrawSinglePanel"
ELSIF nPanels = 2 THEN
    GOSUB "DrawDoublePanel"
ELSE
    GOSUB "DrawTriplePanel"
ENDIF
```

### Numeric comparison idiom

```gdl
! GDL has no < or > operators — use subtraction
IF A - B THEN       ! equivalent to "A > B" (when A - B > 0)
    BLOCK 1, 1, 1
ENDIF
```

### Combined conditions

```gdl
! Combining with arithmetic (no logical AND/OR)
IF hasArms + hasBack THEN       ! 0 = neither, 1 = one, 2 = both
    ! at least one of them is true
ENDIF
```

## Edge Cases & Traps

- **No boolean type**: GDL treats 0 as false, any non-zero as true. `IF A = B THEN` works because `= ` returns 0 or 1.
- **No `<` or `>` operators**: use subtraction: `IF A - B THEN` (true when A > B). Or use `IF A = B THEN` for equality.
- **ELSIF vs ELSEIF**: the keyword is `ELSIF` (one word, no 'E'), not `ELSEIF`.
- **ENDIF, not END IF**: it's a single keyword.
- **THEN is required** on the `IF` line.
- **Performance**: conditionals in the 3D script are evaluated every time the view refreshes. Keep heavy computation in the Master script or Parameter script.
- **Nesting**: GDL supports nesting IF/ENDIF up to reasonable depths (tested to ~50 levels). Use indentation to maintain readability:

```gdl
IF cond1 THEN
    IF cond2 THEN
        IF cond3 THEN
            ! deep conditional
        ENDIF
    ENDIF
ENDIF
```

## Related

- [[FOR_NEXT]] — iteration (the loop counterpart)
- [[ADD_DEL]] — conditional geometry positioning
- [[PRISM_]] — typically wrapped in conditionals
