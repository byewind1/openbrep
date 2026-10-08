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

## 参数与脚本职责

- 尺寸参数用 Length 类型，内部单位为米；毫米需求先换算成米
- A=宽、B=深、ZZYZX=高；尊重对象子类型要求，不要互换角色
- 材质参数用 Material 类型
- 布尔开关用 Boolean 类型（0/1）
- 所有参数必须有合理默认值，不能为空

参数声明、类型和默认值由参数表（paramlist）承载。脚本职责划分：

- 3D 脚本：几何体、材质、热点
- 2D 脚本：平面投影或简化符号
- Master（1D）脚本：运行时校验和派生参数计算（如果有）
- Parameter（Values）脚本：VALUES 范围/枚举约束与 LOCK；不在此脚本声明参数或设置其类型/默认值
