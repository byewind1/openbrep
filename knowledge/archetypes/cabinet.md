---
id: archetype.cabinet
title: 参数化柜体
type: archetype
task_types: [create, modify]
object_types: [cabinet, cupboard, 柜, 柜体, 收纳柜, 鞋柜, 橱柜]
commands: [BLOCK, PRISM_, ADDX, ADDY, ADDZ, DEL, FOR, NEXT, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 85
verified: false
tags: [furniture, storage, parametric]
---

# 参数化柜体 / Cabinet

## 建模目标

柜体应表达为可参数化的箱体系统，而不是固定尺寸几何。默认包括左右侧板、顶底板、背板、门板或抽屉面板、可选层板、踢脚和把手。

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 总宽度 |
| `B` | Length | 总深度 |
| `ZZYZX` | Length | 总高度 |
| `panel_thk` | Length | 板厚 |
| `door_count` | Integer | 门板数量 |
| `shelf_count` | Integer | 内部层板数量 |
| `has_doors` | Boolean | 是否显示门板 |
| `has_handles` | Boolean | 是否显示把手 |
| `kick_height` | Length | 踢脚高度 |
| `mat_body` | Material | 柜体材质 |
| `mat_front` | Material | 门板/抽屉面材质 |

## 几何拆解

- 柜体主箱体用板件组合：侧板、顶板、底板、背板。
- 门板按 `door_count` 均分宽度，可用循环生成。
- 层板按内部高度和层板数量生成。
- 把手可以先用简化 `BLOCK` 或 `CYLIND` 表达，后续再升级。
- 如果需要异形侧板或踢脚缺口，可用 `PRISM_`。

## 3D 策略

- 标准柜优先使用 `BLOCK`，避免过早使用复杂 mesh。
- 派生参数应计算内宽、内高、单门宽、层板间距。
- 门板应略微前置，避免与柜体共面闪烁。
- `has_doors`、`has_handles` 应控制门板和把手是否生成。

## 2D 策略

- 至少有外包络热点。
- 默认可用 `PROJECT2 3, 270, 2`。
- 对柜门可绘制门缝线，提升平面可读性。

## 常见风险

- 门板数量不能为 0。
- 板厚不能大于总宽度或总高度。
- 门板和柜体共面容易显示异常，应有微小前移。
- 循环生成门板和层板时必须保证 `FOR/NEXT` 配对。
