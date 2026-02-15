# GDL Quick Reference (Minimal)

> This is a minimal reference for demo purposes.
> Replace with your comprehensive GDL documentation.
>
> 这是用于演示的最小参考。请替换为你的完整 GDL 文档。

## Basic 3D Commands

```gdl
BLOCK  a, b, c                      ! Box: width, depth, height
CYLIND h, r                          ! Cylinder: height, radius
SPHERE r                             ! Sphere: radius
PRISM_ n, h, x1,y1, ..., xn,yn     ! Prism: vertices, height, coords
```

## Transformation Stack

```gdl
ADD  dx, dy, dz     ! Push translation
DEL  n              ! Pop n transformations (MUST match ADD count)
ADDX dx             ! Shorthand for ADD dx, 0, 0
ADDY dy             ! Shorthand for ADD 0, dy, 0
ADDZ dz             ! Shorthand for ADD 0, 0, dz
```

## Parameter Types

| Type | GDL Type | Example |
|:---|:---|:---|
| Length | Length | `A`, `B`, `ZZYZX` |
| Integer | Integer | `nShelves` |
| Boolean | Boolean | `bHasBack` |
| Real | RealNum | `rAngle` |
| Material | Material | `frameMat` |
| String | String | `sLabel` |

## XML Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Parameters>
    <Parameter>
      <n>paramName</n>
      <Type>Length</Type>
      <Value>1.0</Value>
    </Parameter>
  </Parameters>
  <Script_1D><![CDATA[ ... ]]></Script_1D>
  <Script_2D><![CDATA[ ... ]]></Script_2D>
  <Script_3D><![CDATA[ ... ]]></Script_3D>
  <Script_PR><![CDATA[ ... ]]></Script_PR>
  <Script_UI><![CDATA[ ... ]]></Script_UI>
</Symbol>
```
