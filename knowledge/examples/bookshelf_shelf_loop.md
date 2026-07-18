---
id: example.bookshelf_shelf_loop
type: example
object_types: [bookshelf, shelf, bookcase, 书架, 书柜, 层板]
commands: [BLOCK, FOR, NEXT, ADDZ, DEL]
task_types: [create, modify]
source: GDL Cookbook 3 §cookbook_v3_0116–0118（FOR/NEXT 循环技法）；代码为 OpenBrep 自研并经 previewer 验证
verified: preview
---

# 书架层板 FOR 循环阵列

要点：层板用 `FOR/NEXT` 阵列生成，每次 `ADDZ` 入栈后立即 `DEL 1` 出栈，
避免栈深随循环累积；层数参数先做最小值保护再参与除法。

```gdl
! 参数保护：避免除零与负间距
IF shelf_count < 2 THEN shelf_count = 2
_gap = (ZZYZX - shelf_thk) / (shelf_count - 1)

! 左右侧板
BLOCK side_thk, B, ZZYZX
ADDX A - side_thk
BLOCK side_thk, B, ZZYZX
DEL 1

! 层板阵列（含顶板、底板）
FOR i = 1 TO shelf_count
    ADD side_thk, 0, (i - 1) * _gap
    BLOCK A - 2 * side_thk, B, shelf_thk
    DEL 1
NEXT i
END
```
