import { describe, expect, test } from 'vitest'
import { computePreviewBounds, viewDirectionForPreset, viewUpForPreset } from './previewCamera'

describe('preview camera helpers', () => {
  test('computes bounds from preview meshes', () => {
    const bounds = computePreviewBounds({
      meshes: [
        {
          name: 'box',
          vertices: [
            [0, 0, 0],
            [2, 4, 6],
          ],
          faces: [[0, 1, 1]],
        },
      ],
      wires: [],
      warnings: [],
    })

    expect(bounds.center).toEqual([1, 2, 3])
    expect(bounds.size).toEqual([2, 4, 6])
    expect(bounds.radius).toBeGreaterThan(3)
  })

  test('uses a perspective-friendly isometric direction by default', () => {
    expect(viewDirectionForPreset('iso')).toEqual([4, -6, 4])
    expect(viewUpForPreset('iso')).toEqual([0, 0, 1])
  })

  test('uses a non-collinear up vector for top view', () => {
    expect(viewDirectionForPreset('top')).toEqual([0, 0, 1])
    expect(viewUpForPreset('top')).toEqual([0, 1, 0])
  })
})
