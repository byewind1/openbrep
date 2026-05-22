---
name: gdl-dougong
description: "GDL dougong reference implementation — GDL 斗拱参考实现。含参数化单抄斗拱的可编译 GDL 源码（1d/2d/3d/ui 脚本 + paramlist）。"
version: 1.0.0
author: Hermes Agent
tags: [gdl, dougong, archicad, traditional-architecture, openbrep]
---

# GDL Dougong (斗拱) — Reference Implementation

> 适用于 Archicad 29+ 的参数化单抄斗拱（一斗三升类型）。
> 可编译为 .gsm。作为 OpenBrep GDL 生成器的领域构件参考。

## 触发条件

用户要求：
- 写斗拱 / dougong / bracket set
- 参数化古建筑构件
- 中国建筑 GDL 示例
- "给我一个斗拱的例子"

## 架构说明

### 构件组成（一斗三升）

```
坐斗（底部大斗，带十字槽）
 ├─ 左侧散斗（左拱端小斗）
 ├─ 右侧散斗（右拱端小斗）
 └─ 中央散斗（泥道拱上方）
```

### 文件结构（HSF 工程）

```
dougong/
├── libpartdata.xml        # 项目元数据
├── paramlist.xml           # 参数定义
├── scripts/
│   ├── 1d.gdl             # Master 脚本（预计算）
│   ├── 2d.gdl             # 2D 符号
│   ├── 3d.gdl             # 3D 几何（主脚本）
│   └── ui.gdl             # 界面交互
```

## 核心命令

| 构件 | 命令 | 说明 |
|------|------|------|
| 坐斗 | `PRISM_` | 12 点多边形定义十字槽 |
| 散斗 | `PRISM_` | 8 点多边形定义槽 |
| 拱（华拱/泥道拱） | `REVOLVE` | 旋转扫掠形成拱形曲线 |
| 拱的精细弧线 | `TOLER` | 控制 REVOLVE 曲面平滑度 |
| 变换定位 | `ADD`/`ROTX` | 子程序内变换栈 |
| 栈清理 | `DEL` | 与 ADD/ROT 配平 |

## 参数设计（斗口制）

- `A` = 斗口宽度（清式模数，默认 0.42m）
- `B` = 斗拱总深 = 出跳×2 + 坐斗宽
- `ZZYZX` = 斗拱总高（坐斗高 + 拱厚 × 层数 + 散斗高）
- `n_tiers` = 层数（单抄/双抄）
- `mat_body` = 木质材质

## 关键约束

1. **PRISM_ 多边形顺序**：必须逆时针，status 位控制边可见性
2. **REVOLVE 轮廓**：NSP/3 个点，通过 PUT/GET 填充
3. **变换栈配对**：每个 `ADD`/`ROT` 必须有对应 `DEL`
4. **GOSUB 子程序**：每个构件类型一个子程序，避免代码膨胀
5. **斗口模数**：所有尺寸基于 `A` 计算（单材=1A，足材=1.4A 等）

## 参考 GDL 3D 脚本

```gdl
! ==========================================
! Dougong 3D Script — 一斗三升型
! ==========================================
PEN 1
MATERIAL mat_body
TOLER 0.005

! --- 坐斗 ---
GOSUB 100: ! zuodou

! --- 华拱（出跳） ---
ADDZ zuodou_h
GOSUB 200: ! huagong

! --- 散斗 x 2（拱端） ---
ADDX -jump_len
ADDZ gong_h
GOSUB 300: ! sandou
ADDX jump_len * 2
GOSUB 300: ! sandou

! --- 泥道拱（横向） ---
ADDX -jump_len
ADDZ sandou_h
GOSUB 400: ! nidaogong

! 清栈
DEL 6
END

! ============ 子程序 ============

! 坐斗
100:
  ! 12点十字槽截面
  PRISM_ 12, zuodou_h,
    -dou_w/2, -dou_d/2, 1,
     dou_w/2, -dou_d/2, 1,
     dou_w/2, -dou_hole_w/2, 1,
     dou_hole_w/2, -dou_hole_d/2, 1,
     dou_hole_w/2,  dou_hole_d/2, 1,
     dou_w/2,  dou_hole_d/2, 1,
     dou_w/2,  dou_d/2, 1,
    -dou_w/2,  dou_d/2, 1,
    -dou_w/2,  dou_hole_d/2, 1,
    -dou_hole_w/2,  dou_hole_d/2, 1,
    -dou_hole_w/2, -dou_hole_d/2, 1,
    -dou_w/2, -dou_hole_d/2, 1
RETURN

! 华拱
200:
  ! 拱形轮廓：中间高两端低，用 REVOLVE 扫掠
  ! 此处以半圆弧简化，实际可 PUT 多点逼近卷杀
  REVOLVE NSP/3, 180, 63, GET(NSP)
RETURN

! 散斗
300:
  PRISM_ 8, sandou_h,
    -sd_w/2, -sd_d/2, 1,
     sd_w/2, -sd_d/2, 1,
     sd_w/2, -sd_hole_d/2, 1,
     sd_hole_w/2, -sd_hole_d/2, 1,
     sd_hole_w/2,  sd_hole_d/2, 1,
     sd_w/2,  sd_hole_d/2, 1,
     sd_w/2,  sd_d/2, 1,
    -sd_w/2,  sd_d/2, 1
RETURN

! 泥道拱
400:
  REVOLVE NSP/3, 180, 63, GET(NSP)
RETURN
```

## 参考 Master 脚本（1d.gdl）

```gdl
! Dougong Master Script — 参数预计算
! 斗口模数制
_dou_kou = A           ! 斗口宽度
_dan_cai = _dou_kou    ! 单材
_zu_cai = _dou_kou * 1.4  ! 足材

! 坐斗
zuodou_h = _zu_cai * 0.8
dou_w = _dan_cai * 1.5
dou_d = _dan_cai * 1.5
dou_hole_w = _dan_cai * 0.6
dou_hole_d = _dan_cai * 0.6

! 拱
gong_h = _zu_cai
jump_len = _dan_cai * 1.2

! 散斗
sandou_h = _dan_cai * 0.6
sd_w = _dan_cai * 0.8
sd_d = _dan_cai * 0.8
sd_hole_w = _dan_cai * 0.4
sd_hole_d = _dan_cai * 0.4
```

## 参考参数（paramlist.xml）

```xml
<Length Name="A"><Fix/><Value>0.42</Value></Length>
<Length Name="B"><Fix/><Value>1.20</Value></Length>
<Length Name="ZZYZX"><Fix/><Value>0.60</Value></Length>
<Integer Name="n_tiers"><Value>1</Value></Integer>
<Material Name="mat_body"><Value>0</Value></Material>
```

## 参考 2D 脚本

```gdl
! Dougong 2D Script
PROJECT2 3, 270, 2

! 热点
HOTSPOT2 0, 0
HOTSPOT2 -B/2, 0
HOTSPOT2 B/2, 0
```

## 陷阱

- **PRISM_ 状态码 mask**：`1+2+4=7` 显示所有边，`63` 显示所有面。散斗顶面开槽状态的 mask 易搞错。
- **REVOLVE 360°**：如果不想要完整的旋转体（拱只扫 180°），`alpha` 参数必须精确。
- **`DEL` 计数**：变换栈嵌套复杂时，用 `DEL 1` 逐个出栈，不要一次 `DEL N` 除非你确认栈深度。
- **斗口模数与浮点精度**：古建筑比例关系用分数表达更稳定（`_dan_cai = A`，不用硬编码）。

## 验证方法

```bash
# 在 OpenBrep 工作区中创建 HSF 项目
cd /path/to/gdl-agent
python -c "
from openbrep.hsf import HSFProject
proj = HSFProject.create('dougong')
proj.write_script('3d', scripts/3d.gdl-script)
proj.write_script('1d', '...')
proj.write_param('A', 0.42)
ok = proj.compile()
print('Compile:', 'PASS' if ok else 'FAIL')
"
```

如果 `.gsm` 输出无错误，即可在 Archicad 中放置预览。
