---
id: example.revolve_vase
type: example
object_types: [profile, revolve, 旋转体, 花瓶, 车削件]
commands: [REVOLVE, MATERIAL]
task_types: [create]
source: GDL Cookbook 3 §cookbook_v3_0056（REVOLVE 技法）；代码为 OpenBrep 自研并经 linter 验证
verified: lint
---

# REVOLVE 旋转体（剖面在 x-z 平面，绕 Z 轴旋转）

要点：`REVOLVE n, alpha, mask, x1, z1, s1, ...` 的剖面点是 (x, z) 对，
x 是半径方向；alpha 是旋转角（360 为整圈）；剖面从下往上描。

```gdl
MATERIAL mat_body
REVOLVE 5, 360, 63,
    0.10, 0.00, 15,
    0.14, 0.05, 15,
    0.08, 0.25, 15,
    0.10, 0.35, 15,
    0.09, 0.36, 15
END
```
