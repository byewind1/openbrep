import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import type { ReactNode } from 'react'
import { BufferAttribute, BufferGeometry, DoubleSide } from 'three'
import type { PreviewMesh, PreviewPayload } from '../api/types'

interface PreviewViewportProps {
  preview: PreviewPayload | null
  warnings: string[]
  actions?: ReactNode
  variant?: 'rail' | 'floating'
}

export function PreviewViewport({ preview, warnings, actions, variant = 'rail' }: PreviewViewportProps) {
  return (
    <section className={`viewport-surface viewport-surface-${variant}`}>
      <div className="viewport-toolbar">
        <div>
          <span>3D View</span>
          <span>{preview?.meshes.length ?? 0} meshes</span>
        </div>
        {actions ? <div className="viewport-toolbar-actions">{actions}</div> : null}
      </div>
      <div className="canvas-wrap">
        <Canvas camera={{ position: [2.8, -3.6, 2.4], fov: 42 }}>
          <color attach="background" args={['#05070d']} />
          <ambientLight intensity={0.55} />
          <directionalLight position={[3, -4, 5]} intensity={2.6} />
          <directionalLight position={[-4, 2, 3]} intensity={0.8} color="#38bdf8" />
          <gridHelper args={[4, 8, '#334155', '#182235']} rotation={[Math.PI / 2, 0, 0]} />
          <axesHelper args={[1.4]} />
          {preview?.meshes.map((mesh, index) => <MeshView key={`${mesh.name}-${index}`} mesh={mesh} index={index} />)}
          <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
        </Canvas>
      </div>
      <footer className="viewport-footer">
        <span>
          {preview?.meshes.length ?? 0} meshes | {warnings.length} warnings
        </span>
      </footer>
    </section>
  )
}

function MeshView({ mesh, index }: { mesh: PreviewMesh; index: number }) {
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(mesh.vertices.flat()), 3))
  geometry.setIndex(new BufferAttribute(new Uint32Array(mesh.faces.flat()), 1))
  geometry.computeVertexNormals()

  const colors = ['#d6a04f', '#a8a29e', '#94a3b8', '#64748b', '#8b7355']
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={colors[index % colors.length]} roughness={0.68} metalness={0.02} side={DoubleSide} />
    </mesh>
  )
}
