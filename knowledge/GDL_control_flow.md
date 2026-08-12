# GDL Control Flow

## IF / THEN / ENDIF

Multi-line IF block (always need ENDIF):
```gdl
IF condition THEN
    statement1
    statement2
ENDIF
```

Single-line IF (no ENDIF needed):
```gdl
IF A < 0.5 THEN A = 0.5
```

IF/ELSE:
```gdl
IF bHasBack THEN
    BLOCK A, 0.02, backH
ELSE
    ! No back
ENDIF
```

## FOR / NEXT

Loop from start to end:
```gdl
FOR i = 1 TO nLegs
    ADD i * legSpacing, 0, 0
    BLOCK legW, legD, legH
    DEL 1
NEXT i
```

WITH STEP:
```gdl
FOR i = 0 TO 10 STEP 2
    ! i: 0, 2, 4, 6, 8, 10
NEXT i
```

## WHILE / ENDWHILE

```gdl
WHILE x < A
    BLOCK 0.1, 0.1, 0.1
    x = x + 0.1
ENDWHILE
```

## GOSUB / RETURN (Subroutines)

Call subroutine:
```gdl
GOSUB "DrawLegs"
GOSUB "DrawSeat"
END

"DrawLegs":
    BLOCK legW, legD, legH
RETURN

"DrawSeat":
    BLOCK A, B, seatH
RETURN
```

## Key Rules

- **EVERY multi-line IF needs ENDIF** (except single-line IF THEN)
- **EVERY FOR needs NEXT**
- **EVERY WHILE needs ENDWHILE**
- Subroutine names must be in quotes: `GOSUB "Name"`
- Subroutine must end with `RETURN`
- Main script ends with `END`
- **`NOT` 是函数不是运算符**：必须写 `NOT (x)`，裸 `NOT x` 会报 `Missing parameter(s) after function`。对 0/1 布尔参数更稳的写法是 `IF show_in_3d = 0 THEN ...`
- **单行 IF 同行只允许一条条件语句**：`IF c THEN s1 : s2` 中 `:` 后的 `s2` **无条件执行**（Archicad 语义：THEN 同行一条命令即隐含 ENDIF）。多条语句必须写成多行 `IF ... ENDIF` 块
