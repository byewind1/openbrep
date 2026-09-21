import type { PreviewMaterial, PreviewMesh, PreviewPayload } from '../api/types'

export function materialForMesh(preview: PreviewPayload, mesh: PreviewMesh): PreviewMaterial | null {
  const id = mesh.material_id
  if (!id || !preview.materials) return null
  return preview.materials[id] ?? null
}
