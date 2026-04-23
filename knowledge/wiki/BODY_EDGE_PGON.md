---
type: concept
status: stable
tags: [mesh, body, vertex, edge, polygon, 3d, geometry, brep]
aliases: [BODY, EDGE, PGON, body edge pgon, mesh commands, gdl mesh]
source: raw/ccgdl_dev_doc/docs/GDL_10_3D_Commands_Full.md
---

# BODY_EDGE_PGON

`BODY`, `EDGE`, and `PGON` are GDL's low-level mesh construction commands. They let you define 3D geometry vertex-by-vertex and face-by-face — the BREP (Boundary Representation) approach. Where [[PRISM_]] handles only extruded polygons, `BODY/EDGE/PGON` can represent arbitrary manifold meshes.

## Why BODY/EDGE/PGON?

High-level primitives like [[BLOCK]] and [[PRISM_]] are fast and convenient, but they can only produce axis-aligned boxes and extruded polygons. When you need:

- A tetrahedron or irregular polyhedron
- A curved surface triangulated into facets
- A shape with non-planar faces
- Imported geometry from a 3D model format

`BODY/EDGE/PGON` is the escape hatch. Most GDL objects never need these commands — but when you do, there is no alternative.

## Workflow

Building a mesh requires three steps:

1. **Define vertices** with `BODY` (assigns index numbers to coordinate triplets)
2. **Define edges** with `EDGE` (connects two vertex indices)
3. **Define faces** with `PGON` (references edge indices that form a closed loop)

```gdl
BODY 3,
    0, 0, 0,      ! vertex index 1
    1, 0, 0,      ! vertex index 2
    0, 0, 1       ! vertex index 3

EDGE 3,
    1, 2,         ! edge 1: vertex 1 → vertex 2
    2, 3,         ! edge 2: vertex 2 → vertex 3
    3, 1          ! edge 3: vertex 3 → vertex 1

PGON 1,
    1, 2, 3       ! face: edges 1, 2, 3 (counter-clockwise)
```

## Full Example: Tetrahedron

```gdl
! A tetrahedron with 4 vertices, 6 edges, 4 faces
BODY 4,
    0,   0,   0,     ! V1: origin
    1.0, 0,   0,     ! V2
    0.5, 0.866, 0,   ! V3
    0.5, 0.289, 0.817 ! V4 (apex)

EDGE 6,
    1, 2,             ! E1: base edge
    2, 3,             ! E2: base edge
    3, 1,             ! E3: base edge
    1, 4,             ! E4: side edge
    2, 4,             ! E5: side edge
    3, 4              ! E6: side edge

! Bottom face (counter-clockwise when viewed from below)
PGON -1, 1, 1, 2, 3
! Side faces
PGON -1, 1, 4, 5, 1
PGON -1, 1, 5, 6, 2
PGON -1, 1, 6, 4, 3
```

The first argument to `PGON` is a negative status byte (`-1`) + edge count, followed by edge indices. CCW winding defines outward-facing normals.

## Edge Cases & Traps

- **Vertex order**: vertices must be defined before they are referenced by edges.
- **Edge order**: edges must be defined before they are referenced by faces.
- **Winding**: face normals follow the right-hand rule along the CCW edge loop. Wrong winding produces inward-facing or invisible faces.
- **Manifold requirement**: each edge must be shared by exactly two faces. Non-manifold edges (used by 1 or 3+ faces) cause undefined rendering.
- **Performance**: meshes with thousands of vertices are significantly slower than equivalent [[PRISM_]] or [[BLOCK]] calls. Use higher-level primitives when possible.
- **No built-in triangulation**: GDL does not auto-triangulate concave or non-planar faces. You must split them manually.
- **Negative status in PGON**: the sign of the first PGON argument is used as a status flag. `-1` means "valid face", `1` would mean "invisible/wireframe".

## Related

- [[PRISM_]] — simpler extrusion-based geometry (prefer when applicable)
- [[BLOCK]] — simplest box primitive
- [[Transformation_Stack]] — positioning meshes in world space
