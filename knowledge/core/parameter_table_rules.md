---
id: core.parameter_rules
type: core
task_types: [create, image, modify]
priority: 90
---

# GDL 参数表规则

## 必须包含的基础参数

- 至少一个尺寸参数（宽/高/深/直径）
- 至少一个材质参数
- A、B、ZZYZX 按 Archicad 标准角色保留：A=宽、B=深、ZZYZX=高；不要把高度放进 A 或 B

## 参数命名规范

- 用下划线命名法：shelf_count、back_panel、door_width
- 避免使用 GDL 保留字作为参数名
- 布尔参数前缀用 has_ 或 show_（如 has_back_panel）

## 默认值要求

- 长度类参数在 GDL/HSF 内部以米为单位，默认值也必须使用米；输入为毫米时先换算（1200 mm = 1.2）
- 书架类：默认高度 2.0-2.4，宽度 0.8-1.2，深度 0.3-0.4
- 桌子类：默认高度 0.72-0.76，宽度 1.2-1.6
- 门类：默认高度约 2.1，宽度约 0.9
