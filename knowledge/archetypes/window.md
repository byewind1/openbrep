---
id: archetype.window
title: 参数化窗
type: archetype
task_types: [create, modify]
object_types: [window, 窗, 窗户, 窗框]
commands: [BLOCK, ADDX, ADDY, ADDZ, DEL, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 82
verified: false
tags: [window, hosted-object, building-element]
---

# 参数化窗 / Window

## 建模目标

窗对象应表达外框、内框或分格、玻璃和 2D 平面符号。专业版本需要考虑墙体宿主、窗台高度、开启方式和洞口关系。

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 窗宽 |
| `B` | Length | 窗深 |
| `ZZYZX` | Length | 窗高 |
| `frame_thk` | Length | 窗框厚度 |
| `glass_thk` | Length | 玻璃厚度 |
| `mullion_count` | Integer | 竖向分格数量 |
| `has_sill` | Boolean | 是否显示窗台 |
| `mat_frame` | Material | 窗框材质 |
| `mat_glass` | Material | 玻璃材质 |

## 几何拆解

- 外框由左右框、上下框组成。
- 玻璃作为薄板放在框内。
- 分格条按数量循环生成。
- 窗台作为可选底部构件。

## 3D 策略

- 框体优先使用 `BLOCK`。
- 玻璃厚度应较小，并使用独立材质参数。
- 分格数量需要最小值保护。
- 进阶墙洞逻辑应在确认对象类型和宿主能力后再生成。

## 2D 策略

- 外包络 `HOTSPOT2`。
- 绘制窗框线和分格线。
- 可用 `PROJECT2` 兜底，但窗类对象最好有语义化 2D 表达。

## 常见风险

- 窗作为普通 OBJECT 插入时不会自动开洞。
- 分格数量过多会造成几何密度过高。
- 玻璃和框共面容易闪烁，应有明确厚度或位置偏移。
