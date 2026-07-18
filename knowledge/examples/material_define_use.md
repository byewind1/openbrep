---
id: example.material_define_use
type: example
object_types: [material, 材质, 表面]
commands: [DEFINE MATERIAL, MATERIAL, BLOCK]
task_types: [create, modify]
source: GDL Cookbook 3 §cookbook_v3_0059–0061（材质技法）；代码为 OpenBrep 自研并经 linter 验证
verified: lint
---

# 材质：参数化引用优先，自定义材质须先 DEFINE

要点：材质参数应存 Material 类型（整数索引），脚本里直接 `MATERIAL mat_body`；
需要固定颜色时用 `DEFINE MATERIAL` 自定义再引用，名称必须一致。

```gdl
! 方式 1：引用用户参数（推荐，用户可在设置里改）
MATERIAL mat_body
BLOCK A, B, ZZYZX

! 方式 2：自定义材质（固定外观）
DEFINE MATERIAL "obr_wood" 2, 0.55, 0.35, 0.18
MATERIAL "obr_wood"
ADDZ ZZYZX
BLOCK A, B, 0.02
DEL 1
END
```
