---
type: concept
status: stable
tags: [3d, geometry, group, body, hierarchy]
aliases: [GROUP, group, body, sub-body, nesting]
source: raw/ccgdl_dev_doc/docs/GDL_02_Shapes.md
---

# GROUP

`GROUP` ... `ENDGROUP` creates a **named group** within a GDL body. Groups allow you to organize geometry hierarchically, apply attributes to sub-sections of a body, and control visibility at a finer granularity than the body level.

## Syntax

```gdl
GROUP "name" [, pen, material]
  ... geometry ...
ENDGROUP
```

| Param      | Type        | Description                            |
|------------|-------------|----------------------------------------|
| `"name"`   | string      | Group identifier (for reference)       |
| `pen`      | integer     | Optional — pen override for the group  |
| `material` | integer     | Optional — material override           |

## How Groups Work

- Groups exist **inside a body** (between `BODY` and `END`).
- Groups can contain primitives ([[PRISM_]], [[BLOCK]], [[CYLIND]], etc.), transform operations, and **nested groups**.
- Attributes set on the group (`pen`, `material`) override the outer body's attributes for geometry inside.
- Groups **do not** perform CSG union/intersection — they are a logical organization tool, not a Boolean operation.

## Example

### Basic grouping

```gdl
BODY 1
  GROUP "main_body", 3, 52
    PRISM_ 4, 2.0,
        0, 0, 1,
        1, 0, 1,
        1, 1, 1,
        0, 1, 1
    CYLIND 0.5, 0.5, 0.2, 0.2, 1.5
  ENDGROUP

  GROUP "base", 5, 44
    BLOCK 0, 0, -0.2, 1.2, 1.2, 0.2
  ENDGROUP
END
```

### Nested groups

```gdl
GROUP "assembly", 1, 1
  GROUP "left_wing"
    PRISM_ ...
  ENDGROUP
  GROUP "right_wing"
    PRISM_ ...
  ENDGROUP
ENDGROUP
```

## Group vs Body

| Feature          | `BODY`                    | `GROUP`                     |
|------------------|---------------------------|-----------------------------|
| Scope            | Top-level geometry unit   | Sub-division within a body  |
| Can contain      | Groups + geometry         | Groups + geometry           |
| Creates solid    | Yes (CSG-compatible)      | No (logical only)           |
| Attribute scope  | Entire body               | Nested group only           |
| Nesting          | No (sequential bodies)    | Yes (deeply nested)         |

## Edge Cases & Traps

- **Empty group**: a group with no geometry compiles but produces no visible result.
- **Attribute leak**: attributes set on a group do NOT persist after `ENDGROUP` — the parent body/group attributes resume.
- **Naming**: group names are strings. Duplicate names are allowed but make debugging harder. Names are **not** referenced by any GDL command — they are for human readability only.
- **No CSG**: unlike bodies, groups do not participate in Boolean union/intersection. All geometry in a group merges additively with the rest of the body.
- **Performance**: excessive groups inside a body slow down ArchiCAD's rendering engine. Use them for organization, not as a substitute for separate bodies.

## Related

- [[BODY_EDGE_PGON]] — bodies and the BODY/END construct
- [[IF_ENDIF]] — conditional group visibility
- [[PRISM_]] — common geometry inside groups
