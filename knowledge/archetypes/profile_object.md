---
id: archetype.profile_object
title: 剖面/旋转/放样构件
type: archetype
task_types: [create, modify]
object_types: [profile, revolve, sweep, extrusion, 剖面, 旋转体, 放样, 异形板]
commands: [PRISM_, REVOLVE, SWEEP, ADDX, ADDY, ADDZ, DEL, MATERIAL, PROJECT2, HOTSPOT2]
script_types: [3d, 2d, param]
priority: 84
verified: false
tags: [profile, extrusion, revolve, sweep, parametric]
---

# 剖面/旋转/放样构件 / Profile Object

## 建模目标

当用户描述异形板、旋转体、剖面拉伸或沿路径生成的对象时，应先判断几何策略，而不是默认用多个 `BLOCK` 拼凑。

## 命令选择

| 场景 | 推荐命令 |
|---|---|
| 任意 2D 多边形沿 Z 拉伸 | `PRISM_` |
| 剖面绕轴旋转 | `REVOLVE` |
| 剖面沿路径扫掠 | `SWEEP` |
| 简单矩形板件 | `BLOCK` |

## 默认参数

| 参数 | 类型 | 默认语义 |
|---|---|---|
| `A` | Length | 总宽度或外径 |
| `B` | Length | 总深度 |
| `ZZYZX` | Length | 总高度 |
| `profile_thk` | Length | 剖面厚度 |
| `segments` | Integer | 圆弧或旋转分段 |
| `mat_main` | Material | 主材质 |

## 3D 策略

- 对异形平面轮廓，优先选择 `PRISM_`。
- 对花瓶、柱头、圆形扶手等旋转体，优先选择 `REVOLVE`。
- 对路径构件，优先考虑 `SWEEP`，但要谨慎处理路径点和截面点。
- 如果轮廓复杂，先生成简化可编译版本，再逐步增加细节。

## 2D 策略

- 至少输出外包络热点。
- `PROJECT2 3, 270, 2` 可作为兜底。
- 工程版本应绘制轮廓线，方便平面识别。

## 常见风险

- `PRISM_` 点序、状态码和高度参数容易出错。
- `REVOLVE` 需要清晰旋转轴和剖面点。
- `SWEEP` 比基础命令更难稳定生成，复杂路径应分阶段实现。
