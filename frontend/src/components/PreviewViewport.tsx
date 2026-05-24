import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { BufferAttribute, BufferGeometry, DoubleSide } from 'three'
import type { PreviewMesh, PreviewPayload } from '../api/types'

interface PreviewViewportProps {
  preview: PreviewPayload | null
  warnings: string[]
}

export function PreviewViewport({ preview, warnings }: PreviewViewportProps) {
  return (
    <section className="preview-stage">
      <div className="preview-toolbar">
        <div>
          <strong>Live 3D Preview</strong>
          <span>参数变化实时响应，保存动作独立</span>
        </div>
        <div className="viewport-tabs">
          <button className="active">3D</button>
          <button>2D</button>
        </div>
      </div>
      <div className="canvas-wrap">
        <Canvas camera={{ position: [2.8, -3.6, 2.4], fov: 42 }}>
          <color attach="background" args={['#f7f9fb']} />
          <ambientLight intensity={0.8} />
          <directionalLight position={[3, -4, 5]} intensity={2.2} />
          <gridHelper args={[4, 8, '#94a3b8', '#d8e0ea']} rotation={[Math.PI / 2, 0, 0]} />
          <axesHelper args={[1.4]} />
          {preview?.meshes.map((mesh, index) => <MeshView key={`${mesh.name}-${index}`} mesh={mesh} index={index} />)}
          <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
        </Canvas>
      </div>
      <footer className="preview-footer">
        <span>{preview?.meshes.length ?? 0} meshes</span>
        <span>{warnings.length ? `${warnings.length} warnings` : 'no warnings'}</span>
      </footer>
    </section>
  )
}

function MeshView({ mesh, index }: { mesh: PreviewMesh; index: number }) {
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(mesh.vertices.flat()), 3))
  geometry.setIndex(new BufferAttribute(new Uint32Array(mesh.faces.flat()), 1))
  geometry.computeVertexNormals()

  const colors = ['#0ea5e9', '#22c55e', '#f97316', '#8b5cf6', '#14b8a6']
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={colors[index % colors.length]} roughness={0.68} metalness={0.02} side={DoubleSide} />
    </mesh>
  )
}
