---
id: archetype.table
title: 参数化桌子
type: archetype
task_types: [create, modify]
object_types: [table, desk, 桌, 桌子, 书桌, 餐桌, 会议桌]
commands: [BLOCK, CYLIND, ADDX, ADDY, ADDZ, DEL, FOR, NEXT, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 80
verified: false
tags: [furniture, table, parametric]
---

# 参数化桌子 / Table

## 建模目标

桌子应表达为台面、桌腿和可选横撑组成的参数化家具。默认保证 A/B/ZZYZX 外包络可控，桌腿位置随尺寸变化。

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 总宽度 |
| `B` | Length | 总深度 |
| `ZZYZX` | Length | 总高度 |
| `top_thk` | Length | 台面厚度 |
| `leg_size` | Length | 方腿边长或圆腿直径 |
| `leg_inset` | Length | 桌腿离边距离 |
| `has_stretchers` | Boolean | 是否显示横撑 |
| `mat_top` | Material | 台面材质 |
| `mat_leg` | Material | 桌腿材质 |

## 几何拆解

- 台面使用 `BLOCK A, B, top_thk`，放置在顶部高度。
- 四条方腿可用 `BLOCK leg_size, leg_size, ZZYZX - top_thk`。
- 圆腿可用 `CYLIND`，但默认方腿更稳定。
- 横撑可作为可选 `BLOCK`，沿 X 或 Y 方向布置。

## 3D 策略

- 先生成桌腿，再生成台面，避免遮挡判断混乱。
- 每条腿使用独立 `ADD/DEL` 定位。
- `leg_inset` 应小于 A/B 的一半。
- 横撑高度应由参数或合理默认推导。

## 2D 策略

- 四角 `HOTSPOT2` 定义外包络。
- 使用 `PROJECT2 3, 270, 2` 生成基础平面。
- 可追加桌腿位置的简化矩形，提升平面可读性。

## 常见风险

- 桌腿高度不能为负。
- 桌腿内缩不能超过桌面尺寸。
- 圆腿用 `CYLIND` 时注意当前坐标系和高度方向。
