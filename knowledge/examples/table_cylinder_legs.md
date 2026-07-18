---
id: example.table_cylinder_legs
type: example
object_types: [table, desk, 桌, 桌子, 餐桌, 书桌]
commands: [BLOCK, CYLIND, ADD, DEL, FOR, NEXT]
task_types: [create]
source: GDL Cookbook 3 §cookbook_v3_0118（循环放置）；代码为 OpenBrep 自研并经 previewer 验证
verified: preview
---

# 圆柱腿桌子（双层循环放置四条腿）

要点：桌腿位置用两层 `FOR` 循环从内缩距计算，`CYLIND h, r` 第一参数是高度；
桌面最后放，置于腿顶标高。

```gdl
IF leg_r <= 0 THEN leg_r = 0.02
_inset = leg_r + 0.04
_leg_h = ZZYZX - top_thk

! 四条腿：x/y 各取两个位置
FOR ix = 0 TO 1
    FOR iy = 0 TO 1
        ADD _inset + ix * (A - 2 * _inset), _inset + iy * (B - 2 * _inset), 0
        CYLIND _leg_h, leg_r
        DEL 1
    NEXT iy
NEXT ix

! 桌面
ADDZ _leg_h
BLOCK A, B, top_thk
DEL 1
END
```
