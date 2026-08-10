import { describe, expect, test } from 'vitest'
import type { PreviewMesh, PreviewPayload } from '../api/types'
import { buildMeshGeometry, GHOST_MATERIAL_COLOR, GHOST_MATERIAL_OPACITY, isGhostAvailable } from './previewGhost'

const MESH: PreviewMesh = {
  name: 'box',
  vertices: [
    [0, 0, 0],
    [10, 0, 0],
    [0, 10, 0],
    [0, 0, 10],
  ],
  faces: [[0, 1, 2], [0, 3, 1]],
}

describe('previewGhost (P2a)', () => {
  test('isGhostAvailable accepts payloads and rejects null/undefined', () => {
    const payload: PreviewPayload = { meshes: [], wires: [] }
    expect(isGhostAvailable(payload)).toBe(true)
    expect(isGhostAvailable(null)).toBe(false)
    expect(isGhostAvailable(undefined)).toBe(false)
  })

  test('ghost material params are a constant cold blue-gray translucent', () => {
    expect(GHOST_MATERIAL_COLOR).toMatch(/^#[0-9a-f]{6}$/)
    expect(GHOST_MATERIAL_OPACITY).toBeGreaterThan(0)
    expect(GHOST_MATERIAL_OPACITY).toBeLessThan(1)
  })

  test('buildMeshGeometry centers vertices by offset and indexes faces', () => {
    const geometry = buildMeshGeometry(MESH, [10, 20, 30])
    const pos = geometry.getAttribute('position')
    expect(pos.count).toBe(4)
    expect(Array.from(pos.array)).toEqual([
      -10, -20, -30,
      0, -20, -30,
      -10, -10, -30,
      -10, -20, -20,
    ])
    expect(Array.from(geometry.getIndex()?.array ?? [])).toEqual([0, 1, 2, 0, 3, 1])
    expect(geometry.getAttribute('normal')).toBeDefined()
  })

  test('buildMeshGeometry with a zero offset keeps raw coordinates (identity)', () => {
    const geometry = buildMeshGeometry(MESH, [0, 0, 0])
    const pos = geometry.getAttribute('position')
    expect(Array.from(pos.array.slice(0, 3))).toEqual([0, 0, 0])
    expect(Array.from(pos.array.slice(3, 6))).toEqual([10, 0, 0])
  })
})
