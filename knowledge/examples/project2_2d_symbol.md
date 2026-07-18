---
id: example.project2_2d_symbol
type: example
object_types: [symbol, 2d, 平面符号, 二维]
commands: [PROJECT2, HOTSPOT2, RECT2]
task_types: [create, modify]
source: GDL Cookbook 3 §cookbook_v3_0071（2D Symbol 技法）；代码为 OpenBrep 自研并经 previewer 验证
verified: preview
---

# 2D 脚本：PROJECT2 投影 + 外包络热点

要点：2D 脚本至少要有 `PROJECT2` 或手工图元，否则平面图不可见；
四角 `HOTSPOT2` 保证对象在平面图可选中、可拉伸。

```gdl
PROJECT2 3, 270, 2

HOTSPOT2 0, 0
HOTSPOT2 A, 0
HOTSPOT2 A, B
HOTSPOT2 0, B
```
