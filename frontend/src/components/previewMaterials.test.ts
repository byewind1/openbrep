import { describe, expect, test } from 'vitest'
import { materialForMesh } from './previewMaterials'
import type { PreviewMesh, PreviewPayload } from '../api/types'

const mesh: PreviewMesh = { name: 'top', vertices: [], faces: [], material_id: 'mat_top' }

describe('materialForMesh', () => {
  test('resolves the semantic material by symbolic parameter id', () => {
    const preview: PreviewPayload = {
      meshes: [mesh], wires: [],
      materials: {
        mat_top: { color: '#A87848', roughness: 0.62, metalness: 0, opacity: 1, transmission: 0, ior: 1.5 },
      },
    }

    expect(materialForMesh(preview, mesh)).toMatchObject({ color: '#A87848', roughness: 0.62 })
  })

  test('keeps old payloads and unbound mesh material-free', () => {
    expect(materialForMesh({ meshes: [mesh], wires: [] }, mesh)).toBeNull()
    expect(materialForMesh({ meshes: [mesh], wires: [], materials: {} }, mesh)).toBeNull()
  })
})
