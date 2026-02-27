# GDL 常用 3D 命令参考（Archicad 29）

> 目标：给 LLM 和开发者一个“高命中、低歧义”的 3D 命令速查。
> 约定：说明用中文；代码示例用英文 GDL。

---

## 0. 通用规则（先看）

1. **先校验参数，再绘制几何**。
2. **所有变换命令都要配平**：`ADD/ROT/MUL/XFORM` 入栈，必须 `DEL` 出栈。
3. **主 3D 脚本最后必须有 `END`**。
4. `A/B/ZZYZX` 作为宽/深/高主参数，尽量保持语义稳定。
5. 能用简单命令（`BLOCK`）就不要先上复杂命令（`PRISM_`）。

---

## 1. 基础实体命令

### 1.1 BLOCK

**语法**
```gdl
BLOCK a, b, c
```

**说明**
- 生成长方体，沿当前坐标系正方向延伸。
- `a/b/c` 分别对应 X/Y/Z 尺寸。

**示例**
```gdl
BLOCK A, B, ZZYZX
```

**常见坑**
- 参数写反导致方向和尺寸错位。

---

### 1.2 BRICK

**语法**
```gdl
BRICK a, b, c
```

**说明**
- 与 `BLOCK` 等价，保留写法。

**示例**
```gdl
BRICK 0.6, 0.4, 0.75
```

---

### 1.3 CYLIND

**语法**
```gdl
CYLIND h, r
```

**说明**
- 沿当前 Z 轴生成圆柱。
- 高度在前，半径在后。

**示例**
```gdl
CYLIND leg_h, leg_r
```

**常见坑**
- 把参数写成 `CYLIND r, h`。

---

### 1.4 CONE

**语法**
```gdl
CONE h, r1, r2, alpha1, alpha2
```

**说明**
- 圆锥/圆台体。
- `r1` 底半径，`r2` 顶半径。

**示例**
```gdl
CONE 0.3, 0.12, 0.06, 90, 90
```

---

### 1.5 SPHERE

**语法**
```gdl
SPHERE r
```

**说明**
- 在当前原点生成球体。

**示例**
```gdl
SPHERE 0.05
```

---

### 1.6 ELLIPS

**语法**
```gdl
ELLIPS h, r
```

**说明**
- 生成半椭球类实体（常用于端头/装饰）。

**示例**
```gdl
ELLIPS 0.08, 0.04
```

---

### 1.7 PRISM

**语法**
```gdl
PRISM n, h, x1, y1, x2, y2, ..., xn, yn
```

**说明**
- 任意多边形截面拉伸。
- `n` 与点对数量必须一致，且 `n >= 3`。

**示例**
```gdl
PRISM 4, 0.02,
    0, 0,
    A, 0,
    A, B,
    0, B
```

---

### 1.8 PRISM_

**语法**
```gdl
PRISM_ n, h, x1, y1, s1, x2, y2, s2, ..., xn, yn, sn
```

**说明**
- 带边/面状态控制的棱柱。
- 第二个参数 `h` 必须存在。

**示例**
```gdl
PRISM_ 4, 0.02,
    0, 0, 15,
    A, 0, 15,
    A, B, 15,
    0, B, 15
```

**常见坑**
- 忘记 `h`，触发参数个数错误。

---

### 1.9 CPRISM_

**语法**
```gdl
CPRISM_ top_mat, bottom_mat, side_mat, n, h, ...
```

**说明**
- 顶/底/侧材质可分离控制。

**示例**
```gdl
CPRISM_ matTop, matBottom, matSide, 4, 0.03,
    0, 0, 15,
    A, 0, 15,
    A, B, 15,
    0, B, 15
```

---

### 1.10 TUBE

**语法（常见形式）**
```gdl
TUBE n, m, mask, ...
```

**说明**
- 管状/沿路径截面命令，适合扶手、管件。
- 入门阶段建议先用 `CYLIND` 和分段变换替代。

---

## 2. 坐标变换命令（必须配平）

### 2.1 ADD / ADDX / ADDY / ADDZ

**语法**
```gdl
ADD x, y, z
ADDX x
ADDY y
ADDZ z
```

**说明**
- 平移当前坐标系。
- 每条命令都会压入 1 层变换栈。

**示例**
```gdl
ADDX 0.5
ADDY 0.2
BLOCK 0.1, 0.1, 0.8
DEL 2
```

---

### 2.2 ROT / ROTX / ROTY / ROTZ

**语法**
```gdl
ROT angle
ROTX angle
ROTY angle
ROTZ angle
```

**说明**
- 旋转变换，角度单位是度。

**示例**
```gdl
ROTZ 90
BLOCK 0.2, 0.4, 0.02
DEL 1
```

---

### 2.3 MUL / MULX / MULY / MULZ

**语法**
```gdl
MUL x, y, z
MULX x
MULY y
MULZ z
```

**说明**
- 缩放当前坐标系。

**示例**
```gdl
MUL 1, 1, 0.5
CYLIND 1, 0.05
DEL 1
```

---

### 2.4 XFORM

**语法**
```gdl
XFORM a, b, c, d, e, f, g, h, i, j, k, l
```

**说明**
- 直接设置 3x4 变换矩阵，高级场景使用。

**建议**
- 非必要不用；优先 `ADD/ROT/MUL`，更可读更稳。

---

### 2.5 DEL / DEL TOP / NTR

**语法**
```gdl
DEL n
DEL TOP
n = NTR()
```

**说明**
- `DEL n`：弹出 n 层变换。
- `DEL TOP`：弹到栈顶（慎用）。
- `NTR()`：当前变换栈深度。

**示例**
```gdl
base_ntr = NTR()
ADDX 1
ADDY 2
BLOCK 0.1, 0.1, 0.1
DEL NTR() - base_ntr
```

---

## 3. 外观与细分控制

### 3.1 MATERIAL

**语法**
```gdl
MATERIAL matIndex
```

**说明**
- 设置后续几何的材质。

**示例**
```gdl
MATERIAL matBody
BLOCK A, B, 0.02
```

---

### 3.2 PEN

**语法**
```gdl
PEN penIndex
```

**说明**
- 控制边线笔号。

---

### 3.3 RESOL / TOLER

**语法**
```gdl
RESOL n
TOLER tol
```

**说明**
- `RESOL`：圆弧分段数。
- `TOLER`：容差控制（通常更实用）。

**示例**
```gdl
IF GLOB_SCALE > 100 THEN
    RESOL 12
ELSE
    RESOL 36
ENDIF
```

---

### 3.4 MODEL / SHADOW

**语法**
```gdl
MODEL mode
SHADOW onOff
```

**说明**
- 控制模型显示模式与阴影行为。

---

## 4. 结构控制（3D 中高频）

### 4.1 IF / ENDIF

```gdl
IF has_back = 1 THEN
    BLOCK A, 0.02, back_h
ENDIF
```

- 多行 `IF` 必须有 `ENDIF`。

---

### 4.2 FOR / NEXT

```gdl
FOR i = 1 TO n
    ADDZ i * gap
    BLOCK A, B, 0.02
    DEL 1
NEXT i
```

- `FOR` 与 `NEXT` 必须成对。

---

### 4.3 GOSUB / RETURN

```gdl
GOSUB "DrawLeg"

"DrawLeg":
    CYLIND leg_h, leg_r
RETURN
```

- 子程序内结束必须 `RETURN`，不要写 `END`。

---

## 5. 最小可编译 3D 模板（推荐给 LLM）

```gdl
! scripts/3d.gdl
! Archicad 29

! ---- validate derived values are prepared in 1d.gdl ----
IF A <= 0 THEN A = 0.6
IF B <= 0 THEN B = 0.6
IF ZZYZX <= 0 THEN ZZYZX = 0.75

TOLER 0.001
MATERIAL matBody

! main volume
BLOCK A, B, ZZYZX

END
```

---

## 6. 编译前检查清单

- [ ] 3D 脚本末尾有 `END`
- [ ] `ADD/ROT/MUL/XFORM` 与 `DEL` 配平
- [ ] `IF/ENDIF`、`FOR/NEXT`、`GOSUB/RETURN` 成对
- [ ] `PRISM_` 第二参数 `h` 未遗漏
- [ ] 参数名与 `paramlist.xml` 一致（无拼写漂移）
