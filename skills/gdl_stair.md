---
name: gdl-stair
description: "GDL stair generation strategy for straight parametric stairs, handrails, spiral stairs, and document-derived stair concepts."
version: 1.1.0
tags: [gdl, stair, spiral-stair, handrail, put-get, prism, bprism, tube, archicad, openbrep]
---

# Skill: GDL Stair Objects

Use this skill when the user asks for a stair, staircase, spiral stair, parametric stair, handrail, railing, tread, riser, stair pitch, 楼梯, 螺旋楼梯, 参数化楼梯, 扶手, 栏杆, 踏步, or 踢面.

This skill distills the stair patterns from two GDL Cookbook stair notes:

- 螺旋楼梯
- 参数化楼梯：带扶手

Document-to-skill extraction rule: keep reusable concepts, methods, parameter contracts, code patterns, and failure checks. Do not paste long source prose. The skill should help the model infer how to generate a new stair, not merely repeat one historical example.

## Activation Keywords

- stair
- staircase
- spiral stair
- parametric stair
- handrail
- railing
- tread
- riser
- pitch
- 楼梯
- 螺旋楼梯
- 参数化楼梯
- 扶手
- 栏杆
- 踏步
- 踢面

## Goal

Generate compile-stable GDL stair objects by modelling the whole stair as a parametric body, not as many hard-coded steps. Use loop-driven `PUT` / `GET` point buffers so riser count, tread depth, riser height, stair width, handrail options, and spiral radius remain editable.

## Document-Derived Concepts

This skill is meant to preserve reusable concepts and methods from source documents, not just final code snippets. When generating a stair object, first identify which concept applies, then choose the GDL pattern.

| Concept | Use When | GDL Method | Core Risk |
|---|---|---|---|
| Whole stair as one body | Straight parametric stair with repeated treads | `PUT` / `GET` profile + `PRISM` | Hard-coded steps create excess edges and do not scale |
| Point buffer as geometry generator | Variable riser count or variable spiral segments | `PUT` in `FOR ... NEXT`, then `GET(NSP)` | Wrong `NSP / 2` vs `NSP / 3` count |
| Raked riser | Tread riser edge leans back | Offset one buffered point by `rak` or `rakd` | Negative tread overlap or invalid profile |
| Stair orientation | Profile is easier to draw in local XY | `ADDX`, `ADDY`, `ROTX`, `ROTY` before draw | Unbalanced transform stack |
| Handrail configuration | No/left/right/both rails | `hrconf` value list -> `hrstyl` flag | UI value not translated into script logic |
| Tube rail | Rail follows a path with round section | `TUBE` with circle section and 4 path nodes | Missing overshoot nodes, faceted rail |
| Simple rail | Straight sloped handrail only | `SQR`, `ATN`, `ROTX`, `CYLIND` | Divide by zero when run length is zero |
| Spiral stair body | Spiral or curved stair with inner well | `bPRISM_` with negative depth | Using normal `PRISM` loses the negative-depth trick |
| 2D usability | Object must be selectable/stretchable in plan | `HOTSPOT2` + `PROJECT2` | Projection without hotspots is hard to edit |
| Pitch display | User wants slope/pitch annotation | `ATN`, `DEFINE STYLE`, `TEXT2` | Text size not scaled to stair geometry |

### Pattern Card: Whole Stair PRISM

Inputs:

- `numrisr`, `riser`, `going`, `rak`, `width`.

Outputs:

- One continuous stair body with sloped underside.

Method:

- Start the profile at the lower origin.
- Add a first tread-depth point.
- Add the high endpoint based on `numrisr`.
- Walk from top riser to bottom riser with `FOR N = numrisr TO 1 STEP -1`.
- Close the profile.
- Emit `PRISM NSP / 2, width, GET(NSP)`.

Failure checks:

- `NSP` must be even before `PRISM NSP / 2`.
- `going` and `riser` must be positive.
- `rak` must not make a riser cross the adjacent tread.

### Pattern Card: Spiral bPRISM_

Inputs:

- `A`, `width`, `numrisr`, `going`, `riser`, `rakd`.

Outputs:

- A spiral stair body around a central well.

Method:

- Shift origin by `ADDY -A / 2`.
- Rotate with `ROTX 90`.
- Fill `PUT` buffer with X/Y/status triples.
- Use `bPRISM_ matl, matl, matl, NSP / 3, -width, A / 2, GET(NSP)`.

Failure checks:

- `NSP` must divide by 3.
- `width` must not exceed `A / 2.01`.
- `rakd` should be clamped to `going / 3`.

### Pattern Card: TUBE Handrail

Inputs:

- `hrht`, `hrdiam`, `numrisr`, `going`, `riser`, `width`, `hrmatl`.

Outputs:

- Smooth round handrail following the stair pitch.

Method:

- Define a circular section with two section records.
- Define four path nodes: overshoot start, true start, true end, overshoot end.
- Use `RESOL 12` or higher before `TUBE`.
- Add `4000 + 1` in the section record for a smooth circular rail.

Failure checks:

- Missing overshoot nodes can produce poor rail ends.
- Rail side offset must include `hrdiam / 2`.
- Transform stack must be cleaned after the rail.

### Pattern Card: CYLIND Handrail

Inputs:

- `hrht`, `hrdiam`, `numrisr`, `going`, `riser`, `width`, `hrmatl`.

Outputs:

- Simple straight sloped cylindrical handrail.

Method:

- `hgoing = numrisr * going`.
- `hriser = numrisr * riser`.
- `hrailen = SQR(hgoing ^ 2 + hriser ^ 2)`.
- `hrailang = ATN(hriser / hgoing)`.
- Rotate and place a `CYLIND hrailen, hrdiam / 2`.

Failure checks:

- Guard `hgoing > 0` before `ATN(hriser / hgoing)`.
- Use `ROTX -90 + hrailang` after positioning at `startht`.
- Clean exactly the transforms added in the subroutine.

### Pattern Card: 2D Projection With Hotspots

Inputs:

- `A`, `width`, `leng`, `pitc`, `stret`, `shodata`.

Outputs:

- Selectable and optionally annotated plan symbol.

Method:

- Place origin, end, width-edge, and stretch hotspots.
- Use `PROJECT2 3, 270, 2` after hotspots.
- Add direction lines or pitch text only after projection requirements are satisfied.

Failure checks:

- Do not rely on `PROJECT2` alone.
- Do not calculate text size from an unguarded denominator.

## Method Selection

Choose the generation method before writing scripts. The method is part of the skill, not an afterthought.

| User intent | Primary body method | Rail method | 2D method | Notes |
|---|---|---|---|---|
| straight parametric stair | `PUT` / `GET` + `PRISM` side profile | optional `TUBE` or `CYLIND` | hotspots + `PROJECT2` | Best default for editable straight runs. |
| stair with clean sloped underside | one continuous `PRISM` body | optional | hotspots + `PROJECT2` | Avoid separate tread blocks. |
| schematic/blocky stair | repeated `BLOCK` only if requested | optional simple `CYLIND` | manual lines acceptable | Lower fidelity draft only. |
| spiral stair | `PUT` / `GET` + `bPRISM_` | rectangular `bPRISM_` or helical `TUBE` | spiral hotspots + `PROJECT2` | `bPRISM_` can handle negative depth. |
| round handrail following a slope/path | stair body unchanged | `TUBE` | unchanged | Use overshoot points at path ends. |
| simple straight handrail | stair body unchanged | `CYLIND` | unchanged | Compute length with `SQR`; angle with `ATN`. |
| show pitch in plan | unchanged | unchanged | `TEXT2` with `STR(pitc, 4, 2)` | Guard `going > 0`. |

Rules:

- If the user asks for a straight or generic parametric stair, use the straight `PRISM` profile method.
- If the user asks for a spiral stair, use `bPRISM_` and the spiral point buffer method.
- If the user asks for railings with a circular or continuous tube, use `TUBE`.
- If the user asks for a simple straight rail, use `CYLIND` with calculated length and angle.
- If the user asks for both sides, write one rail method and mirror it where practical instead of duplicating logic.
- If the user asks for only a quick placeholder, a repeated `BLOCK` stair is allowed only as a temporary draft and must be labelled schematic.

## Generation Workflow

Use this sequence for a complete HSF project:

1. `paramlist.xml`: declare all public parameters, including stair body, rail, material, pen, 2D display, and optional spiral parameters.
2. `scripts/1d.gdl`: validate minimum values, derive width, total run, total rise, pitch, handrail flags, and guard denominators.
3. `scripts/3d.gdl`: keep the main body short; call subroutines for stair body, left rail, right rail, and optional posts.
4. `scripts/2d.gdl`: place hotspots first, then `PROJECT2`; add optional direction arrow and pitch label.
5. `scripts/pr.gdl`: constrain value lists and numeric ranges when possible.
6. `scripts/ui.gdl`: only add custom UI if explicitly requested; do not let UI script block compile-stable geometry.

## Parameter Contract

Always define parameters before geometry. Use these names unless the user requests a different schema:

- `A`: stair width or spiral stair outer diameter, depending on object type.
- `B`: footprint depth for straight stairs, or use as a secondary plan dimension when needed.
- `ZZYZX`: overall height when the stair object needs height control.
- `numrisr`: integer riser count, minimum 1.
- `riser`: riser height.
- `going`: tread depth.
- `rak`: raked riser offset for straight stairs.
- `width`: derived width in `1d.gdl`, usually `width = A`.
- `hrconf`: handrail choice, values: no handrail, left, right, both.
- `hrstyl`: numeric handrail flag derived from `hrconf`.
- `hrht`: handrail height above stair.
- `hrdiam`: handrail diameter.
- `hrmatl`: handrail material.
- `matl`: stair body material.
- `pcol`: pen color.
- Spiral-specific: `rakd`, `hrhit`, `stret`.

Minimum validation in `1d.gdl`:

```gdl
IF A < 0.30 THEN A = 0.30
IF numrisr < 1 THEN numrisr = 1
IF riser <= 0 THEN riser = 0.17
IF going <= 0 THEN going = 0.28
IF rak < 0 THEN rak = 0
IF hrdiam <= 0 THEN hrdiam = 0.04
IF hrht <= 0 THEN hrht = 0.90
```

For spiral stairs, constrain impossible well and rake values:

```gdl
IF width > A / 2 THEN width = A / 2.01
IF rakd > going / 3 THEN rakd = going / 3
```

Starter `paramlist.xml` shape:

```xml
<Length Name="A"><Fix/><Value>1.20</Value></Length>
<Length Name="B"><Fix/><Value>2.80</Value></Length>
<Length Name="ZZYZX"><Fix/><Value>1.70</Value></Length>
<Integer Name="numrisr"><Value>10</Value></Integer>
<Length Name="riser"><Value>0.17</Value></Length>
<Length Name="going"><Value>0.28</Value></Length>
<Length Name="rak"><Value>0.00</Value></Length>
<String Name="hrconf"><Value>Both handrails</Value></String>
<Length Name="hrht"><Value>0.90</Value></Length>
<Length Name="hrdiam"><Value>0.04</Value></Length>
<Material Name="matl"><Value>0</Value></Material>
<Material Name="hrmatl"><Value>0</Value></Material>
<PenColor Name="pcol"><Value>1</Value></PenColor>
<Boolean Name="shodata"><Value>0</Value></Boolean>
```

Derived variables should be created in `1d.gdl`, not guessed inside every script:

```gdl
width = A
leng = numrisr * going
total_rise = numrisr * riser
IF going <= 0 THEN going = 0.28
pitc = ATN(riser / going)
```

Rules:

- Do not invent aliases like `steps`, `height`, `depth`, or `rail_height` unless they are also declared in `paramlist.xml`.
- Prefer one public parameter and one derived variable over repeatedly recomputing the same expression in 3D and 2D.
- Keep `A` stretch semantics clear: for straight stair `A` is width; for spiral stair `A` is outer diameter.
- If both straight and spiral variants are supported in one object, add a mode parameter and branch explicitly.

## Straight Parametric Stair Strategy

Do not generate each tread as a separate `BLOCK` unless the user explicitly asks for a very schematic object. The preferred straight stair body is one `PRISM` profile extruded by stair width.

Use a subroutine-based 3D structure:

```gdl
MATERIAL matl
PEN pcol
width = A

GOSUB 200  ! parametric stair body

IF hrstyl = 1 OR hrstyl = 3 THEN GOSUB 300  ! left handrail
IF hrstyl = 2 OR hrstyl = 3 THEN GOSUB 400  ! right handrail
END
```

Build the stair body with `PUT` / `GET`, not fixed coordinate lists:

```gdl
200:
  ADDX width / 2
  ROTY -90

  PUT 0, 0
  PUT 0, going
  PUT (numrisr - 1) * riser, numrisr * going

  FOR N = numrisr TO 1 STEP -1
      PUT N * riser, N * going
      PUT N * riser, (N - 1) * going - rak
  NEXT N
  PUT 0, 0

  PRISM NSP / 2, width, GET(NSP)
  DEL 2
RETURN
```

Rules:

- `PRISM` point count is `NSP / 2` because each profile point has X/Y.
- Keep the stair in the positive X/Y working quadrant before rotations.
- Use `ROTY -90` to orient the profile into the final stair direction.
- Keep `ADD` / `ROT` / `DEL` balanced in each subroutine.
- Prefer one sloped underside body over repeated isolated step blocks.

## Handrail Strategy

Represent handrail choice as a user-facing value list and convert it to a numeric flag in `1d.gdl`.

Parameter script pattern:

```gdl
VALUES "hrconf" "No handrail",
                "Left handrail only",
                "Right handrail only",
                "Both handrails"
```

Master script pattern:

```gdl
IF hrconf = "No handrail" THEN hrstyl = 0
IF hrconf = "Left handrail only" THEN hrstyl = 1
IF hrconf = "Right handrail only" THEN hrstyl = 2
IF hrconf = "Both handrails" THEN hrstyl = 3
```

Use `TUBE` when the handrail needs a smooth sloped tube that follows a path:

```gdl
300:
  startht = hrht + riser
  endht = (numrisr + 1) * riser + hrht
  d = 0.01

  ADDX width / 2 - hrdiam / 2
  MATERIAL hrmatl
  RESOL 12
  TUBE 2, 4, 63,
      0, 0, 901,
      hrdiam / 2, 360, 4000 + 1,
      0, -d, startht - d, 0,
      0, 0, startht, 0,
      0, numrisr * going, endht, 0,
      0, numrisr * going + d, endht + d, 0
  DEL 1
RETURN
```

Use `CYLIND` when a simple straight sloped rail is enough:

```gdl
400:
  startht = hrht + riser
  hgoing = numrisr * going
  hriser = numrisr * riser
  hrailen = SQR(hgoing ^ 2 + hriser ^ 2)
  hrailang = ATN(hriser / hgoing)

  ADDX -width / 2 + hrdiam / 2
  ADDZ startht
  ROTX -90 + hrailang
  MATERIAL hrmatl
  RESOL 12
  CYLIND hrailen, hrdiam / 2
  DEL 3
RETURN
```

Rules:

- Use `TUBE` for a rail path; use `CYLIND` for a simple straight member.
- For mirrored rails, prefer one rail subroutine plus `MULX -1` when possible.
- Give `TUBE` path ends a small overshoot node so the rail miters cleanly.
- Guard `hgoing` before `ATN(hriser / hgoing)` if there is any chance it is zero.

## Spiral Stair Strategy

Use `bPRISM_` for spiral stair bodies because it can tolerate a negative depth. This matters when the stair well radius is smaller than the stair width.

3D structure:

```gdl
MATERIAL matl
PEN pcol
GOSUB 50   ! validation

ADDY -A / 2
ROTX 90
GOSUB 100  ! put spiral stair profile points

bPRISM_ matl, matl, matl,
    NSP / 3, -width, A / 2, GET(NSP)

IF hrhit > riser THEN GOSUB 200
DEL 2
END
```

Point buffer pattern:

```gdl
100:
  PUT 0, 0, 15
  PUT going, 0, 15
  PUT numrisr * going, (numrisr - 1) * riser, 15

  FOR N = numrisr TO 1 STEP -1
      PUT N * going + rakd, N * riser, 15
      PUT (N - 1) * going, N * riser, 15
  NEXT N
  PUT 0, 0, 15
RETURN
```

Spiral handrail can reuse the same `bPRISM_` path trick for a rectangular rail:

```gdl
200:
  PUT 0, 0, 15,
      0, 0.04, 15
  PUT numrisr * going, numrisr * riser + 0.04, 15,
      numrisr * going, numrisr * riser, 15

  ADDY hrhit
  bPRISM_ matl, matl, matl,
      NSP / 3, -0.04, A / 2, GET(NSP)
  DEL 1
RETURN
```

Rules:

- Use `NSP / 3` for spiral `bPRISM_` point count because each point has X/Y/status.
- Apply the initial `ADDY -A / 2` and `ROTX 90` before drawing the spiral body.
- Keep `width <= A / 2.01` so the stair well remains valid.
- If a round spiral rail is required, use a helical `TUBE` path instead of rectangular `bPRISM_`.

## 2D Symbol Strategy

Use `PROJECT2 3, 270, 2` as the default 2D representation unless the user asks for a fully hand-drawn symbolic plan. Always add hotspots before projection so the object remains easy to select and stretch.

Straight stair 2D pattern:

```gdl
leng = numrisr * going
pitc = ATN(riser / going)

HOTSPOT2 0, 0
HOTSPOT2 0, leng
HOTSPOT2 -A / 2, 0
HOTSPOT2 A / 2, 0

PEN pcol
PROJECT2 3, 270, 2
LINE2 0, 0, 0, leng
LINE2 0, leng, -A / 2, leng - A / 2
LINE2 0, leng, A / 2, leng - A / 2
```

Spiral stair 2D pattern:

```gdl
HOTSPOT2 0, 0
HOTSPOT2 0, -A / 2
HOTSPOT2 0, -A / 2 + width
HOTSPOT2 A / 2, 0
IF stret THEN HOTSPOT2 -A / 2, 0
PROJECT2 3, 270, 2
```

Optional pitch label:

```gdl
IF shodata THEN
    fontz = going * 1000 / A_
    DEFINE STYLE "show" "Arial", fontz, 4, 0
    SET STYLE "show"
    ROT2 90
    TEXT2 0, 0, STR(pitc, 4, 2)
ENDIF
```

## Common Pitfalls

- Do not hard-code a fixed number of steps. Use `numrisr` and a `FOR ... NEXT` loop.
- Do not create one object per tread for a normal stair body. It produces excess edges and poor rendering.
- Do not forget that `PRISM` needs a height/depth argument after the point count.
- Do not use `NSP / 3` for straight `PRISM`; straight profile points are X/Y, so use `NSP / 2`.
- Do not use `NSP / 2` for spiral `bPRISM_`; its buffered points include status, so use `NSP / 3`.
- Do not use `CYLIND` for complex handrail paths with bends. Use `TUBE`.
- Do not omit 2D hotspots when relying on `PROJECT2`.
- Do not leave `ADD` / `ROT` transforms unbalanced across subroutines.
- Do not divide by `going` or `hgoing` before validating them.

## Final Checklist

- [ ] `paramlist.xml` declares every parameter used in `1d.gdl`, `2d.gdl`, and `3d.gdl`.
- [ ] `numrisr`, `riser`, `going`, `hrdiam`, and `hrht` have safe defaults and minimum guards.
- [ ] Straight stair body uses `PUT` / `GET` + `PRISM`, with point count `NSP / 2`.
- [ ] Spiral stair body uses `PUT` / `GET` + `bPRISM_`, with point count `NSP / 3`.
- [ ] Handrail choices are controlled by `hrconf` / `hrstyl`.
- [ ] `TUBE` or `CYLIND` selection matches the handrail geometry.
- [ ] Every subroutine returns and balances transforms locally.
- [ ] 2D script includes hotspots plus `PROJECT2` or explicit symbolic drawing.
