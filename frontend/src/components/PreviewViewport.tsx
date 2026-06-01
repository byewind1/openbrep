import { Edges, OrbitControls, OrthographicCamera, PerspectiveCamera } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Camera, OrthographicCamera as OrthographicCameraType, PerspectiveCamera as PerspectiveCameraType } from 'three'
import { BufferAttribute, BufferGeometry, DoubleSide, Vector3 } from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { PreviewMesh, PreviewPayload } from '../api/types'
import {
  computePreviewBounds,
  orthographicZoomForBounds,
  perspectiveDistanceForBounds,
  PREVIEW_CAMERA_FOV_DEGREES,
  viewDirectionForPreset,
  viewUpForPreset,
  type PreviewBounds,
  type PreviewCameraMode,
  type PreviewViewPreset,
} from './previewCamera'

interface PreviewViewportProps {
  preview: PreviewPayload | null
  warnings: string[]
  actions?: ReactNode
  variant?: 'rail' | 'floating' | 'workspace'
  expanded?: boolean
  onExpand?: () => void
  onCollapse?: () => void
  onFloat?: () => void
}

export function PreviewViewport({
  preview,
  warnings,
  actions,
  variant = 'rail',
  expanded = false,
  onExpand,
  onCollapse,
  onFloat,
}: PreviewViewportProps) {
  const [cameraMode, setCameraMode] = useState<PreviewCameraMode>('perspective')
  const [viewPreset, setViewPreset] = useState<PreviewViewPreset>('iso')
  const [fitNonce, setFitNonce] = useState(0)
  const [showEdges, setShowEdges] = useState(true)
  const [showGrid, setShowGrid] = useState(true)
  const bounds = useMemo(() => computePreviewBounds(preview), [preview])

  function fitView() {
    setFitNonce((value) => value + 1)
  }

  function resetView() {
    setCameraMode('perspective')
    setViewPreset('iso')
    setShowEdges(true)
    setShowGrid(true)
    fitView()
  }

  return (
    <section className={`viewport-surface viewport-surface-${variant}`}>
      <div className="viewport-toolbar">
        <div>
          <strong>3D View</strong>
          <span>{preview?.meshes.length ?? 0} meshes</span>
        </div>
        <div className="viewport-toolbar-actions">
          <button type="button" className="viewport-action-button" onClick={fitView} title="Fit model to view">
            Fit
          </button>
          <button type="button" className="viewport-action-button" onClick={resetView} title="Reset camera and layers">
            Reset
          </button>
          <ViewportPresetButton preset="iso" activePreset={viewPreset} onSelect={setViewPreset} />
          <ViewportPresetButton preset="top" activePreset={viewPreset} onSelect={setViewPreset} />
          <ViewportPresetButton preset="front" activePreset={viewPreset} onSelect={setViewPreset} />
          <ViewportPresetButton preset="right" activePreset={viewPreset} onSelect={setViewPreset} />
          <button
            type="button"
            className={`viewport-action-button${cameraMode === 'orthographic' ? ' active' : ''}`}
            onClick={() => setCameraMode((mode) => (mode === 'perspective' ? 'orthographic' : 'perspective'))}
            title="Toggle orthographic inspection mode"
          >
            {cameraMode === 'perspective' ? 'Persp' : 'Ortho'}
          </button>
          <button
            type="button"
            className={`viewport-action-button${showEdges ? ' active' : ''}`}
            onClick={() => setShowEdges((value) => !value)}
            title="Toggle mesh edges"
          >
            Edges
          </button>
          <button
            type="button"
            className={`viewport-action-button${showGrid ? ' active' : ''}`}
            onClick={() => setShowGrid((value) => !value)}
            title="Toggle construction grid"
          >
            Grid
          </button>
          {onFloat ? (
            <button type="button" className="viewport-action-button" onClick={onFloat} title="Open floating preview">
              Float
            </button>
          ) : null}
          {expanded ? (
            <button type="button" className="viewport-action-button" onClick={onCollapse} title="Return to script editor">
              Dock
            </button>
          ) : onExpand ? (
            <button type="button" className="viewport-action-button" onClick={onExpand} title="Expand preview to main workspace">
              Expand
            </button>
          ) : null}
          {actions}
        </div>
      </div>
      <div className="canvas-wrap">
        <Canvas>
          {cameraMode === 'perspective' ? (
            <PerspectiveCamera makeDefault fov={38} near={0.001} far={100000} />
          ) : (
            <OrthographicCamera makeDefault near={0.001} far={100000} />
          )}
          <PreviewCameraRig bounds={bounds} mode={cameraMode} preset={viewPreset} fitNonce={fitNonce} />
          <color attach="background" args={['#05070d']} />
          <ambientLight intensity={0.58} />
          <directionalLight position={[3, -4, 5]} intensity={2.7} />
          <directionalLight position={[-4, 2, 3]} intensity={0.72} color="#38bdf8" />
          {showGrid ? <gridHelper args={[4, 8, '#334155', '#182235']} rotation={[Math.PI / 2, 0, 0]} /> : null}
          <axesHelper args={[1.4]} />
          {preview?.meshes.map((mesh, index) => (
            <MeshView key={`${mesh.name}-${index}`} mesh={mesh} index={index} showEdges={showEdges} />
          ))}
        </Canvas>
      </div>
      <footer className="viewport-footer">
        <span>
          {cameraMode === 'orthographic' ? 'Orthographic' : 'Perspective'} | {viewPreset.toUpperCase()}
        </span>
        <span>
          {preview?.meshes.length ?? 0} meshes | {warnings.length} warnings
        </span>
      </footer>
    </section>
  )
}

function ViewportPresetButton({
  preset,
  activePreset,
  onSelect,
}: {
  preset: PreviewViewPreset
  activePreset: PreviewViewPreset
  onSelect: (preset: PreviewViewPreset) => void
}) {
  const label = preset === 'iso' ? 'ISO' : preset[0].toUpperCase() + preset.slice(1)
  return (
    <button
      type="button"
      className={`viewport-action-button${activePreset === preset ? ' active' : ''}`}
      onClick={() => onSelect(preset)}
      title={`Set ${label} view`}
    >
      {label}
    </button>
  )
}

function PreviewCameraRig({
  bounds,
  mode,
  preset,
  fitNonce,
}: {
  bounds: PreviewBounds
  mode: PreviewCameraMode
  preset: PreviewViewPreset
  fitNonce: number
}) {
  const controlsRef = useRef<OrbitControlsImpl | null>(null)
  const { camera, size } = useThree()

  useEffect(() => {
    fitCamera(camera, bounds, preset, mode, size.width, size.height)
    controlsRef.current?.target.set(...bounds.center)
    controlsRef.current?.update()
  }, [camera, bounds, preset, mode, fitNonce, size.width, size.height])

  return <OrbitControls ref={controlsRef} makeDefault enableDamping dampingFactor={0.08} screenSpacePanning={false} />
}

function fitCamera(
  camera: Camera,
  bounds: PreviewBounds,
  preset: PreviewViewPreset,
  mode: PreviewCameraMode,
  viewportWidth: number,
  viewportHeight: number,
) {
  const center = new Vector3(...bounds.center)
  const direction = new Vector3(...viewDirectionForPreset(preset)).normalize()
  const up = new Vector3(...viewUpForPreset(preset)).normalize()
  const distance = perspectiveDistanceForBounds(bounds, viewportWidth, viewportHeight)
  const projectionCamera = camera as PerspectiveCameraType | OrthographicCameraType

  projectionCamera.up.copy(up)
  projectionCamera.position.copy(center).add(direction.multiplyScalar(distance))
  projectionCamera.lookAt(center)

  if (mode === 'orthographic') {
    const ortho = projectionCamera as OrthographicCameraType
    ortho.zoom = orthographicZoomForBounds(bounds, viewportWidth, viewportHeight)
  } else {
    const perspective = projectionCamera as PerspectiveCameraType
    perspective.fov = PREVIEW_CAMERA_FOV_DEGREES
  }

  projectionCamera.near = 0.001
  projectionCamera.far = Math.max(100000, distance * 100)
  projectionCamera.updateProjectionMatrix()
}

function MeshView({ mesh, index, showEdges }: { mesh: PreviewMesh; index: number; showEdges: boolean }) {
  const geometry = useMemo(() => {
    const next = new BufferGeometry()
    next.setAttribute('position', new BufferAttribute(new Float32Array(mesh.vertices.flat()), 3))
    next.setIndex(new BufferAttribute(new Uint32Array(mesh.faces.flat()), 1))
    next.computeVertexNormals()
    return next
  }, [mesh.faces, mesh.vertices])

  const colors = ['#d6a04f', '#a8a29e', '#94a3b8', '#64748b', '#8b7355']
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={colors[index % colors.length]} roughness={0.68} metalness={0.02} side={DoubleSide} />
      {showEdges ? <Edges color="#111827" threshold={18} /> : null}
    </mesh>
  )
}
