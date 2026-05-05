---
id: archetype.bookshelf
title: 参数化书架
type: archetype
task_types: [create, modify]
object_types: [bookshelf, shelf, bookcase, 书架, 书柜]
commands: [BLOCK, ADDX, ADDY, ADDZ, DEL, FOR, NEXT, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 90
verified: false
tags: [board-assembly, furniture, parametric]
---

# 参数化书架 / Bookshelf

## 建模目标

书架应被建模为可维护的板式构件，而不是单个 `BLOCK`。默认由左右侧板、顶板、底板、中间层板和可选背板组成。

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 总宽度 |
| `B` | Length | 总深度 |
| `ZZYZX` | Length | 总高度 |
| `frame_thk` | Length | 左右侧板厚度 |
| `shelf_thickness` | Length | 层板厚度 |
| `shelf_count` | Integer | 总层数，包含顶板和底板 |
| `has_back_panel` | Boolean | 是否生成背板 |
| `back_thk` | Length | 背板厚度 |
| `mat_frame` | Material | 框架材质 |
| `mat_shelf` | Material | 层板材质 |

## 几何拆解

- 左右侧板使用 `BLOCK frame_thk, B, ZZYZX`。
- 顶板、底板使用 `BLOCK _inner_w, B, shelf_thickness`。
- 中间层板使用 `FOR/NEXT` 循环生成。
- 背板沿 Y 方向靠后布置，使用 `ADDY B - back_thk`。
- 派生参数 `_inner_w` 应保证非负，层板间距 `_shelf_gap` 应避免除零。

## 3D 策略

- 优先使用 `BLOCK` 表达矩形板件。
- 每次 `ADDX`、`ADDY`、`ADDZ` 后必须用对应 `DEL` 清理变换栈。
- 中间层板循环范围应避免 `shelf_count < 2` 时出错。
- 生成顺序建议：侧板、底板、顶板、中间层板、背板。

## 2D 策略

- 至少生成四个外包络 `HOTSPOT2`，保证平面可选中。
- 可用 `PROJECT2 3, 270, 2` 快速获得 2D 投影。
- 工程化版本可进一步用 `RECT2` 或 `POLY2_` 绘制简化平面符号。

## 常见风险

- `FOR i = 1 TO shelf_count - 2` 在层数不足时需要保护。
- `_inner_w = A - 2 * frame_thk` 可能为负。
- `ADD/DEL` 数量不平衡会导致后续板件位置错误。
- 参数名必须和 `paramlist.xml` 完全一致。
