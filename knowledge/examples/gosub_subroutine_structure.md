---
id: example.gosub_subroutine_structure
type: example
object_types: [subroutine, 子程序, 结构化]
commands: [GOSUB, RETURN, END, CYLIND, BLOCK]
task_types: [create, modify, debug]
source: GDL Cookbook 3 §cookbook_v3_0076 / §cookbook_v3_0100（GOSUB 结构化技法）；代码为 OpenBrep 自研并经 linter 验证
verified: lint
---

# GOSUB 子程序结构（主流程短，工作在子程序）

要点：`GOSUB "标签"` 标签必须加引号；子程序末尾用 `RETURN`，
只有主流程末尾才用 `END`——子程序里误写 END 会提前终止 3D 生成。

```gdl
! 主流程
GOSUB "DrawLegs"
GOSUB "DrawTop"
END

"DrawLegs":
    ADD 0.05, 0.05, 0
    CYLIND ZZYZX - 0.03, 0.02
    DEL 1
RETURN

"DrawTop":
    ADDZ ZZYZX - 0.03
    BLOCK A, B, 0.03
    DEL 1
RETURN
```
