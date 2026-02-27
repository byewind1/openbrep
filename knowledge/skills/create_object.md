# Skill: create_object（Archicad 29）

> 目标：把“从需求到可编译对象”流程固定为可复用策略，提升一次生成成功率。
> 约定：说明中文，代码英文。

---

## 1) 参数先行（Parameter First）

先定义参数，再写任何几何。参数层必须覆盖：

1. 尺寸主参数：`A`, `B`, `ZZYZX`
2. 功能参数：数量、开关、角度、材质
3. 约束规则：最小值/最大值/联动关系

### 1.1 参数命名规则

- `A/B/ZZYZX`：宽/深/高（保留语义）
- `n_`：整数数量（如 `n_shelves`）
- `has_`：布尔开关（0/1）
- `_thk/_h/_w/_d`：尺寸后缀
- `mat_`：材质参数

### 1.2 参数最小模板

```xml
<Length Name="A"><Fix/><Value>0.60</Value></Length>
<Length Name="B"><Fix/><Value>0.40</Value></Length>
<Length Name="ZZYZX"><Fix/><Value>0.75</Value></Length>
<Integer Name="n_parts"><Value>4</Value></Integer>
<Boolean Name="has_back"><Value>1</Value></Boolean>
<Material Name="mat_body"><Value>0</Value></Material>
```

### 1.3 参数阶段检查

- [ ] 是否有 A/B/ZZYZX
- [ ] 布尔参数是否用 0/1
- [ ] 数量参数是否有下限保护
- [ ] 参数名是否和后续脚本一致

---

## 2) Master 预计算（scripts/1d.gdl）

Master 只做三件事：
1. 参数兜底
2. 派生变量计算
3. 调试输出开关（可选）

### 2.1 推荐写法

```gdl
! scripts/1d.gdl
IF A < 0.30 THEN A = 0.30
IF B < 0.30 THEN B = 0.30
IF ZZYZX < 0.30 THEN ZZYZX = 0.30

IF n_shelves < 1 THEN n_shelves = 1

_panel_h = (ZZYZX - top_thk - bottom_thk) / n_shelves
IF _panel_h < 0.02 THEN _panel_h = 0.02
```

### 2.2 禁止事项

- 不在 Master 中堆大量几何命令
- 不在 Master 中写难维护的深层循环绘图

### 2.3 Master 阶段检查

- [ ] 所有除法分母已防 0
- [ ] 派生变量在 3D/2D 使用前都已定义
- [ ] 没有拼写漂移（如 `panel_h` vs `_panel_h`）

---

## 3) 3D 几何（scripts/3d.gdl）

3D 脚本的目标是：**先保证可编译，再逐步加细节**。

### 3.1 结构顺序

1. 细分/容差设置（可选）
2. 主体几何
3. 循环阵列构件
4. 可选构件（IF 开关）
5. `END`

### 3.2 稳定骨架

```gdl
! scripts/3d.gdl
TOLER 0.001
MATERIAL mat_body

! main body
BLOCK A, B, body_h

! repeated parts
FOR i = 1 TO n_parts
    ADDZ i * part_gap
    BLOCK A, B, part_thk
    DEL 1
NEXT i

! optional geometry
IF has_back = 1 THEN
    ADDY B - back_thk
    BLOCK A, back_thk, back_h
    DEL 1
ENDIF

END
```

### 3.3 3D 高风险点

- `PRISM_` 忘高度参数
- `ADD/DEL` 不配平
- 子程序写 `END` 而不是 `RETURN`

### 3.4 3D 阶段检查

- [ ] 3D 末尾有 `END`
- [ ] 所有路径下 `ADD/DEL` 配平
- [ ] `IF/ENDIF`, `FOR/NEXT` 配对

---

## 4) 2D 投影（scripts/2d.gdl）

2D 脚本必须满足可读与可编辑。

### 4.1 最小可用策略

- 先加 `PROJECT2 3, 270, 2`
- 再叠加关键轮廓与热点

### 4.2 推荐写法

```gdl
! scripts/2d.gdl
PROJECT2 3, 270, 2

PEN 1
RECT2 0, 0, A, -B

HOTSPOT2 0, 0, 1
HOTSPOT2 A, 0, 2
HOTSPOT2 A, -B, 3
HOTSPOT2 0, -B, 4
HOTSPOT2 A / 2, -B / 2, 5
```

### 4.3 2D 阶段检查

- [ ] 至少有 `PROJECT2` 或手绘图元
- [ ] 热点完整（角点 + 关键控制点）
- [ ] 2D 尺寸语义与 3D 一致

---

## 5) 跨脚本一致性检查（最终门禁）

在提交给编译器前，做统一一致性检查：

### 5.1 参数一致性

- `paramlist.xml` 中每个参数，在 1D/3D/2D 的拼写完全一致。

### 5.2 派生变量一致性

- 3D/2D 使用的 `_var` 必须在 1D（或本脚本前部）先赋值。

### 5.3 几何语义一致性

- `A/B/ZZYZX` 的“宽/深/高”意义在 2D 与 3D 中不反转。

### 5.4 语法一致性

- `IF/ENDIF`、`FOR/NEXT`、`GOSUB/RETURN`、`ADD/DEL` 全部配平。

---

## 6) 生成顺序（必须遵守）

1. 先输出 `paramlist.xml`
2. 再输出 `scripts/1d.gdl`
3. 再输出 `scripts/3d.gdl`
4. 再输出 `scripts/2d.gdl`
5. 最后附“自检清单结果”

---

## 7) 最小失败恢复策略

若一次生成后编译失败：

1. 优先修结构错误（闭合、参数个数、未定义变量）
2. 不大改功能，仅做最小修补
3. 每次只修一类错误，防止引入新错误
