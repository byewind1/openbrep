---
type: concept
status: stable
tags: [parameters, xml, types, configuration, paramlist, ui]
aliases: [paramlist, parameters xml, PARAMETERS, paramlist.xml, gdl parameters]
source: raw/ccgdl_dev_doc/docs/GDL_01_Basics.md
---

# Paramlist_XML

`paramlist.xml` is the parameter definition file for a GDL object. It defines all user-facing parameters — their names, types, default values, ranges, and UI grouping. Unlike GDL scripts which use PARAMETERS statements, the modern GDL format uses `paramlist.xml` for declarative parameter specification.

## Why Parameters?

Without parameters, every GDL object would be a fixed shape. Parameters make objects **parametric** — a single door object can be 0.8m or 1.2m wide depending on the `A` parameter. The `paramlist.xml` file is the contract between the object and the ArchiCAD UI: it tells ArchiCAD what knobs and sliders to show the user.

## File Location

```
my_object/
├── paramlist.xml        ← parameter definitions
├── scripts/
│   ├── master.gdl       ← master script (parameter defaults, calculations)
│   ├── 1d.gdl           ← 1D / plan script
│   ├── 2d.gdl           ← 2D projection script
│   ├── 3d.gdl           ← 3D geometry script
│   ├── ui.gdl           ← UI script (custom dialogs)
│   └── vl.gdl           ← property / listing script
```

## Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<PARAMETERS product="ArchiCAD" version="1">
    <PARAMETER name="A" type="Length">
        <VALUE>1.00</VALUE>
        <FIXEDVALUE>0.0</FIXEDVALUE>
        <RANGEMIN>0.1</RANGEMIN>
        <RANGEMAX>3.0</RANGEMAX>
    </PARAMETER>
</PARAMETERS>
```

## Elements

| Element            | Description                                |
|--------------------|--------------------------------------------|
| `PARAMETERS`       | Root element (`product` and `version` attrs) |
| `PARAMETER`        | A single parameter (`name` and `type` attrs) |
| `VALUE`            | Default value                               |
| `FIXEDVALUE`       | Fixed value override (0.0 = no override)    |
| `RANGEMIN`         | Minimum allowed value                       |
| `RANGEMAX`         | Maximum allowed value                       |
| `ARRAY`            | Array parameter definition (see below)      |

## Common Parameter Types

| Type             | GDL Equivalent | Description               |
|------------------|----------------|---------------------------|
| `Length`         | numeric        | Dimension in meters       |
| `Angle`          | numeric        | Rotation angle in degrees |
| `Real`           | numeric        | Real number (unitless)    |
| `Integer`        | numeric        | Integer value             |
| `Boolean`        | numeric        | 0 or 1 flag               |
| `Material`       | material       | ArchiCAD material         |
| `Pen`            | integer        | Pen number (1-255)        |
| `Line`           | integer        | Line type index           |
| `Fill`           | integer        | Fill pattern index        |
| `String`         | string         | Text value                |

## Array Parameters

```xml
<PARAMETER name="values" type="Real">
    <ARRAY length="8" dimension="1"/>
</PARAMETER>
```

Arrays can be multi-dimensional:

```xml
<ARRAY length="12" dimension="2" dim1="4" dim2="3"/>
```

Access in GDL: `values[1]`, `values[2][3]`, etc.

## Examples

### Simple rectangular column

```xml
<PARAMETERS product="ArchiCAD" version="1">
    <PARAMETER name="A" type="Length">
        <VALUE>0.40</VALUE>
        <RANGEMIN>0.10</RANGEMIN>
        <RANGEMAX>2.00</RANGEMAX>
    </PARAMETER>
    <PARAMETER name="B" type="Length">
        <VALUE>0.40</VALUE>
        <RANGEMIN>0.10</RANGEMIN>
        <RANGEMAX>2.00</RANGEMAX>
    </PARAMETER>
    <PARAMETER name="ZZYZX" type="Length">
        <VALUE>3.00</VALUE>
        <RANGEMIN>0.50</RANGEMIN>
        <RANGEMAX>15.00</RANGEMAX>
    </PARAMETER>
    <PARAMETER name="hasBase" type="Boolean">
        <VALUE>1</VALUE>
    </PARAMETER>
</PARAMETERS>
```

### With materials and dropdown

```xml
<PARAMETERS product="ArchiCAD" version="1">
    <PARAMETER name="frameMat" type="Material">
        <VALUE>1</VALUE>
    </PARAMETER>
    <PARAMETER name="style" type="Integer">
        <VALUE>1</VALUE>
        <RANGEMIN>1</RANGEMIN>
        <RANGEMAX>3</RANGEMAX>
    </PARAMETER>
</PARAMETERS>
```

## Edge Cases & Traps

- **No VALUE element**: defaults to 0. This may produce invisible geometry if the parameter controls a dimension.
- **RANGEMIN > RANGEMAX**: ArchiCAD clamps the parameter but the behavior is undefined — always ensure `RANGEMIN ≤ RANGEMAX`.
- **Empty paramlist.xml**: if the file exists but is empty, ArchiCAD treats the object as non-parametric (no UI parameters).
- **Type mismatch**: defining a parameter as `String` but trying to use it in a numeric context fails silently (evaluates to 0).
- **Name collision**: two parameters with the same `name` attribute — ArchiCAD uses only the first definition.
- **Unused parameters**: defining parameters that no script references is wasteful but harmless. Missing parameters that ARE referenced causes runtime warnings.

## Related

- [[PRISM_]] — using A, B, ZZYZX parameters for geometry
- [[HOTSPOT2]] — linking hotspots to parameters declared in paramlist.xml
- [[IF_ENDIF]] — branching on Boolean parameters
