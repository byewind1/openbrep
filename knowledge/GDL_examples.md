# GDL 简单示例（Archicad 29）

> 目标：提供一个“从参数到 2D/3D 全链路可用”的最小对象样例。
> 约定：说明中文，代码英文。

---

## 参数化椅子（SimpleChair）

**[FILE: paramlist.xml]** 完整参数
```xml
<?xml version="1.0" encoding="UTF-8"?>
<ParamSection>
    <ParamSectHeader>
        <AutoHotspots>false</AutoHotspots>
        <StatBits>
            <STBit_FixSize/>
        </StatBits>
        <WDLeftFrame>0</WDLeftFrame>
        <WDRightFrame>0</WDRightFrame>
        <WDTopFrame>0</WDTopFrame>
        <WDBotFrame>0</WDBotFrame>
        <LayFlags>65535</LayFlags>
        <WDMirrorThickness>0</WDMirrorThickness>
        <WDWallInset>0</WDWallInset>
    </ParamSectHeader>
    <Parameters SectVersion="27" SectionFlags="0" SubIdent="0">
        <Length Name="A">
            <Description><![CDATA["Chair width"]]></Description>
            <Fix/>
            <Value>0.48</Value>
        </Length>
        <Length Name="B">
            <Description><![CDATA["Chair depth"]]></Description>
            <Fix/>
            <Value>0.52</Value>
        </Length>
        <Length Name="ZZYZX">
            <Description><![CDATA["Chair total height"]]></Description>
            <Fix/>
            <Value>0.85</Value>
        </Length>
        <Length Name="seat_h">
            <Description><![CDATA["Seat height"]]></Description>
            <Value>0.45</Value>
        </Length>
        <Length Name="seat_thk">
            <Description><![CDATA["Seat thickness"]]></Description>
            <Value>0.03</Value>
        </Length>
        <Length Name="leg_dia">
            <Description><![CDATA["Leg diameter"]]></Description>
            <Value>0.04</Value>
        </Length>
        <Length Name="back_h">
            <Description><![CDATA["Backrest height above seat"]]></Description>
            <Value>0.35</Value>
        </Length>
        <Boolean Name="has_back">
            <Description><![CDATA["Backrest switch"]]></Description>
            <Value>1</Value>
        </Boolean>
        <Boolean Name="has_arm">
            <Description><![CDATA["Armrest switch"]]></Description>
            <Value>0</Value>
        </Boolean>
        <Material Name="mat_leg">
            <Description><![CDATA["Leg material"]]></Description>
            <Value>0</Value>
        </Material>
        <Material Name="mat_seat">
            <Description><![CDATA["Seat material"]]></Description>
            <Value>0</Value>
        </Material>
    </Parameters>
</ParamSection>
```

**[FILE: scripts/1d.gdl]** Master脚本
```gdl
! Master Script (1D)
! Archicad 29

! ---- parameter guard ----
IF A < 0.30 THEN A = 0.30
IF B < 0.30 THEN B = 0.30
IF ZZYZX < 0.40 THEN ZZYZX = 0.40

IF seat_h < 0.20 THEN seat_h = 0.20
IF seat_h > ZZYZX - 0.08 THEN seat_h = ZZYZX - 0.08

IF seat_thk < 0.01 THEN seat_thk = 0.01
IF seat_thk > 0.08 THEN seat_thk = 0.08

IF leg_dia < 0.02 THEN leg_dia = 0.02
IF leg_dia > 0.12 THEN leg_dia = 0.12

IF back_h < 0 THEN back_h = 0
IF back_h > ZZYZX - seat_h THEN back_h = ZZYZX - seat_h

! booleans must be 0/1
IF has_back <> 0 AND has_back <> 1 THEN has_back = 1
IF has_arm <> 0 AND has_arm <> 1 THEN has_arm = 0

! ---- derived variables ----
_leg_r = leg_dia / 2
_leg_h = seat_h - seat_thk
IF _leg_h < 0.05 THEN _leg_h = 0.05

_back_w = A - leg_dia * 2
IF _back_w < 0.05 THEN _back_w = 0.05

_arm_z = seat_h + back_h * 0.45
_arm_y0 = leg_dia
_arm_len = B - leg_dia * 2
IF _arm_len < 0.05 THEN _arm_len = 0.05
```

**[FILE: scripts/3d.gdl]** 3D脚本
```gdl
! 3D Script
! Archicad 29

TOLER 0.001

! ---- seat ----
MATERIAL mat_seat
ADDZ seat_h - seat_thk
BLOCK A, B, seat_thk
DEL 1

! ---- legs ----
MATERIAL mat_leg

! leg 1 (front-left)
ADDX _leg_r
ADDY _leg_r
CYLIND _leg_h, _leg_r
DEL 2

! leg 2 (front-right)
ADDX A - _leg_r
ADDY _leg_r
CYLIND _leg_h, _leg_r
DEL 2

! leg 3 (back-left)
ADDX _leg_r
ADDY B - _leg_r
CYLIND _leg_h, _leg_r
DEL 2

! leg 4 (back-right)
ADDX A - _leg_r
ADDY B - _leg_r
CYLIND _leg_h, _leg_r
DEL 2

! ---- backrest ----
IF has_back = 1 THEN
    MATERIAL mat_seat
    ADDX leg_dia
    ADDY B - leg_dia
    ADDZ seat_h
    BLOCK _back_w, leg_dia, back_h
    DEL 3
ENDIF

! ---- armrests ----
IF has_arm = 1 THEN
    MATERIAL mat_seat

    ! left arm
    ADDX 0
    ADDY _arm_y0
    ADDZ _arm_z
    BLOCK leg_dia, _arm_len, leg_dia
    DEL 3

    ! right arm
    ADDX A - leg_dia
    ADDY _arm_y0
    ADDZ _arm_z
    BLOCK leg_dia, _arm_len, leg_dia
    DEL 3
ENDIF

END
```

**[FILE: scripts/2d.gdl]** 2D脚本（含PROJECT2）
```gdl
! 2D Script
! Archicad 29

! recommended projection baseline
PROJECT2 3, 270, 2

! manual symbol overlay for readability
PEN 1

! footprint
RECT2 0, 0, A, -B

! legs in plan
CIRCLE2 leg_dia / 2, -leg_dia / 2, leg_dia / 2
CIRCLE2 A - leg_dia / 2, -leg_dia / 2, leg_dia / 2
CIRCLE2 leg_dia / 2, -(B - leg_dia / 2), leg_dia / 2
CIRCLE2 A - leg_dia / 2, -(B - leg_dia / 2), leg_dia / 2

! backrest hint
IF has_back = 1 THEN
    LINE2 leg_dia, -B + leg_dia, A - leg_dia, -B + leg_dia
ENDIF

! hotspots
HOTSPOT2 0, 0, 1
HOTSPOT2 A, 0, 2
HOTSPOT2 A, -B, 3
HOTSPOT2 0, -B, 4
HOTSPOT2 A / 2, -B / 2, 5
```

---

## 使用说明

1. 先将 `paramlist.xml` 写入参数表。
2. 再写入 `1d.gdl / 3d.gdl / 2d.gdl`。
3. 执行语法检查，确认：
   - `IF/ENDIF`、`ADD/DEL` 配平；
   - 3D 脚本有 `END`；
   - 2D 脚本包含 `PROJECT2`。
