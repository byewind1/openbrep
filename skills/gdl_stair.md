---
name: gdl-stair
description: "GDL stair generation strategy for straight parametric stairs, handrails, and spiral stairs."
version: 1.0.0
tags: [gdl, stair, spiral-stair, handrail, archicad, openbrep]
---

# Skill: GDL Stair Objects

Use this skill when the user asks for a stair, staircase, spiral stair, parametric stair, handrail, railing, tread, riser, stair pitch, 楼梯, 螺旋楼梯, 参数化楼梯, 扶手, 栏杆, 踏步, or 踢面.

This skill distills the stair patterns from two GDL Cookbook stair notes:

- 螺旋楼梯
- 参数化楼梯：带扶手

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
