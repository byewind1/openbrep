import { BufferAttribute, BufferGeometry } from 'three'
import type { PreviewMesh, PreviewPayload } from '../api/types'

/** ghost 材质参数（P2a）：冷灰蓝半透明，恒定单色，不随 displayMode 变化 */
export const GHOST_MATERIAL_COLOR = '#7c8db5'
export const GHOST_MATERIAL_OPACITY = 0.25

/** ghost 可用性：store 有快照才可对比（null/undefined 均视为无） */
export function isGhostAvailable(ghost: PreviewPayload | null | undefined): ghost is PreviewPayload {
  return ghost !== null && ghost !== undefined
}

/**
 * 几何构建共用函数（P2a）：把 mesh 顶点按 offset 居中（当前模型与 ghost 同世界
 * 坐标系，减去同一个 bounds.center 即对齐）。MeshView 与 PreviewGhostOverlay 共用，
 * 避免两处复制同一 BufferGeometry 构建逻辑。
 */
export function buildMeshGeometry(mesh: PreviewMesh, offset: [number, number, number]): BufferGeometry {
  const next = new BufferGeometry()
  const [ox, oy, oz] = offset
  next.setAttribute(
    'position',
    new BufferAttribute(
      new Float32Array(mesh.vertices.map(([x, y, z]) => [x - ox, y - oy, z - oz]).flat()),
      3,
    ),
  )
  next.setIndex(new BufferAttribute(new Uint32Array(mesh.faces.flat()), 1))
  next.computeVertexNormals()
  return next
}
