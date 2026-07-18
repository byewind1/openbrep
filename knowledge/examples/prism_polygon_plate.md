---
id: example.prism_polygon_plate
type: example
object_types: [profile, plate, 异形板, 板件, 多边形]
commands: [PRISM_, ADDZ, DEL]
task_types: [create]
source: GDL Cookbook 3 §cookbook_v3_0147（PRISM_ 技法）；代码为 OpenBrep 自研并经 linter 验证
verified: lint
---

# PRISM_ 异形板（顶点数与 n 必须一致）

要点：`PRISM_ n, h, x1, y1, s1, ...` 中 `n` 必须等于顶点组数；
每个顶点带 status 掩码（15 = 全部边可见）；漏写高度 h 是高频错误。

```gdl
! L 形板：6 个顶点，n = 6
PRISM_ 6, plate_thk,
    0,      0,      15,
    A,      0,      15,
    A,      B/2,    15,
    A/2,    B/2,    15,
    A/2,    B,      15,
    0,      B,      15
END
```
