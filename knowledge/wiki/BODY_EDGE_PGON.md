---
type: concept
status: stable
tags: [mesh, body, vertex, edge, polygon, 3d, geometry, brep]
aliases: [BODY, VERT, EDGE, PGON, body edge pgon, mesh commands, primitive elements, gdl mesh]
source: official:gdl.graphisoft.com/reference-guide/primitive-elements
---

# BODY / VERT / EDGE / PGON

`VERT`, `EDGE`, `PGON`, and `BODY` are GDL primitive elements for low-level boundary representation. They are powerful but fragile. OpenBrep must not use them as default generation commands for ordinary furniture, doors, windows, cabinets, or shelves.

Prefer [[BLOCK]], [[PRISM_]], [[CYLIND]], [[REVOLVE]], or [[SWEEP]] whenever the shape can be described by high-level primitives.

## Official Syntax

```gdl
VERT x, y, z

EDGE vert1, vert2, pgon1, pgon2, status

PGON n, vect, status, edge1, edge2, ..., edgen

BODY status
```

Related optional primitive:

```gdl
VECT x, y, z
```

## Required Order

For simplified primitive definitions, the official recommended order is:

```text
VERT / TEVE
EDGE / VECT
PGON / PIPG
COOR
BODY
```

`BODY` composes the preceding primitive definitions into one body. It is not a vertex container.

## Minimal Simplified Example

This creates one triangular surface. It is an open surface, not a production solid.

```gdl
VERT 0, 0, 0
VERT 1, 0, 0
VERT 0, 1, 0

EDGE 1, 2, -1, -1, 0
EDGE 2, 3, -1, -1, 0
EDGE 3, 1, -1, -1, 0

PGON 3, 0, -1, 1, 2, 3
BODY -1
```

Use `vect = 0` and negative `status` only when you intentionally let the engine calculate polygon/body status. For production solids, define closed bodies carefully or use higher-level commands.

## Recommended OpenBrep Use

- Mark as advanced / non-default knowledge for AI generation.
- Use only when importing explicit mesh data or when a shape cannot be expressed by high-level GDL commands.
- Require an explicit reason in the generation plan before using primitive elements.
- For furniture and building object generation, prefer parametric high-level solids.
- Do not use primitive elements just to model boxes, shelves, frames, cylinders, revolved profiles, or simple extrusions.

## Edge Cases & Traps

- `BODY` syntax is `BODY status`; it does not accept vertex coordinates.
- `EDGE` syntax requires two vertex indices, two neighboring polygon indices, and status.
- `PGON` syntax requires `n`, `vect`, `status`, then edge indices.
- Referenced primitives must already exist.
- Indexing is reset by `BASE` or a new body.
- A closed body requires each edge to have two adjacent polygons.
- Open or non-manifold bodies can cut, render, and hide-line poorly.
- Wrong edge signs or winding can flip polygon normals.
- Primitive meshes are usually slower and less maintainable than high-level GDL solids.

## Related

- [[PRISM_]] — preferred for extruded polygon profiles
- [[BLOCK]] — preferred for box-like members
- [[REVOLVE]] — preferred for rotational profiles
- [[SWEEP]] — preferred for swept profiles
