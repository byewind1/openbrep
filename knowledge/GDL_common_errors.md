# GDL 常见错误（20 种，Archicad 29）

> 目标：把高频编译/运行问题做成“可直接修复”的字典。
> 约定：说明中文，代码英文。

---

## 1. 多行 IF 缺少 ENDIF

**现象**：Archicad 报错 `ENDIF expected` 或 `Syntax error near IF block`。

**原因**：多行 `IF ... THEN` 代码块没有闭合。

**修复**：每个多行 `IF` 都补齐 `ENDIF`。

**示例**：错误代码 → 正确代码
```gdl
! wrong
IF has_back = 1 THEN
    BLOCK A, 0.02, back_h

! right
IF has_back = 1 THEN
    BLOCK A, 0.02, back_h
ENDIF
```

---

## 2. 单行 IF 误加 ENDIF

**现象**：Archicad 报错 `Unexpected ENDIF`。

**原因**：单行 `IF ... THEN statement` 本身不需要 `ENDIF`。

**修复**：删掉多余 `ENDIF`，或改成多行结构。

**示例**：错误代码 → 正确代码
```gdl
! wrong
IF A < 0.3 THEN A = 0.3
ENDIF

! right
IF A < 0.3 THEN A = 0.3
```

---

## 3. FOR 与 NEXT 不配对

**现象**：Archicad 报错 `NEXT expected` 或 `NEXT without FOR`。

**原因**：循环结构未闭合，或写了 `NEXT` 但没有对应 `FOR`。

**修复**：保证每个 `FOR` 有同层 `NEXT`。

**示例**：错误代码 → 正确代码
```gdl
! wrong
FOR i = 1 TO n
    BLOCK 0.1, 0.1, 0.1

! right
FOR i = 1 TO n
    BLOCK 0.1, 0.1, 0.1
NEXT i
```

---

## 4. WHILE 与 ENDWHILE 不配对

**现象**：Archicad 报错 `ENDWHILE expected`。

**原因**：`WHILE` 循环未正确结束。

**修复**：补齐 `ENDWHILE`，并确保循环条件可退出。

**示例**：错误代码 → 正确代码
```gdl
! wrong
WHILE i < 10
    i = i + 1

! right
WHILE i < 10
    i = i + 1
ENDWHILE
```

---

## 5. GOSUB 名称未加引号

**现象**：Archicad 报错 `Undefined label` 或 `Syntax error in GOSUB`。

**原因**：`GOSUB` 目标名缺少引号。

**修复**：`GOSUB "Label"`，并确保标签存在。

**示例**：错误代码 → 正确代码
```gdl
! wrong
GOSUB DrawLeg

! right
GOSUB "DrawLeg"

"DrawLeg":
    CYLIND 0.4, 0.02
RETURN
```

---

## 6. 子程序内误用 END

**现象**：编译通过但 3D 显示中断，后续几何不出现。

**原因**：子程序里写了 `END`，提前终止主脚本。

**修复**：子程序末尾用 `RETURN`，主流程末尾才用 `END`。

**示例**：错误代码 → 正确代码
```gdl
! wrong
"DrawSeat":
    BLOCK A, B, 0.03
END

! right
"DrawSeat":
    BLOCK A, B, 0.03
RETURN
```

---

## 7. 3D 主脚本缺少 END

**现象**：Archicad 报错 `END expected` 或脚本结构异常。

**原因**：3D 主流程没有终止语句。

**修复**：在主 3D 脚本末尾增加 `END`。

**示例**：错误代码 → 正确代码
```gdl
! wrong
BLOCK A, B, ZZYZX

! right
BLOCK A, B, ZZYZX
END
```

---

## 8. ADD/DEL 层数不平衡

**现象**：几何位置错乱、对象漂移，或运行期异常。

**原因**：变换入栈与出栈层数不一致。

**修复**：严格配平，每个执行路径都要平衡。

**示例**：错误代码 → 正确代码
```gdl
! wrong
ADDX 0.5
ADDY 0.2
BLOCK 0.1, 0.1, 0.1
DEL 1

! right
ADDX 0.5
ADDY 0.2
BLOCK 0.1, 0.1, 0.1
DEL 2
```

---

## 9. IF 分支内配平不一致

**现象**：只有某些参数状态下几何错位。

**原因**：`IF/ELSE` 两分支中 `ADD/DEL` 不对称。

**修复**：每个分支内部各自闭合，避免跨分支 `DEL`。

**示例**：错误代码 → 正确代码
```gdl
! wrong
IF has_back = 1 THEN
    ADDZ 0.3
    BLOCK A, 0.02, 0.4
ELSE
    BLOCK A, B, 0.02
ENDIF
DEL 1

! right
IF has_back = 1 THEN
    ADDZ 0.3
    BLOCK A, 0.02, 0.4
    DEL 1
ELSE
    BLOCK A, B, 0.02
ENDIF
```

---

## 10. PRISM_ 漏写高度参数

**现象**：Archicad 报错 `Wrong number of arguments`。

**原因**：`PRISM_` 第二参数高度 `h` 被省略。

**修复**：使用 `PRISM_ n, h, ...` 完整语法。

**示例**：错误代码 → 正确代码
```gdl
! wrong
PRISM_ 4, 0, 0, 15, A, 0, 15, A, B, 15, 0, B, 15

! right
PRISM_ 4, 0.02,
    0, 0, 15,
    A, 0, 15,
    A, B, 15,
    0, B, 15
```

---

## 11. PRISM 顶点数量与 n 不一致

**现象**：Archicad 报错 `Parameter count mismatch` 或几何畸形。

**原因**：`n` 与实际点数量不匹配。

**修复**：确保 `n` 与顶点对数量一致。

**示例**：错误代码 → 正确代码
```gdl
! wrong
PRISM 5, 0.02,
    0, 0,
    A, 0,
    A, B,
    0, B

! right
PRISM 4, 0.02,
    0, 0,
    A, 0,
    A, B,
    0, B
```

---

## 12. CYLIND 参数顺序写反

**现象**：圆柱像“薄片”或尺寸严重异常。

**原因**：把 `CYLIND h, r` 写成了 `CYLIND r, h`。

**修复**：确认第一个参数是高度，第二个是半径。

**示例**：错误代码 → 正确代码
```gdl
! wrong
CYLIND 0.02, 0.6

! right
CYLIND 0.6, 0.02
```

---

## 13. 参数名拼写漂移（未定义变量）

**现象**：Archicad 报错 `Undefined variable`。

**原因**：`paramlist.xml` 与脚本中的变量名不一致。

**修复**：统一参数命名，避免 `seatH/seat_h` 混用。

**示例**：错误代码 → 正确代码
```gdl
! wrong
IF seatH < 0.2 THEN seatH = 0.2
BLOCK A, B, seat_h

! right
IF seat_h < 0.2 THEN seat_h = 0.2
BLOCK A, B, seat_h
```

---

## 14. 布尔值使用 TRUE/FALSE

**现象**：参数约束或逻辑判断失效。

**原因**：GDL 布尔通常使用 `0/1`。

**修复**：统一为 `0/1`，并在参数脚本做兜底。

**示例**：错误代码 → 正确代码
```gdl
! wrong
VALUES "has_back" TRUE, FALSE

! right
VALUES "has_back" 0, 1
```

---

## 15. 除零错误

**现象**：Archicad 报错 `Division by zero` 或结果异常。

**原因**：分母变量可能为 0（例如数量参数）。

**修复**：分母前做保护分支。

**示例**：错误代码 → 正确代码
```gdl
! wrong
gap = H / n_shelves

! right
IF n_shelves < 1 THEN n_shelves = 1
gap = H / n_shelves
```

---

## 16. Parameter Script 使用视图上下文变量

**现象**：参数脚本行为异常或返回默认值。

**原因**：Parameter Script 中使用了不适用的全局变量。

**修复**：参数脚本只做参数校验/范围，不依赖视图上下文。

**示例**：错误代码 → 正确代码
```gdl
! wrong (parameter script)
IF GLOB_VIEW_TYPE = 3 THEN
    VALUES "A" RANGE [0.3, 2.0]
ENDIF

! right
VALUES "A" RANGE [0.3, 2.0]
```

---

## 17. 2D 脚本缺少 PROJECT2 或绘图命令

**现象**：平面图中对象不可见。

**原因**：2D 脚本为空或只有变量计算没有绘图。

**修复**：至少保留 `PROJECT2` 或手工 2D 图元。

**示例**：错误代码 → 正确代码
```gdl
! wrong
! 2D script has no drawing command

! right
PROJECT2 3, 270, 2
```

---

## 18. 材质参数类型不正确

**现象**：编译时材质设置报错或渲染异常。

**原因**：将材质值当字符串使用，参数类型不匹配。

**修复**：`Material` 参数存整数索引，脚本按参数设置。

**示例**：错误代码 → 正确代码
```gdl
! wrong
MATERIAL "Wood - Oak"

! right
MATERIAL mat_body
```

---

## 19. 循环边界 off-by-one

**现象**：多一块或少一块几何。

**原因**：`FOR i=0 TO n` 与 `FOR i=1 TO n` 语义混用。

**修复**：固定一种循环约定，并对应计算位置。

**示例**：错误代码 → 正确代码
```gdl
! wrong
FOR i = 1 TO n
    ADDZ i * gap
    BLOCK A, B, t
    DEL 1
NEXT i

! right
FOR i = 0 TO n - 1
    ADDZ (i + 1) * gap
    BLOCK A, B, t
    DEL 1
NEXT i
```

---

## 20. Markdown 反引号混入 GDL 脚本

**现象**：Archicad 报错 `Unexpected token`，定位到 ``` 行。

**原因**：从 AI 回复复制代码时把 Markdown fence 一起复制。

**修复**：删除所有 ``` 行，仅保留纯 GDL 代码。

**示例**：错误代码 → 正确代码
```gdl
! wrong
```gdl
BLOCK A, B, ZZYZX
END
```

! right
BLOCK A, B, ZZYZX
END
```
