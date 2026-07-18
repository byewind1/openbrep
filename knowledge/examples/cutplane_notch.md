---
id: example.cutplane_notch
type: example
object_types: [cut, notch, 切口, 开槽, 斜切]
commands: [CUTPLANE, CUTEND, BLOCK, ROTY]
task_types: [create, modify]
source: GDL Cookbook 3 §cookbook_v3_0259–0261（CUTPLANE/CUTEND 技法）；代码为 OpenBrep 自研并经 linter 验证
verified: lint
---

# CUTPLANE 斜切（每个 CUTPLANE 必须有对应 CUTEND）

要点：`CUTPLANE` 与 `CUTEND` 必须成对；切割面由当前坐标系的 x-y 平面定义，
先用变换栈摆好切割面姿态，切完及时 `CUTEND` + `DEL`，避免误切后续几何。

```gdl
! 把长方体顶部斜切掉一角
ADDZ ZZYZX * 0.7
ROTY -15
CUTPLANE
DEL 2

BLOCK A, B, ZZYZX

CUTEND
END
```
