---
id: example.door_panel_swing
type: example
object_types: [door, 门, 门扇, 平开门]
commands: [BLOCK, ROTZ, ADD, DEL, HOTSPOT2]
task_types: [create, modify]
source: GDL Cookbook 3 §cookbook_v3_0585（门 2D/3D 技法）；代码为 OpenBrep 自研并经 previewer 验证
verified: preview
---

# 门扇开启角（铰链侧为旋转轴）

要点：门扇绕铰链边 `ROTZ` 开启角，先 `ADD` 到铰链位置再旋转；
开启角参数做 0–120 度范围保护；框与扇分开建模。

```gdl
IF open_angle < 0 THEN open_angle = 0
IF open_angle > 120 THEN open_angle = 120

! 门框（简化：左右立梃 + 顶梁）
BLOCK frame_thk, frame_d, ZZYZX
ADDX A - frame_thk
BLOCK frame_thk, frame_d, ZZYZX
DEL 1
ADDZ ZZYZX - frame_thk
BLOCK A, frame_d, frame_thk
DEL 1

! 门扇：铰链在左立梃内侧
ADD frame_thk, frame_d / 2, 0
ROTZ -open_angle
BLOCK A - 2 * frame_thk, panel_thk, ZZYZX - frame_thk
DEL 2
END
```
