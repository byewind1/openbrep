---
id: archetype.door
title: 参数化门
type: archetype
task_types: [create, modify]
object_types: [door, 门, 门扇, 门框]
commands: [BLOCK, ADDX, ADDY, ADDZ, ROTZ, DEL, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 82
verified: false
tags: [door, hosted-object, building-element]
---

# 参数化门 / Door

## 建模目标

门对象应至少表达门框、门扇、开启方向和 2D 平面符号。专业版本需要考虑墙体宿主、开启线、门洞关系和尺寸参数。

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 门洞宽度 |
| `B` | Length | 门厚或墙厚方向深度 |
| `ZZYZX` | Length | 门高度 |
| `frame_thk` | Length | 门框厚度 |
| `leaf_thk` | Length | 门扇厚度 |
| `opening_angle` | Angle | 2D 开启角度 |
| `handing` | Integer | 左右开启方向 |
| `mat_frame` | Material | 门框材质 |
| `mat_leaf` | Material | 门扇材质 |

## 几何拆解

- 门框分左右竖框和上框，可用 `BLOCK`。
- 门扇用薄板 `BLOCK` 表达。
- 门扇位置按 `handing` 控制左右。
- 开启线主要在 2D 脚本表达，3D 可保持关闭状态。

## 3D 策略

- 默认先生成门框，再生成门扇。
- 门框应围绕门洞边界，避免超出 A/ZZYZX。
- 门扇厚度沿 B 方向表达。
- 墙宿主逻辑属于进阶能力，不应在不确定时硬写复杂洞口逻辑。

## 2D 策略

- 需要外包络 `HOTSPOT2`。
- 绘制门扇线和开启弧线，至少表达左开/右开。
- 可用 `PROJECT2` 作为最低可见策略，但门类对象应优先绘制语义化 2D 符号。

## 常见风险

- 门对象如果作为普通 OBJECT 插入，不会自动产生墙洞。
- 开启方向不能只靠 3D 几何表达，2D 符号更重要。
- `opening_angle` 应有合理范围。
