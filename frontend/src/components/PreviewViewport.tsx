import { Edges, OrbitControls, OrthographicCamera, PerspectiveCamera } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Camera, OrthographicCamera as OrthographicCameraType, PerspectiveCamera as PerspectiveCameraType } from 'three'
import { BufferAttribute, BufferGeometry, Color, DoubleSide, ShaderMaterial, Vector3 } from 'three'
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
  hasDirtyScripts?: boolean
}

type PreviewDisplayMode = 'solid' | 'random' | 'wire' | 'xray' | 'mono'

const DISPLAY_MODES: Array<{ id: PreviewDisplayMode; label: string; title: string }> = [
  { id: 'solid', label: '实体', title: 'Solid shaded, uniform color' },
  { id: 'random', label: '随机', title: 'Each part gets a distinct color' },
  { id: 'wire', label: '线框', title: 'Feature edges only (hidden line)' },
  { id: 'xray', label: 'X光', title: 'X-ray fresnel ghost' },
  { id: 'mono', label: '单色', title: 'Flat unlit single color' },
]

export function PreviewViewport({
  preview,
  warnings,
  actions,
  variant = 'rail',
  expanded = false,
  onExpand,
  onCollapse,
  onFloat,
  hasDirtyScripts = false,
}: PreviewViewportProps) {
  const [cameraMode, setCameraMode] = useState<PreviewCameraMode>('perspective')
  const [viewPreset, setViewPreset] = useState<PreviewViewPreset>('iso')
  const [fitNonce, setFitNonce] = useState(0)
  const [showEdges, setShowEdges] = useState(true)
  const [showGrid, setShowGrid] = useState(true)
  const [displayMode, setDisplayMode] = useState<PreviewDisplayMode>('solid')
  const bounds = useMemo(() => computePreviewBounds(preview), [preview])
  const sourceLabel = previewSourceLabel(preview, hasDirtyScripts)

  function fitView() {
    setFitNonce((value) => value + 1)
  }

  function resetView() {
    setCameraMode('perspective')
    setViewPreset('iso')
    setShowEdges(true)
    setShowGrid(true)
    setDisplayMode('solid')
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
          {DISPLAY_MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              className={`viewport-action-button${displayMode === mode.id ? ' active' : ''}`}
              onClick={() => setDisplayMode(mode.id)}
              title={mode.title}
            >
              {mode.label}
            </button>
          ))}
          <span className="viewport-toolbar-sep" aria-hidden="true" />
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
          <ambientLight intensity={0.7} />
          <directionalLight position={[3, -4, 5]} intensity={2.2} />
          <directionalLight position={[-4, 2, 3]} intensity={0.72} color="#38bdf8" />
          {showGrid ? <gridHelper args={[4, 8, '#334155', '#182235']} rotation={[Math.PI / 2, 0, 0]} /> : null}
          <axesHelper args={[1.4]} />
          {preview?.meshes.map((mesh, index) => (
            <MeshView key={`${mesh.name}-${index}`} mesh={mesh} index={index} showEdges={showEdges} displayMode={displayMode} />
          ))}
        </Canvas>
      </div>
      <footer className="viewport-footer">
        <span>
          {cameraMode === 'orthographic' ? 'Orthographic' : 'Perspective'} | {viewPreset.toUpperCase()}
        </span>
        <span className="viewport-fidelity-hint" title="The built-in previewer renders a GDL subset. Compile and open in Archicad for the final result.">
          Approximate preview · verify in Archicad
        </span>
        <span>
          {preview?.meshes.length ?? 0} meshes | {warnings.length} warnings | {sourceLabel}
        </span>
      </footer>
    </section>
  )
}

function previewSourceLabel(preview: PreviewPayload | null, hasDirtyScripts: boolean) {
  if (hasDirtyScripts && preview?.verification?.source !== 'editor_buffer') return 'Stale'
  return preview?.verification?.source === 'editor_buffer' ? 'Editor Buffer' : 'Saved'
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

// Uniform default + deterministic per-part colors in random mode.
// Golden-angle hue rotation keeps neighbouring parts well separated
// without flickering between renders.
const SOLID_COLOR = '#c0b49e'
const MONO_COLOR = '#b9c2cc'
const WIRE_COLOR = '#7dd3fc'
const XRAY_COLOR = '#6fd3ff'

function partColor(index: number): Color {
  return new Color().setHSL(((index * 137.508) % 360) / 360, 0.58, 0.6)
}

// X-ray ghost material: fragment-level fresnel so edge falloff survives
// interpolation on large faces. f = (base + (1-base)·fresnel^sharp) × gain.
const xrayMaterial = new ShaderMaterial({
  uniforms: {
    uColor: { value: new Color(XRAY_COLOR) },
    uGain: { value: 0.85 },
    uSharpness: { value: 1.6 },
    uBase: { value: 0.06 },
  },
  vertexShader: /* glsl */ `
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      vec4 worldPos = modelMatrix * vec4(position, 1.0);
      vNormal = normalize(mat3(modelMatrix) * normal);
      vViewDir = normalize(cameraPosition - worldPos.xyz);
      gl_Position = projectionMatrix * viewMatrix * worldPos;
    }
  `,
  fragmentShader: /* glsl */ `
    uniform vec3 uColor;
    uniform float uGain;
    uniform float uSharpness;
    uniform float uBase;
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      float fresnel = pow(1.0 - abs(dot(normalize(vNormal), normalize(vViewDir))), uSharpness);
      float alpha = clamp((uBase + (1.0 - uBase) * fresnel) * uGain, 0.0, 1.0);
      gl_FragColor = vec4(uColor, alpha);
    }
  `,
  transparent: true,
  depthWrite: false,
  side: DoubleSide,
})

function MeshView({
  mesh,
  index,
  showEdges,
  displayMode,
}: {
  mesh: PreviewMesh
  index: number
  showEdges: boolean
  displayMode: PreviewDisplayMode
}) {
  const geometry = useMemo(() => {
    const next = new BufferGeometry()
    next.setAttribute('position', new BufferAttribute(new Float32Array(mesh.vertices.flat()), 3))
    next.setIndex(new BufferAttribute(new Uint32Array(mesh.faces.flat()), 1))
    next.computeVertexNormals()
    return next
  }, [mesh.faces, mesh.vertices])

  if (displayMode === 'wire') {
    // Hidden-line: only feature/boundary edges. A full triangle
    // wireframe collapses to a white blob on dense meshes.
    return (
      <mesh geometry={geometry}>
        <meshBasicMaterial visible={false} />
        <Edges color={WIRE_COLOR} threshold={8} />
      </mesh>
    )
  }

  if (displayMode === 'xray') {
    return <mesh geometry={geometry} material={xrayMaterial} />
  }

  if (displayMode === 'mono') {
    return (
      <mesh geometry={geometry}>
        <meshBasicMaterial color={MONO_COLOR} />
        {showEdges ? <Edges color="#334155" threshold={18} /> : null}
      </mesh>
    )
  }

  if (displayMode === 'random') {
    return (
      <mesh geometry={geometry}>
        <meshStandardMaterial color={partColor(index)} roughness={0.62} metalness={0.04} side={DoubleSide} />
        {showEdges ? <Edges color="#334155" threshold={18} /> : null}
      </mesh>
    )
  }

  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={SOLID_COLOR} roughness={0.62} metalness={0.04} side={DoubleSide} />
      {showEdges ? <Edges color="#334155" threshold={18} /> : null}
    </mesh>
  )
}
