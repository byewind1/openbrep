---
id: core.planning_contract
type: core
task_types: [create, image]
priority: 95
---

# GDL 构件规划约定

## 规划前必须确定

- 构件属于哪个对象族（bookshelf/cabinet/door/window/railing/table/profile_object）
- 主几何策略（板式组合/框架/拉伸/旋转/放样）
- 参数表必须包含的基础参数（尺寸、材质、开关）
- 2D 表达策略（投影/简化符号/独立绘制）

## 参数表基本原则

- 尺寸参数用 Length 类型，默认值用常见建筑尺寸
- 材质参数用 Material 类型
- 布尔开关用 Boolean 类型（0/1）
- 所有参数必须有合理默认值，不能为空

## 脚本职责划分

- 3D 脚本：几何体、材质、热点
- 2D 脚本：平面投影或简化符号
- Master 脚本：派生参数计算（如果有）
- 参数脚本：参数定义、分组、默认值
