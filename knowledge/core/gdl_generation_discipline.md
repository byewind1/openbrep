---
id: core.generation_discipline
type: core
task_types: [create, image, modify]
priority: 92
---

# GDL 生成纪律（硬规则）

以下四条是交付门槛，违反任何一条都属于未完成。每条附最小正确/错误对照。

## 规则 1：2D 脚本必须非空

每个对象都必须产出有效的 2d.gdl。首选 `PROJECT2` 投影；需要符号化表达时
用手工 2D 图元（RECT2 / LINE2 / POLY2 / CIRCLE2 / ARC2）。空 2d.gdl = 交付失败。

```gdl
! ✅ 正确：一行投影即可
PROJECT2 3, 270, 2
```

```gdl
! ✅ 正确：手工符号（以书架为例）
RECT2 0, 0, A, B
FOR i = 1 TO n_shelves
  LINE2 0, i * (B / (n_shelves + 1)), A, i * (B / (n_shelves + 1))
NEXT i
```

```gdl
! ❌ 错误：2d.gdl 留空或只写注释
! （没有任何绘图命令）
```

## 规则 2：变换栈必须配平

每个 `ADD / ADDX / ADDY / ADDZ / ROTX / ROTY / ROTZ / MUL / MULX / MULY / MULZ`
压入一层变换，必须弹出对应层数。脚本结束时栈必须为空。

- 局部变换：`DEL n` 精确弹出刚压入的 n 层。
- 循环体内：每轮迭代结束弹平本轮压入的层。
- 不确定深度时用 `NTR()` 记账，不要凭感觉写 DEL 数字。

```gdl
! ✅ 正确：压几层弹几层
ADDX A/2
ADDZ thickness
BLOCK shelf_w, B, thickness
DEL 2
```

```gdl
! ✅ 正确：NTR 记账（嵌套/循环里更稳）
base_ntr = NTR()
FOR i = 1 TO n_shelves
  ADDZ i * spacing
  BLOCK shelf_w, B, thickness
  DEL NTR() - base_ntr
NEXT i
```

```gdl
! ❌ 错误：压 3 层只弹 1 层（栈泄漏，后续几何全部错位）
ADDX 0.1
ADDY 0.2
ROTZ 30
BLOCK 1, 1, 1
DEL 1
```

自查：数一遍脚本里变换命令总数与 DEL 弹出总数，二者必须相等。

## 规则 3：重复几何必须用 FOR/NEXT 循环

层板、立杆、螺栓孔、分格、踏步等重复元素，一律用参数驱动的 FOR/NEXT，
禁止手工展开重复体（不可参数化 = 不是参数化构件）。

```gdl
! ✅ 正确：参数驱动循环
FOR i = 1 TO hole_count
  ADDX (i - 1) * hole_spacing
  CYLIND thickness, hole_d / 2
  DEL 1
NEXT i
```

```gdl
! ❌ 错误：手工展开（改 hole_count 不会生效）
ADDX 0
CYLIND thickness, hole_d / 2
DEL 1
ADDX 0.2
CYLIND thickness, hole_d / 2
DEL 1
```

## 规则 4：声明的参数必须驱动几何

paramlist 里声明的每个数值/布尔参数，必须至少被 3D 脚本（或经 Master
派生后被 3D）实际引用一次。声明了不接线的参数 = 没做，必须删掉或接上。

```gdl
! ✅ 正确：post_spacing 决定立杆数量与位置
n_posts = INT(A / post_spacing) + 1
FOR i = 1 TO n_posts
  ADDX (i - 1) * post_spacing
  CYLIND ZZYZX, post_d / 2
  DEL 1
NEXT i
```

```gdl
! ❌ 错误：paramlist 声明了 blade_angle，3D 里写死 30
! （改 blade_angle 几何毫无变化）
ROTX 30
```

交付前自查清单：

- [ ] 2d.gdl 非空且有 PROJECT2 或 2D 图元
- [ ] 变换命令总数 == DEL 弹出总数
- [ ] 重复元素走 FOR/NEXT 且循环次数由参数驱动
- [ ] paramlist 每个参数都在脚本里被引用
