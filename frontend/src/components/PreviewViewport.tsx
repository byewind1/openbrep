import { Edges, OrbitControls, OrthographicCamera, PerspectiveCamera } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Camera, OrthographicCamera as OrthographicCameraType, PerspectiveCamera as PerspectiveCameraType } from 'three'
import { BufferAttribute, BufferGeometry, Color, DoubleSide, PMREMGenerator, ShaderMaterial, Vector3 } from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { PreviewMesh, PreviewPayload } from '../api/types'
import {
  computePreviewBounds,
  isFittableViewport,
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
        {/* absolute + inset:0：见 styles.css .canvas-wrap 注释，
            防止 canvas 的内联 px 宽度反向撑住容器导致无法收缩 */}
        <Canvas gl={{ logarithmicDepthBuffer: true }} style={{ position: 'absolute', inset: 0 }}>
          {cameraMode === 'perspective' ? (
            <PerspectiveCamera makeDefault fov={38} near={0.001} far={100000} />
          ) : (
            <OrthographicCamera makeDefault near={0.001} far={100000} />
          )}
          <PreviewCameraRig bounds={bounds} mode={cameraMode} preset={viewPreset} fitNonce={fitNonce} />
          <color attach="background" args={['#0a0e14']} />
          <StudioEnvironment />
          <ambientLight intensity={0.25} />
          <directionalLight position={[3, -4, 5]} intensity={1.1} />
          <directionalLight position={[-4, 2, 3]} intensity={0.5} color="#7cc7f5" />
          {/* 大坐标模型（毫米级脚本）居中渲染，避免 float32 抖动与深度量化闪烁 */}
          <group position={bounds.center}>
            {showGrid ? <gridHelper args={[4, 8, '#334155', '#182235']} rotation={[Math.PI / 2, 0, 0]} /> : null}
            <axesHelper args={[1.4]} />
            {preview?.meshes.map((mesh, index) => (
              <MeshView key={`${mesh.name}-${index}`} mesh={mesh} index={index} showEdges={showEdges} displayMode={displayMode} offset={bounds.center} />
            ))}
          </group>
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
    // 舞台被 display:none 隐藏时 r3f 上报 0×0，此时 fit 会写坏相机
    if (!isFittableViewport(size.width, size.height)) return
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

  // Tight depth range around the model: with logarithmicDepthBuffer this
  // mostly guards the ortho path; the far plane must still cover the model.
  projectionCamera.near = Math.max(distance / 1000, 0.001)
  projectionCamera.far = Math.max(distance * 20, 100)
  projectionCamera.updateProjectionMatrix()
}

// Uniform default + categorical per-part palette in random mode.
// Colors chosen for contrast on the dark viewport background.
const SOLID_COLOR = '#8595ab'
const MONO_COLOR = '#a89e92'
const EDGE_COLOR = '#1c2530'
const WIRE_COLOR = '#86d4f8'
const XRAY_COLOR = '#6fd3ff'

// 12 色分类调色板（高区分度，按部件序号确定性取用，不闪烁）
const PART_PALETTE = [
  '#e8a33d', '#5fb4e8', '#7ed491', '#e87d7d',
  '#b39de8', '#e8d05f', '#5fe8d0', '#e88fc0',
  '#a5c66f', '#7d9be8', '#e8975f', '#63c7e8',
]

/** 面级连通域编号（并查集）：共享顶点的面归一部件，按出现顺序编 0..K-1 */
function computeFaceComponents(faces: number[][], vertCount: number): number[] {
  const parent = new Int32Array(vertCount)
  for (let i = 0; i < vertCount; i++) parent[i] = i
  const find = (x: number): number => {
    let r = x
    while (parent[r] !== r) r = parent[r]
    while (parent[x] !== r) {
      const next = parent[x]
      parent[x] = r
      x = next
    }
    return r
  }
  const unite = (a: number, b: number) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent[rb] = ra
  }
  for (const tri of faces) {
    for (let k = 1; k < tri.length; k++) unite(tri[0], tri[k])
  }
  const remap = new Map<number, number>()
  return faces.map((tri) => {
    const root = find(tri[0])
    if (!remap.has(root)) remap.set(root, remap.size)
    return remap.get(root) as number
  })
}

// X-ray ghost material: fragment-level fresnel so edge falloff survives
// interpolation on large faces. f = (base + (1-base)·fresnel^sharp) × gain.
// logdepthbuf chunks keep it consistent with logarithmicDepthBuffer.
const xrayMaterial = new ShaderMaterial({
  uniforms: {
    uColor: { value: new Color(XRAY_COLOR) },
    uGain: { value: 0.85 },
    uSharpness: { value: 1.6 },
    uBase: { value: 0.06 },
  },
  vertexShader: /* glsl */ `
    #include <common>
    #include <logdepthbuf_pars_vertex>
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      vec4 worldPos = modelMatrix * vec4(position, 1.0);
      vNormal = normalize(mat3(modelMatrix) * normal);
      vViewDir = normalize(cameraPosition - worldPos.xyz);
      gl_Position = projectionMatrix * viewMatrix * worldPos;
      #include <logdepthbuf_vertex>
    }
  `,
  fragmentShader: /* glsl */ `
    #include <common>
    #include <logdepthbuf_pars_fragment>
    uniform vec3 uColor;
    uniform float uGain;
    uniform float uSharpness;
    uniform float uBase;
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      #include <logdepthbuf_fragment>
      float fresnel = pow(1.0 - abs(dot(normalize(vNormal), normalize(vViewDir))), uSharpness);
      float alpha = clamp((uBase + (1.0 - uBase) * fresnel) * uGain, 0.0, 1.0);
      gl_FragColor = vec4(uColor, alpha);
    }
  `,
  transparent: true,
  depthWrite: false,
  side: DoubleSide,
})

function StudioEnvironment() {
  // model-viewer 同款做法：RoomEnvironment 经 PMREM 生成 IBL，
  // 无需 HDR 资源文件即可获得工作室级反射光照
  const { gl, scene } = useThree()
  useEffect(() => {
    const pmrem = new PMREMGenerator(gl)
    const envMap = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environment = envMap
    return () => {
      scene.environment = null
      envMap.dispose()
      pmrem.dispose()
    }
  }, [gl, scene])
  return null
}

function MeshView({
  mesh,
  index,
  showEdges,
  displayMode,
  offset,
}: {
  mesh: PreviewMesh
  index: number
  showEdges: boolean
  displayMode: PreviewDisplayMode
  offset: [number, number, number]
}) {
  const geometry = useMemo(() => {
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
  }, [mesh.faces, mesh.vertices, offset])

  // 随机分色：BS2G 产物常是单一合并 mesh（放样链/拓扑体），按 mesh 分色
  // 会退化成一色；改为按面连通域拆成子 mesh，各自独立的调色板颜色材质
  const partGeometries = useMemo(() => {
    if (displayMode !== 'random') return []
    const comp = computeFaceComponents(mesh.faces, mesh.vertices.length)
    const byComp = new Map<number, number[]>()
    mesh.faces.forEach((tri, fi) => {
      const arr = byComp.get(comp[fi]) ?? []
      arr.push(...tri)
      byComp.set(comp[fi], arr)
    })
    return [...byComp.entries()].map(([compId, indices]) => {
      const g = new BufferGeometry()
      g.setAttribute('position', geometry.getAttribute('position'))
      g.setIndex(new BufferAttribute(new Uint32Array(indices), 1))
      g.computeVertexNormals()
      return { compId, geometry: g }
    })
  }, [displayMode, mesh.faces, mesh.vertices.length, geometry])

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
        <meshStandardMaterial color={MONO_COLOR} roughness={0.7} metalness={0.0} envMapIntensity={0.6} side={DoubleSide} />
        {showEdges ? <Edges color={EDGE_COLOR} threshold={18} /> : null}
      </mesh>
    )
  }

  if (displayMode === 'random') {
    return (
      <>
        {partGeometries.map((part) => (
          <mesh key={part.compId} geometry={part.geometry}>
            <meshStandardMaterial
              color={PART_PALETTE[part.compId % PART_PALETTE.length]}
              roughness={0.5}
              metalness={0.05}
              envMapIntensity={0.75}
              side={DoubleSide}
            />
            {showEdges ? <Edges color={EDGE_COLOR} threshold={18} /> : null}
          </mesh>
        ))}
      </>
    )
  }

  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={SOLID_COLOR} roughness={0.5} metalness={0.05} envMapIntensity={0.75} side={DoubleSide} />
      {showEdges ? <Edges color={EDGE_COLOR} threshold={18} /> : null}
    </mesh>
  )
}
