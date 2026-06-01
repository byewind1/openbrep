import { describe, expect, test } from 'vitest'
import {
  computePreviewBounds,
  orthographicZoomForBounds,
  perspectiveDistanceForBounds,
  type PreviewBounds,
  viewDirectionForPreset,
  viewUpForPreset,
} from './previewCamera'

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

  test('fits perspective camera farther back in narrow preview rails', () => {
    const bounds: PreviewBounds = { center: [0, 0, 0], size: [4, 4, 4], radius: 4 }

    const narrowDistance = perspectiveDistanceForBounds(bounds, 300, 640)
    const wideDistance = perspectiveDistanceForBounds(bounds, 640, 640)

    expect(narrowDistance).toBeGreaterThan(wideDistance)
  })

  test('reduces orthographic zoom when the preview rail is narrow', () => {
    const bounds: PreviewBounds = { center: [0, 0, 0], size: [4, 4, 4], radius: 4 }

    const narrowZoom = orthographicZoomForBounds(bounds, 300, 640)
    const wideZoom = orthographicZoomForBounds(bounds, 640, 640)

    expect(narrowZoom).toBeLessThan(wideZoom)
  })
})
