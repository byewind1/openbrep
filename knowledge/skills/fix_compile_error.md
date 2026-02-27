# Skill: fix_compile_error（Archicad 29）

> 目标：把“报错 -> 定位 -> 最小修复 -> 回归验证”流程标准化，降低反复试错。
> 约定：说明中文，代码英文。

---

## 1) 修复总流程（四步）

1. **分类错误**：结构/语义/参数/2D-3D一致性
2. **最小改动**：只改导致报错的最小代码块
3. **二次校验**：防止修一处坏一处
4. **回归检查**：至少验证 3 组参数（小/中/大）

---

## 2) 报错分流字典（高频）

### A. 结构类

- `ENDIF expected` → 缺 `ENDIF`
- `NEXT expected` → 缺 `NEXT`
- `Unexpected ENDIF` → 单行 IF 误加 ENDIF
- `END expected` → 主 3D 缺 END

### B. 参数类

- `Wrong number of arguments` → 命令参数数量不对
- `Undefined variable` → 参数名或派生变量拼写漂移
- `Division by zero` → 分母未保护

### C. 运行类（编译过但显示错）

- 几何整体错位 → `ADD/DEL` 不平衡
- 局部缺失 → 子程序 `RETURN` 错误或被提前 `END`
- 2D 不显示 → 缺 `PROJECT2`/绘图命令

---

## 3) 最小修复策略（按优先级）

### 3.1 先修结构闭合

```gdl
! before
IF has_back = 1 THEN
    BLOCK A, 0.02, back_h

! after
IF has_back = 1 THEN
    BLOCK A, 0.02, back_h
ENDIF
```

### 3.2 再修命令参数

```gdl
! before
PRISM_ 4, 0, 0, 15, A, 0, 15, A, B, 15, 0, B, 15

! after
PRISM_ 4, 0.02,
    0, 0, 15,
    A, 0, 15,
    A, B, 15,
    0, B, 15
```

### 3.3 再修变量与参数映射

```gdl
! before
BLOCK A, B, seatH

! after
BLOCK A, B, seat_h
```

### 3.4 最后修变换平衡

```gdl
! before
ADDX 0.5
ADDY 0.2
BLOCK 0.1, 0.1, 0.1
DEL 1

! after
ADDX 0.5
ADDY 0.2
BLOCK 0.1, 0.1, 0.1
DEL 2
```

---

## 4) 快速检查脚本（人工执行顺序）

1. 查 `IF/ENDIF` 是否数量匹配
2. 查 `FOR/NEXT` 是否数量匹配
3. 查 `GOSUB` 是否都有对应标签与 `RETURN`
4. 查 `ADD/ROT/MUL` 与 `DEL` 是否配平
5. 查每个命令参数数量是否正确
6. 查 `paramlist.xml` 与脚本变量名是否一致
7. 查 2D 是否有 `PROJECT2` 或绘图图元

---

## 5) Debug 输出策略（低侵入）

只在必要时加入可开关调试，不要永久污染脚本。

```gdl
! scripts/1d.gdl
debug_mode = 0

IF debug_mode = 1 THEN
    PRINT "A=", A, " B=", B, " ZZYZX=", ZZYZX
    PRINT "n_shelves=", n_shelves
ENDIF
```

---

## 6) 修复后回归模板

### 6.1 参数回归

- Case-S: 最小边界
- Case-M: 默认值
- Case-L: 大尺寸上界

### 6.2 视图回归

- 3D：主体显示与位置正确
- 2D：投影与热点存在

### 6.3 结构回归

- 新增修复没有引入新的闭合错误
- 变换栈无泄漏

---

## 7) 禁止的修复方式

- 为了“暂时过编译”而删除大量功能代码
- 一次性重写整份脚本（高回归风险）
- 忽略参数层约束，直接在 3D 硬编码补丁

---

## 8) 标准输出（用于 AI 回答）

修复结果应按以下结构给出：

1. **错误定位**（文件+片段）
2. **根因解释**（一句话）
3. **最小补丁**（before/after）
4. **回归结论**（检查项）
