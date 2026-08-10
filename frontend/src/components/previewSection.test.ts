import { describe, expect, test } from 'vitest'
import type { PreviewBounds } from './previewCamera'
import {
  axisIndex,
  axisVector,
  clampT,
  localCoordForT,
  sectionPlaneParams,
  tForLocalCoord,
} from './previewSection'

const bounds: PreviewBounds = { center: [10, 20, 30], size: [4, 8, 2], radius: 5 }

describe('axis helpers', () => {
  test('axisVector maps x/y/z to unit normals', () => {
    expect(axisVector('x')).toEqual([1, 0, 0])
    expect(axisVector('y')).toEqual([0, 1, 0])
    expect(axisVector('z')).toEqual([0, 0, 1])
  })

  test('axisIndex maps x/y/z to 0/1/2', () => {
    expect(axisIndex('x')).toBe(0)
    expect(axisIndex('y')).toBe(1)
    expect(axisIndex('z')).toBe(2)
  })

  test('clampT clamps to [0,1]', () => {
    expect(clampT(0)).toBe(0)
    expect(clampT(1)).toBe(1)
    expect(clampT(0.5)).toBe(0.5)
    expect(clampT(-1)).toBe(0)
    expect(clampT(2)).toBe(1)
    expect(clampT(Number.NaN)).toBe(0)
    expect(clampT(Number.POSITIVE_INFINITY)).toBe(1)
  })
})

describe('t ↔ 局部坐标换算（模型居中，跨度 -size/2..+size/2）', () => {
  test('localCoordForT maps 0→-size/2 and 1→+size/2', () => {
    expect(localCoordForT(bounds, 'x', 0)).toBe(-2)
    expect(localCoordForT(bounds, 'x', 1)).toBe(2)
    expect(localCoordForT(bounds, 'y', 0)).toBe(-4)
    expect(localCoordForT(bounds, 'y', 1)).toBe(4)
    expect(localCoordForT(bounds, 'z', 0.5)).toBe(0)
  })

  test('roundtrip t → coord → t is stable', () => {
    for (const axis of ['x', 'y', 'z'] as const) {
      for (const t of [0, 0.1, 0.33, 0.5, 0.77, 0.9, 1]) {
        expect(tForLocalCoord(bounds, axis, localCoordForT(bounds, axis, t))).toBeCloseTo(t, 9)
      }
    }
  })

  test('out-of-range coords clamp to [0,1]', () => {
    expect(tForLocalCoord(bounds, 'x', -10)).toBe(0)
    expect(tForLocalCoord(bounds, 'x', 10)).toBe(1)
    expect(tForLocalCoord(bounds, 'x', -2)).toBe(0)
    expect(tForLocalCoord(bounds, 'x', 2)).toBe(1)
  })

  test('zero-size axis degrades to t=0', () => {
    const flat: PreviewBounds = { center: [0, 0, 0], size: [0, 2, 2], radius: 1 }
    expect(tForLocalCoord(flat, 'x', 0)).toBe(0)
  })
})

describe('sectionPlaneParams（world 空间剖切平面）', () => {
  test('normal points against the axis (clips the negative side)', () => {
    const p = sectionPlaneParams(bounds, { axis: 'x', t: 0.5 })
    expect(p.normal).toEqual([-1, 0, 0])
    // t=0.5 → localCoord=0 → worldCoord = 0 + center.x = 10
    expect(p.constant).toBe(10)
  })

  test('t=0 puts the plane at the min edge: nothing clipped', () => {
    const p = sectionPlaneParams(bounds, { axis: 'y', t: 0 })
    // localCoord = -4 → worldCoord = -4 + 20 = 16
    expect(p.constant).toBe(16)
    // 模型 y 世界范围 16..24，平面在 16：dot(n,p)+c > 0 ⇔ y < 16 被裁 → 无裁切
    expect(p.normal).toEqual([0, -1, 0])
  })

  test('t=1 puts the plane at the max edge: everything clipped', () => {
    const p = sectionPlaneParams(bounds, { axis: 'y', t: 1 })
    expect(p.constant).toBe(24)
  })

  test('plane sweeps from min to max as t grows (keep the positive side)', () => {
    const at0 = sectionPlaneParams(bounds, { axis: 'z', t: 0 })
    const at1 = sectionPlaneParams(bounds, { axis: 'z', t: 1 })
    // world z 范围 29..31
    expect(at0.constant).toBe(29)
    expect(at1.constant).toBe(31)
    // 平面位置的坐标 = constant（normal 为 -axis 时）
  })
})
