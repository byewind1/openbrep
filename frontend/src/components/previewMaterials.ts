import type { PreviewMaterial, PreviewMesh, PreviewPayload } from '../api/types'
import type { ColorRepresentation } from 'three'

export interface PreviewMaterialStyle {
  kind: 'standard' | 'physical'
  properties: {
    color: ColorRepresentation
    roughness: number
    metalness: number
    transparent: boolean
    opacity: number
    transmission: number
    ior: number
  }
}

export function materialForMesh(preview: PreviewPayload, mesh: PreviewMesh): PreviewMaterial | null {
  const id = mesh.material_id
  if (!id || !preview.materials) return null
  return preview.materials[id] ?? preview.materials[id.toLowerCase()] ?? preview.materials[id.toUpperCase()] ?? null
}

export function semanticMaterialStyle(material: PreviewMaterial | null | undefined, neutral = false): PreviewMaterialStyle {
  return {
    kind: !neutral && Boolean(material?.transmission) ? 'physical' : 'standard',
    properties: {
      color: neutral ? '#a6adb8' : material?.color ?? '#8595ab',
      roughness: neutral ? 0.8 : material?.roughness ?? 0.5,
      metalness: neutral ? 0 : material?.metalness ?? 0.05,
      transparent: !neutral && Boolean(material && material.opacity < 1),
      opacity: neutral ? 1 : material?.opacity ?? 1,
      transmission: neutral ? 0 : material?.transmission ?? 0,
      ior: material?.ior ?? 1.5,
    },
  }
}
