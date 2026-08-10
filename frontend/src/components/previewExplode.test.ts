import { BufferAttribute, BufferGeometry } from 'three'
import { describe, expect, test } from 'vitest'
import type { PreviewMesh } from '../api/types'
import {
  buildExplodedParts,
  clampFactor,
  computeFaceComponents,
  explodeOffset,
  overallCentroid,
  partCentroids,
  shouldSplitParts,
} from './previewExplode'

// 两个不连通的四面体：comp0 在原点附近，comp1 沿 +X 偏移 100
const MESH: PreviewMesh = {
  name: 'two-boxes',
  vertices: [
    [0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10], // comp 0
    [100, 0, 0], [110, 0, 0], [100, 10, 0], [100, 0, 10], // comp 1
  ],
  faces: [
    [0, 1, 2], [0, 3, 1],
    [4, 5, 6], [4, 7, 5],
  ],
}

const OFFSET: [number, number, number] = [10, 20, 30]

describe('computeFaceComponents (P2b 提级)', () => {
  test('assigns disconnected faces to distinct components in appearance order', () => {
    expect(computeFaceComponents(MESH.faces, MESH.vertices.length)).toEqual([0, 0, 1, 1])
  })
})

describe('partCentroids (P2b)', () => {
  test('computes vertex-mean centroid per component, centered by offset', () => {
    const parts = partCentroids(MESH, OFFSET)
    expect(parts).toHaveLength(2)
    expect(parts[0]).toEqual({ compId: 0, centroid: [-7.5, -17.5, -27.5] })
    expect(parts[1]).toEqual({ compId: 1, centroid: [92.5, -17.5, -27.5] })
  })
})

describe('overallCentroid (P2b)', () => {
  test('averages all vertices of all meshes, centered by offset', () => {
    expect(overallCentroid([MESH], OFFSET)).toEqual([42.5, -17.5, -27.5])
  })

  test('empty mesh list yields zero vector', () => {
    expect(overallCentroid([], OFFSET)).toEqual([0, 0, 0])
  })
})

describe('clampFactor / explodeOffset (P2b)', () => {
  test('clampFactor bounds to [0,1]; non-finite falls back to 0', () => {
    expect(clampFactor(0.5)).toBe(0.5)
    expect(clampFactor(2)).toBe(1)
    expect(clampFactor(-0.3)).toBe(0)
    expect(clampFactor(Number.NaN)).toBe(0)
    expect(clampFactor(Number.POSITIVE_INFINITY)).toBe(0)
    expect(clampFactor(Number.NEGATIVE_INFINITY)).toBe(0)
  })

  test('explodeOffset moves each part away from the overall centroid', () => {
    const overall = overallCentroid([MESH], OFFSET) // [42.5, -17.5, -27.5]
    const [p0] = partCentroids(MESH, OFFSET)
    const [p1] = partCentroids(MESH, OFFSET).slice(1)
    expect(explodeOffset(p0.centroid, overall, 0.5)).toEqual([-25, 0, 0])
    expect(explodeOffset(p1.centroid, overall, 0.5)).toEqual([25, 0, 0])
  })

  test('factor 0 / 1 clamp at the boundaries', () => {
    const overall = overallCentroid([MESH], OFFSET)
    const [p0] = partCentroids(MESH, OFFSET)
    expect(explodeOffset(p0.centroid, overall, 0)).toEqual([0, 0, 0])
    expect(explodeOffset(p0.centroid, overall, 1)).toEqual([-50, 0, 0])
    expect(explodeOffset(p0.centroid, overall, 5)).toEqual([-50, 0, 0])
    expect(explodeOffset(p0.centroid, overall, -1)).toEqual([0, 0, 0])
  })

  test('degenerate: part centroid equals overall centroid → zero displacement', () => {
    expect(explodeOffset([1, 2, 3], [1, 2, 3], 0.8)).toEqual([0, 0, 0])
    // 单部件整体：部件质心 == 整体质心 → 不散架
    const single: PreviewMesh = { name: 'one', vertices: MESH.vertices.slice(0, 4), faces: MESH.faces.slice(0, 2) }
    const overall = overallCentroid([single], OFFSET)
    const [part] = partCentroids(single, OFFSET)
    expect(explodeOffset(part.centroid, overall, 1)).toEqual([0, 0, 0])
  })

  test('NaN factor is a no-op (zero displacement)', () => {
    const overall = overallCentroid([MESH], OFFSET)
    const [p0] = partCentroids(MESH, OFFSET)
    expect(explodeOffset(p0.centroid, overall, Number.NaN)).toEqual([0, 0, 0])
  })
})

describe('shouldSplitParts (P2b)', () => {
  test('factor=0 with non-random modes keeps the whole-mesh render path', () => {
    expect(shouldSplitParts(0, 'solid')).toBe(false)
    expect(shouldSplitParts(0, 'wire')).toBe(false)
    expect(shouldSplitParts(0, 'xray')).toBe(false)
    expect(shouldSplitParts(0, 'mono')).toBe(false)
  })

  test('random mode always splits; any positive factor splits too', () => {
    expect(shouldSplitParts(0, 'random')).toBe(true)
    expect(shouldSplitParts(0.5, 'solid')).toBe(true)
    expect(shouldSplitParts(0.01, 'wire')).toBe(true)
  })
})

describe('buildExplodedParts (P2b)', () => {
  test('shares the whole geometry position attribute, splits indices per component', () => {
    const whole = new BufferGeometry()
    whole.setAttribute('position', new BufferAttribute(new Float32Array(MESH.vertices.flat()), 3))
    const parts = buildExplodedParts(MESH, OFFSET, whole)
    expect(parts).toHaveLength(2)
    parts.forEach((part) => {
      expect(part.geometry.getAttribute('position')).toBe(whole.getAttribute('position'))
      expect(part.geometry.getIndex()).not.toBeNull()
      expect(part.geometry.getAttribute('normal')).toBeDefined()
    })
    expect(Array.from(parts[0].geometry.getIndex()?.array ?? [])).toEqual([0, 1, 2, 0, 3, 1])
    expect(Array.from(parts[1].geometry.getIndex()?.array ?? [])).toEqual([4, 5, 6, 4, 7, 5])
    expect(parts[0].centroid).toEqual([-7.5, -17.5, -27.5])
  })
})
