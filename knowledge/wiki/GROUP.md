---
type: concept
status: stable
tags: [3d, geometry, group, boolean, solid-operation]
aliases: [GROUP, ENDGROUP, PLACEGROUP, KILLGROUP, ADDGROUP, SUBGROUP, ISECTGROUP, ISECTLINES, SWEEPGROUP]
source: official:gdl.graphisoft.com/reference-guide/solid-geometry-commands
---

# GROUP

`GROUP` / `ENDGROUP` defines a named group of 3D bodies for solid geometry operations. The bodies inside the group are not generated in the model until the group is explicitly placed with `PLACEGROUP`.

OpenBrep should treat group commands as advanced, non-default GDL. For ordinary furniture, casework, and simple building objects, prefer high-level primitives such as [[BLOCK]], [[PRISM_]], [[CYLIND]], [[REVOLVE]], and [[SWEEP]].

## Official Syntax

```gdl
GROUP "name"
    statement1
    ...
ENDGROUP
```

Related solid geometry commands:

```gdl
ADDGROUP (g_expr1, g_expr2)
SUBGROUP (g_expr1, g_expr2)
ISECTGROUP (g_expr1, g_expr2)
ISECTLINES (g_expr1, g_expr2)
PLACEGROUP g_expr
KILLGROUP g_expr
SWEEPGROUP (g_expr, x, y, z)
```

## Recommended OpenBrep Use

- Use `GROUP` only when the task explicitly needs Boolean union, difference, intersection, intersection lines, or sweeping a body.
- Define groups with unique names inside the current script.
- Always `PLACEGROUP` the final group expression that should appear in the model.
- `KILLGROUP` temporary groups after they are no longer needed.
- Avoid group operations in first-pass generation unless a simpler primitive approach would clearly fail.

## Example: Boolean Difference

```gdl
GROUP "box"
    BLOCK A, B, ZZYZX
ENDGROUP

GROUP "cut"
    ADD A / 2, B / 2, ZZYZX / 2
    SPHERE cut_r
    DEL 1
ENDGROUP

_result = SUBGROUP("box", "cut")
PLACEGROUP _result
KILLGROUP "box"
KILLGROUP "cut"
KILLGROUP _result
```

## Edge Cases & Traps

- Group definitions are not visible geometry by themselves.
- `PLACEGROUP` is required to generate geometry from a group expression.
- Group definitions cannot be nested.
- Group names must be unique inside the current script.
- Transformations and cutplanes outside a group definition do not affect group parts; transformations and cutplanes inside a group do not affect bodies outside the group.
- Attribute `DEFINE` and `SET` statements are transparent across group definitions.
- `ADDGROUP`, `SUBGROUP`, `ISECTGROUP`, and `ISECTLINES` return group expressions; they do not place geometry by themselves.
- `KILLGROUP` clears group bodies from memory. Killed group names cannot be reused in the same script.

## Related

- [[CUTPLANE]] — simpler 3D trimming when a full Boolean group is not needed
- [[BODY_EDGE_PGON]] — primitive body definitions used by lower-level modeling
- [[SWEEP]] — profile sweep without group Boolean workflow
