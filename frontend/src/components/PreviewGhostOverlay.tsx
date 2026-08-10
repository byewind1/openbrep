import { useMemo } from 'react'
import { DoubleSide } from 'three'
import type { PreviewMesh, PreviewPayload } from '../api/types'
import { buildMeshGeometry, GHOST_MATERIAL_COLOR, GHOST_MATERIAL_OPACITY } from './previewGhost'

interface PreviewGhostOverlayProps {
  /** 任务前版本预览（store 的 previewGhost） */
  ghost: PreviewPayload
  /** 当前模型的 bounds.center：两个 payload 同世界坐标系，减同一中心即对齐 */
  boundsCenter: [number, number, number]
}

/**
 * 修改前后对比 ghost 叠加（P2a）：任务前版本以半透明单色材质叠加渲染。
 * - 不挂 onClick → r3f 只对带 handler 的 object raycast，ghost 天然不参与拾取、
 *   也不吞 onPointerMissed 清选中；
 * - 不进 fit bounds / 部件面板 / 剖切（不挂 clippingPlanes，剖切只看当前模型）；
 * - 恒定单色半透明，不随 displayMode 变化；
 * - renderOrder 排在当前模型之后（transparent 本就后画，双保险）；
 * - depthWrite=false，与当前模型叠加时不做深度写入，避免遮挡后侧模型。
 */
export function PreviewGhostOverlay({ ghost, boundsCenter }: PreviewGhostOverlayProps) {
  return (
    <group renderOrder={10}>
      {ghost.meshes.map((mesh, index) => (
        <GhostMesh key={`${mesh.name}-${index}`} mesh={mesh} center={boundsCenter} />
      ))}
    </group>
  )
}

function GhostMesh({ mesh, center }: { mesh: PreviewMesh; center: [number, number, number] }) {
  const geometry = useMemo(() => buildMeshGeometry(mesh, center), [mesh, center])
  return (
    <mesh geometry={geometry} renderOrder={10}>
      <meshBasicMaterial
        color={GHOST_MATERIAL_COLOR}
        transparent
        opacity={GHOST_MATERIAL_OPACITY}
        depthWrite={false}
        side={DoubleSide}
      />
    </mesh>
  )
}
