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
- A、B 作为 Archicad 标准宽高参数保留

## 参数命名规范

- 用下划线命名法：shelf_count、back_panel、door_width
- 避免使用 GDL 保留字作为参数名
- 布尔参数前缀用 has_ 或 show_（如 has_back_panel）

## 默认值要求

- 长度类参数默认值必须是合理的建筑尺寸（单位 mm）
- 书架类：默认高度 2000-2400，宽度 800-1200，深度 300-400
- 桌子类：默认高度 720-760，宽度 1200-1600
- 门类：默认高度 2100，宽度 900
