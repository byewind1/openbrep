import { describe, expect, test } from 'vitest'
import type { Preview2DPayload } from '../api/types'
import {
  clampView2D,
  computeBounds2D,
  fitView2D,
  panBy,
  pixelsToViewBox,
  toViewBoxString,
  zoomAt,
  type View2D,
} from './preview2dView'

function samplePreview(): Preview2DPayload {
  return {
    lines: [{ from: [0, 0], to: [4, 0] }],
    polygons: [[[0, 0], [2, 0], [2, 2], [0, 2]]],
    circles: [{ cx: 1, cy: 1, r: 0.5 }],
    arcs: [{ cx: 0, cy: 0, r: 3, a0: 0, a1: 90 }],
  }
}

describe('preview2dView pure math (P3c)', () => {
  test('computeBounds2D covers lines/polygons/circles/arcs', () => {
    const bounds = computeBounds2D(samplePreview())
    expect(bounds).not.toBeNull()
    expect(bounds!.minX).toBeCloseTo(-3, 9) // arc 外接盒
    expect(bounds!.maxX).toBeCloseTo(4, 9)
    expect(bounds!.minY).toBeCloseTo(-3, 9)
    expect(bounds!.maxY).toBeCloseTo(3, 9) // circle (1,1,r=.5) + arc r=3
  })

  test('computeBounds2D returns null for empty payload', () => {
    expect(computeBounds2D(null)).toBeNull()
    expect(computeBounds2D({ lines: [], polygons: [], circles: [], arcs: [] })).toBeNull()
  })

  test('fitView2D pads by 8% of the max dimension and floors tiny extents', () => {
    const fit = fitView2D({ minX: 0, minY: 0, maxX: 2, maxY: 1 })
    // max extent 2 → pad 0.16
    expect(fit.minX).toBeCloseTo(-0.16, 9)
    expect(fit.minY).toBeCloseTo(-0.16, 9)
    expect(fit.width).toBeCloseTo(2.32, 9)
    expect(fit.height).toBeCloseTo(1.32, 9)

    const flat = fitView2D({ minX: 0, minY: 0, maxX: 0, maxY: 0 })
    expect(flat.width).toBeGreaterThanOrEqual(0.1)
    expect(flat.height).toBeGreaterThanOrEqual(0.1)
  })

  test('toViewBoxString formats svg viewBox', () => {
    expect(toViewBoxString({ minX: -1, minY: -2, width: 3, height: 4 })).toBe('-1 -2 3 4')
  })

  test('zoomAt keeps the anchor point at the same viewport fraction', () => {
    const view: View2D = { minX: 0, minY: 0, width: 10, height: 10 }
    const cx = 3
    const cy = 4
    const zoomed = zoomAt(view, cx, cy, 2)
    // 锚点归一化坐标不变
    expect((cx - view.minX) / view.width).toBeCloseTo((cx - zoomed.minX) / zoomed.width, 9)
    expect((cy - view.minY) / view.height).toBeCloseTo((cy - zoomed.minY) / zoomed.height, 9)
    expect(zoomed.width).toBeCloseTo(5, 9)
    expect(zoomed.height).toBeCloseTo(5, 9)
  })

  test('zoomAt ignores non-positive or non-finite factors', () => {
    const view: View2D = { minX: 0, minY: 0, width: 10, height: 10 }
    expect(zoomAt(view, 1, 1, 0)).toBe(view)
    expect(zoomAt(view, 1, 1, Number.NaN)).toBe(view)
  })

  test('panBy shifts the view origin only', () => {
    const panned = panBy({ minX: 0, minY: 0, width: 10, height: 10 }, 2, -3)
    expect(panned).toEqual({ minX: 2, minY: -3, width: 10, height: 10 })
  })

  test('clampView2D limits zoom and preserves center', () => {
    const fit: View2D = { minX: 0, minY: 0, width: 10, height: 10 }
    const tiny = clampView2D({ minX: 4.95, minY: 4.95, width: 0.1, height: 0.1 }, fit)
    expect(tiny.width).toBeCloseTo(10 * 0.02, 9) // 被 minScale 拦住
    expect(tiny.minX + tiny.width / 2).toBeCloseTo(5, 9) // 中心不变
    expect(tiny.minY + tiny.height / 2).toBeCloseTo(5, 9)

    const huge = clampView2D({ minX: -500, minY: -500, width: 1000, height: 1000 }, fit)
    expect(huge.width).toBeCloseTo(10 * 20, 9) // 被 maxScale 拦住
    expect(huge.minX + huge.width / 2).toBeCloseTo(0, 9)
  })

  test('pixelsToViewBox converts pixel deltas into viewBox units', () => {
    const view: View2D = { minX: 0, minY: 0, width: 100, height: 50 }
    expect(pixelsToViewBox(view, 10, 5, 100, 50)).toEqual({ dx: 10, dy: 5 })
    expect(pixelsToViewBox(view, 10, 5, 0, 0)).toEqual({ dx: 0, dy: 0 })
  })
})
