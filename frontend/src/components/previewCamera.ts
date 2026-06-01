import type { PreviewPayload } from '../api/types'

export type PreviewCameraMode = 'perspective' | 'orthographic'
export type PreviewViewPreset = 'iso' | 'top' | 'front' | 'right'

export interface PreviewBounds {
  center: [number, number, number]
  size: [number, number, number]
  radius: number
}

export const PREVIEW_CAMERA_FOV_DEGREES = 38

export function perspectiveDistanceForBounds(bounds: PreviewBounds, viewportWidth: number, viewportHeight: number): number {
  const safeWidth = Math.max(viewportWidth, 1)
  const safeHeight = Math.max(viewportHeight, 1)
  const aspect = safeWidth / safeHeight
  const verticalFov = degreesToRadians(PREVIEW_CAMERA_FOV_DEGREES)
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * aspect)
  const fitFov = Math.max(Math.min(verticalFov, horizontalFov), degreesToRadians(1))
  return Math.max((bounds.radius / Math.sin(fitFov / 2)) * 1.18, 2.5)
}

export function orthographicZoomForBounds(bounds: PreviewBounds, viewportWidth: number, viewportHeight: number): number {
  const safeViewportMin = Math.max(Math.min(viewportWidth, viewportHeight), 1)
  const maxWorldSize = Math.max(bounds.size[0], bounds.size[1], bounds.size[2], 0.5)
  return Math.max(28, safeViewportMin / (maxWorldSize * 1.65))
}

export function computePreviewBounds(preview: PreviewPayload | null): PreviewBounds {
  const points = preview?.meshes.flatMap((mesh) => mesh.vertices) ?? []
  if (points.length === 0) {
    return {
      center: [0, 0, 0],
      size: [2, 2, 2],
      radius: 2,
    }
  }

  const min = [...points[0]] as [number, number, number]
  const max = [...points[0]] as [number, number, number]
  for (const point of points) {
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], point[axis])
      max[axis] = Math.max(max[axis], point[axis])
    }
  }

  const size: [number, number, number] = [
    Math.max(max[0] - min[0], 0.01),
    Math.max(max[1] - min[1], 0.01),
    Math.max(max[2] - min[2], 0.01),
  ]
  const center: [number, number, number] = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ]
  const radius = Math.max(Math.hypot(size[0], size[1], size[2]) / 2, 0.5)
  return { center, size, radius }
}

export function viewDirectionForPreset(preset: PreviewViewPreset): [number, number, number] {
  switch (preset) {
    case 'top':
      return [0, 0, 1]
    case 'front':
      return [0, -1, 0]
    case 'right':
      return [1, 0, 0]
    case 'iso':
    default:
      return [4, -6, 4]
  }
}

export function viewUpForPreset(preset: PreviewViewPreset): [number, number, number] {
  return preset === 'top' ? [0, 1, 0] : [0, 0, 1]
}

function degreesToRadians(degrees: number): number {
  return (degrees * Math.PI) / 180
}
