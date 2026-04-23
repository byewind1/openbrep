---
type: concept
status: stable
tags: [2d, projection, cutplane, section, floor-plan]
aliases: [CUTPLANE, cutplane, cut plane, section plane, floor plan cut, cut_plane]
source: raw/ccgdl_dev_doc/docs/GDL_02_Shapes.md
---

# CUTPLANE

`CUTPLANE` controls the **cut plane height** for 2D floor plan projections. It determines at what height the 3D model is sliced to produce the 2D symbol view. This is critical for doors, windows, columns, and any object that needs to display differently above vs. below the cut plane.

## Syntax

```gdl
CUTPLANE x, y, z, nx, ny, nz
```

| Param    | Type    | Description                              |
|----------|---------|------------------------------------------|
| `x,y,z`  | numeric | A point on the cut plane                 |
| `nx,ny,nz`| numeric | Normal vector defining plane orientation |

## How It Works

1. When ArchiCAD generates the 2D projection of an object, it intersects the 3D geometry with the cut plane defined in the **master script**.
2. The intersection produces a 2D section that appears in the floor plan.
3. The default cut plane for floor plans is typically **1.00 m** (or 1000 mm) above the story zero, depending on regional standards and Project Preferences.

## Example

### Standard floor plan cut

```gdl
! Cut at 1.0 m above floor, horizontal plane
CUTPLANE 0, 0, 1.0, 0, 0, 1
```

### Vertical section

```gdl
! Vertical cut along XZ plane
CUTPLANE 0, 0, 0, 0, 1, 0
```

## Object Type Defaults

For objects inserted into building elements, ArchiCAD uses:

| Object Type | Default Cut Behavior                              |
|-------------|---------------------------------------------------|
| Column      | Cut plane passes through center (always shown)    |
| Beam        | Cut plane strikes top edge (shown in outline)     |
| Wall        | Cut at story-specific cut plane height            |
| Door/Window | Cut plane intersects at mid-height (sill + open)  |
| Slab        | Cut plane passes below (shown in plan)            |

## Combined with PROJECT2

[[PROJECT2]] generates the actual 2D projection using the current cut plane:

```gdl
! Master script
CUTPLANE 0, 0, 1.0, 0, 0, 1

! 2D script — uses the cut plane from master
PROJECT2 0, 3, 4
```

## Edge Cases & Traps

- **No CUTPLANE defined**: ArchiCAD uses its default floor plan cut height (typically 1.0 m). This may not match your object's intended display logic.
- **Cut plane above object**: if the cut plane is above the object's top, no cut geometry appears in the 2D plan (object shown dashed or not at all).
- **Cut plane below object**: only the outline/shadow appears, no cut fill.
- **Multiple CUTPLANE definitions**: only the **last** `CUTPLANE` in the master script takes effect. Defining it in 2D script has no effect on the projection.
- **Normal direction**: the normal should point **upward** (positive Z) for floor plans. An inverted normal flips which side of the cut is visible.
- **Unit mismatch**: ensure the Z-coordinate matches your project units (meters vs. millimeters).

## Related

- [[PROJECT2]] — generating 2D projection from 3D
- [[IF_ENDIF]] — conditional display logic based on cut plane
- [[BLOCK]] — geometry that may appear differently in cut vs. elevation
