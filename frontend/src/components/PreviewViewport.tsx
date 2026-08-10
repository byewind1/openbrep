import { ContactShadows, Edges, OrbitControls, OrthographicCamera, PerspectiveCamera } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Camera, OrthographicCamera as OrthographicCameraType, PerspectiveCamera as PerspectiveCameraType } from 'three'
import { BufferAttribute, BufferGeometry, Color, DoubleSide, Plane, PMREMGenerator, ShaderMaterial, Vector3 } from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { PreviewMesh, PreviewPayload, PreviewQuality } from '../api/types'
import type { PreviewGhostLabel } from '../state/workbenchStoreTypes'
import { useT } from '../i18n'
import { ExplodeControls } from './ExplodeControls'
import { PreviewGhostOverlay } from './PreviewGhostOverlay'
import { PreviewGhostToggle } from './PreviewGhostToggle'
import { PreviewPickingBar } from './PreviewPickingBar'
import { PreviewPartsPanel } from './PreviewPartsPanel'
import { SectionControls } from './SectionControls'
import { SectionHandle } from './SectionHandle'
import { QualityToggle, ShadowsToggle } from './ViewportVisualControls'
import { buildMeshGeometry, isGhostAvailable } from './previewGhost'
import { buildExplodedParts, explodeOffset, overallCentroid as computeOverallCentroid, shouldSplitParts } from './previewExplode'
import type { ExplodedPart } from './previewExplode'
import { makeSelection } from './previewPicking'
import type { PreviewSelection } from './previewPicking'
import { buildPartsView, componentColorIdentity, filterVisibleMeshes, hashColor } from './previewParts'
import { sectionPlaneParams } from './previewSection'
import type { SectionState } from './previewSection'
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
  /** 选中 mesh 后跳转到 GDL 脚本对应段（scriptName 如 "3d.gdl"；endLine 为
   *  相关代码段末行，单行定位时为 null/缺省，见 P1e） */
  onRevealSource?: (scriptName: string, lineNumber: number, endLine?: number | null) => void
  /** 预览质量档（P1b）：与 onQualityChange 同时提供才显示切换按钮 */
  quality?: PreviewQuality
  onQualityChange?: (quality: PreviewQuality) => void
  /** P2a 修改前后对比：任务前预览快照（store 只读消费，不走倒灌）。
   *  null = 无可对比版本 → 对比按钮 disabled */
  previewGhost?: PreviewPayload | null
  /** ghost 快照原因（i18n key），视口角落标签用 */
  previewGhostLabel?: PreviewGhostLabel | null
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
  onRevealSource,
  quality,
  onQualityChange,
  previewGhost,
  previewGhostLabel,
}: PreviewViewportProps) {
  const t = useT()
  const [cameraMode, setCameraMode] = useState<PreviewCameraMode>('perspective')
  const [viewPreset, setViewPreset] = useState<PreviewViewPreset>('iso')
  const [fitNonce, setFitNonce] = useState(0)
  const [showEdges, setShowEdges] = useState(true)
  const [showGrid, setShowGrid] = useState(true)
  const [displayMode, setDisplayMode] = useState<PreviewDisplayMode>('solid')
  const [selection, setSelection] = useState<PreviewSelection | null>(null)
  // 部件隐藏是纯视图态：不进 store、不持久化，预览刷新后重置
  const [hiddenParts, setHiddenParts] = useState<ReadonlySet<number>>(new Set())
  const [partsOpen, setPartsOpen] = useState(false)
  // 接地阴影（P1b）：null = 跟随模式默认（solid/mono/random 开，wire/xray 关），
  // 用户手动切换后覆盖；纯视图态，不持久化
  const [showShadows, setShowShadows] = useState<boolean | null>(null)
  const shadowsEnabled = showShadows ?? !(displayMode === 'wire' || displayMode === 'xray')
  // 剖切面（P1c）：null = 关闭；{axis, t} 为当前剖切配置。纯视图态不持久化
  const [section, setSection] = useState<SectionState | null>(null)
  // TransformControls gizmo 交互标记：gizmo 点击不算"空白点击"，不清选中
  const gizmoActiveRef = useRef(false)
  // 对比叠加（P2a）：纯视图态每实例独立；ghost 消失（换项目）时强制关闭
  const [showGhost, setShowGhost] = useState(false)
  // 爆炸图（P2b）：0 = 关闭；滑杆 0–1 控制散开程度。纯视图态每实例独立
  const [explodeFactor, setExplodeFactor] = useState(0)
  // fit 只看可见部件：隐藏的 mesh 不进 bounds（computePreviewBounds 签名不变）
  const bounds = useMemo(() => computePreviewBounds(filterVisibleMeshes(preview, hiddenParts)), [preview, hiddenParts])
  // clippingPlanes 数组：内容只在 section/bounds 变化时改变（材质不逐帧重编译）
  const sectionPlanes = useMemo(() => {
    if (!section) return []
    const { normal, constant } = sectionPlaneParams(bounds, section)
    return [new Plane(new Vector3(...normal), constant)]
  }, [section, bounds])
  // 爆炸整体质心（P2b）：可见 mesh 顶点均值，centered 坐标；fit bounds 不含
  // 爆炸位移（保持现状语义，用户自己缩放）
  const overallCentroid = useMemo(
    () => computeOverallCentroid(filterVisibleMeshes(preview, hiddenParts)?.meshes ?? [], bounds.center),
    [preview, hiddenParts, bounds],
  )
  const parts = useMemo(() => {
    // wire 与 random 同为逐部件 hash 取色（chip 色与渲染一致）
    if (displayMode === 'random' || displayMode === 'wire') return buildPartsView(preview, 'random', null, hiddenParts)
    return buildPartsView(preview, 'flat', MODE_COLOR[displayMode], hiddenParts)
  }, [preview, displayMode, hiddenParts])
  const sourceLabel = previewSourceLabel(preview, hasDirtyScripts)

  // 预览数据更新（Update / 重新编译）后，旧下标可能指向别的 mesh：清空选中；
  // 隐藏状态一并重置；Esc 取消选中（单击空白由 Canvas onPointerMissed 处理）
  useEffect(() => {
    setSelection(null)
    setHiddenParts(new Set())
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setSelection(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [preview])

  // xray ShaderMaterial 按视口实例一份（共享会被多视口的剖切状态互相覆盖）；
  // 卸载时 dispose（浮动窗会反复开关）
  const xrayMaterial = useMemo(() => createXrayMaterial(), [])
  useEffect(() => () => xrayMaterial.dispose(), [xrayMaterial])
  // xray ShaderMaterial 的 clippingPlanes：平面数组是 uniform，
  // 内容变化无需 needsUpdate（material.clipping 恒为 true）
  useEffect(() => {
    xrayMaterial.clippingPlanes = sectionPlanes
  }, [xrayMaterial, sectionPlanes])

  // P2a：ghost 不可用（换项目清空快照）时，本实例的对比开关回到关闭态
  useEffect(() => {
    if (!isGhostAvailable(previewGhost)) setShowGhost(false)
  }, [previewGhost])

  function revealSource(source: PreviewSelection['source']) {
    if (!source) return
    onRevealSource?.(source.scriptName, source.line, source.segment?.end ?? null)
  }

  function fitView() {
    setFitNonce((value) => value + 1)
  }

  function resetView() {
    setCameraMode('perspective')
    setViewPreset('iso')
    setShowEdges(true)
    setShowGrid(true)
    setDisplayMode('solid')
    setHiddenParts(new Set())
    setShowShadows(null)
    setSection(null)
    setExplodeFactor(0)
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
          <button
            type="button"
            className={`viewport-action-button${partsOpen ? ' active' : ''}`}
            onClick={() => setPartsOpen((value) => !value)}
            title={t('preview.parts.toggle')}
          >
            {t('preview.parts.title')}
          </button>
          <ShadowsToggle enabled={shadowsEnabled} onToggle={() => setShowShadows(!shadowsEnabled)} />
          {quality && onQualityChange ? <QualityToggle quality={quality} onChange={onQualityChange} /> : null}
          <SectionControls
            active={section !== null}
            axis={section?.axis ?? 'z'}
            t={section?.t ?? 0.5}
            onToggle={() => setSection((current) => (current ? null : { axis: 'z', t: 0.5 }))}
            onAxisChange={(axis) => setSection((current) => (current ? { ...current, axis } : current))}
            onTChange={(t) => setSection((current) => (current ? { ...current, t } : current))}
          />
          <PreviewGhostToggle
            available={isGhostAvailable(previewGhost)}
            active={showGhost}
            onToggle={() => setShowGhost((value) => !value)}
          />
          <ExplodeControls factor={explodeFactor} onChange={setExplodeFactor} />
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
        <Canvas
          gl={{ logarithmicDepthBuffer: true }}
          style={{ position: 'absolute', inset: 0 }}
          onCreated={({ gl }) => {
            // P1c：材质 clippingPlanes（局部剖切）依赖渲染器开关
            gl.localClippingEnabled = true
          }}
          onPointerMissed={() => {
            // TransformControls gizmo 的点击不算空白：不清选中
            if (gizmoActiveRef.current) return
            setSelection(null)
          }}
        >
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
          {/* 接地软阴影（P1b）：落在 bounds 底面；frames 默认每帧重捕，
              隐藏舞台（display:none）恢复后自愈。wire/xray 默认关（消隐线框下
              阴影是噪声），用户可手动开。 */}
          {shadowsEnabled && (preview?.meshes.length ?? 0) > 0 ? (
            <ContactShadows
              position={[bounds.center[0], bounds.center[1] - bounds.size[1] / 2, bounds.center[2]]}
              scale={Math.max(bounds.size[0], bounds.size[2]) * 1.6}
              far={Math.max(bounds.size[1] * 1.5, 4)}
              opacity={0.55}
              blur={2}
              resolution={256}
              color="#000000"
            />
          ) : null}
          {/* 大坐标模型（毫米级脚本）居中渲染，避免 float32 抖动与深度量化闪烁 */}
          <group position={bounds.center}>
            {showGrid ? <gridHelper args={[4, 8, '#334155', '#182235']} rotation={[Math.PI / 2, 0, 0]} /> : null}
            <axesHelper args={[1.4]} />
            {preview?.meshes.map((mesh, index) =>
              hiddenParts.has(index) ? null : (
                <MeshView
                  key={`${mesh.name}-${index}`}
                  mesh={mesh}
                  index={index}
                  showEdges={showEdges}
                  displayMode={displayMode}
                  offset={bounds.center}
                  selected={selection?.meshIndex === index}
                  clippingPlanes={sectionPlanes}
                  xrayMaterial={xrayMaterial}
                  explodeFactor={explodeFactor}
                  overallCentroid={overallCentroid}
                  onSelect={() => setSelection(makeSelection(index, mesh))}
                  onJump={() => {
                    const next = makeSelection(index, mesh)
                    setSelection(next)
                    revealSource(next.source)
                  }}
                />
              ),
            )}
          </group>
          {section ? (
            <SectionHandle
              section={section}
              bounds={bounds}
              onTChange={(t) => setSection((current) => (current ? { ...current, t } : current))}
              gizmoActiveRef={gizmoActiveRef}
            />
          ) : null}
          {/* P2a 对比叠加：任务前版本半透明 ghost；offset 用当前 bounds.center
              （同世界坐标系，减同一中心即对齐） */}
          {showGhost && previewGhost ? <PreviewGhostOverlay ghost={previewGhost} boundsCenter={bounds.center} /> : null}
        </Canvas>
        {selection ? (
          <PreviewPickingBar
            selection={selection}
            onJump={() => revealSource(selection.source)}
            onDismiss={() => setSelection(null)}
          />
        ) : null}
        {partsOpen && parts.length > 0 ? (
          <PreviewPartsPanel
            parts={parts}
            selectedIndex={selection?.meshIndex ?? null}
            onSelect={(meshIndex) => {
              const mesh = preview?.meshes[meshIndex]
              if (mesh) setSelection(makeSelection(meshIndex, mesh))
            }}
            onJump={(meshIndex) => {
              const mesh = preview?.meshes[meshIndex]
              if (!mesh) return
              const next = makeSelection(meshIndex, mesh)
              setSelection(next)
              revealSource(next.source)
            }}
            onToggleHidden={(meshIndex) =>
              setHiddenParts((current) => {
                const next = new Set(current)
                if (next.has(meshIndex)) next.delete(meshIndex)
                else next.add(meshIndex)
                return next
              })
            }
          />
        ) : null}
        {showGhost && previewGhost && previewGhostLabel ? (
          <div className="viewport-ghost-tag">
            {t('preview.ghost.cornerTag', { label: t(previewGhostLabel) })}
          </div>
        ) : null}
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
const XRAY_COLOR = '#6fd3ff'
// 画布底色（= styles/tokens.css --bg-canvas）：线框模式的消隐遮挡填充色，必须与 token 一致
const CANVAS_BG_COLOR = '#05070d'
// 选中强调色（琥珀，与 --accent 同族）：线框/Edges 叠加在五种显示模式下均可辨认
const SELECTION_COLOR = '#ffc94d'

// 统一单色模式的显示色（部件面板 chip 与渲染共用；random/wire 为逐部件 hash 色，不在此列）
const MODE_COLOR: Record<Exclude<PreviewDisplayMode, 'random' | 'wire'>, string> = {
  solid: SOLID_COLOR,
  mono: MONO_COLOR,
  xray: XRAY_COLOR,
}

// X-ray ghost material: fragment-level fresnel so edge falloff survives
// interpolation on large faces. f = (base + (1-base)·fresnel^sharp) × gain.
// logdepthbuf chunks keep it consistent with logarithmicDepthBuffer.
// 注意：必须是每个视口实例一份（createXrayMaterial 工厂），不能模块级共享——
// clippingPlanes 是按实例状态（剖切）写入材质的，共享会让多个同时挂载的
// 视口（右栏 / expand 舞台 / 浮动窗）互相覆盖剖切状态。
function createXrayMaterial(): ShaderMaterial {
  return new ShaderMaterial({
  uniforms: {
    uColor: { value: new Color(XRAY_COLOR) },
    uGain: { value: 0.85 },
    uSharpness: { value: 1.6 },
    uBase: { value: 0.06 },
  },
  // P1c 剖切：clipping=true 让 three 注入 clippingPlanes uniform，shader 内
  // 手动挂 clipping chunks（与 logdepthbuf 同模式）；平面是 uniform，
  // 内容变化无需 needsUpdate 重编译。
  clipping: true,
  vertexShader: /* glsl */ `
    #include <common>
    #include <clipping_planes_pars_vertex>
    #include <logdepthbuf_pars_vertex>
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      vec4 worldPos = modelMatrix * vec4(position, 1.0);
      vNormal = normalize(mat3(modelMatrix) * normal);
      vViewDir = normalize(cameraPosition - worldPos.xyz);
      // clipping_planes_vertex chunk 引用 mvPosition（视图空间坐标），
      // 必须显式定义，否则开启剖切（NUM_CLIPPING_PLANES>0）时编译失败、
      // mesh 整体不可见
      vec4 mvPosition = viewMatrix * worldPos;
      gl_Position = projectionMatrix * mvPosition;
      #include <logdepthbuf_vertex>
      #include <clipping_planes_vertex>
    }
  `,
  fragmentShader: /* glsl */ `
    #include <common>
    #include <clipping_planes_pars_fragment>
    #include <logdepthbuf_pars_fragment>
    uniform vec3 uColor;
    uniform float uGain;
    uniform float uSharpness;
    uniform float uBase;
    varying vec3 vNormal;
    varying vec3 vViewDir;
    void main() {
      #include <clipping_planes_fragment>
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
}

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
  selected,
  clippingPlanes,
  xrayMaterial,
  explodeFactor,
  overallCentroid,
  onSelect,
  onJump,
}: {
  mesh: PreviewMesh
  index: number
  showEdges: boolean
  displayMode: PreviewDisplayMode
  offset: [number, number, number]
  selected: boolean
  /** P1c 剖切面：挂到该 mesh 的所有渲染材质（含 Edges/xray）上 */
  clippingPlanes: Plane[]
  /** 视口实例级 xray 材质（不能模块级共享，剖切状态会互相覆盖） */
  xrayMaterial: ShaderMaterial
  /** P2b 爆炸：>0 时按连通域拆件并沿质心方向散开；0 = 关闭（渲染路径不变） */
  explodeFactor: number
  /** P2b 整体质心（centered 坐标，可见 mesh 顶点均值） */
  overallCentroid: [number, number, number]
  onSelect: () => void
  onJump: () => void
}) {
  // 几何构建共用（P2a）：与 PreviewGhostOverlay 同源，见 previewGhost.buildMeshGeometry
  const geometry = useMemo(() => buildMeshGeometry(mesh, offset), [mesh, offset])

  // 拆件（P2b）：random 取色与爆炸共用同一次 computeFaceComponents
  // （见 previewExplode.buildExplodedParts）；factor=0 且非 random 时不拆，
  // 走整 mesh 渲染路径，与 P2b 之前像素级一致
  const parts = useMemo(() => {
    if (!shouldSplitParts(explodeFactor, displayMode)) return []
    return buildExplodedParts(mesh, offset, geometry)
  }, [explodeFactor, displayMode, mesh, offset, geometry])

  const exploded = explodeFactor > 0

  // 拆件路径：random 恒拆（逐件取色）；爆炸开启时五档全拆，每件外套 group
  // 位移（位移走 group，不改几何数据）。选中高亮该 mesh 的全部 part。
  if (exploded || displayMode === 'random') {
    return (
      <>
        {parts.map((part) => (
          <group
            key={part.compId}
            position={exploded ? explodeOffset(part.centroid, overallCentroid, explodeFactor) : undefined}
          >
            <PartMesh
              part={part}
              mesh={mesh}
              index={index}
              showEdges={showEdges}
              displayMode={displayMode}
              selected={selected}
              clippingPlanes={clippingPlanes}
              xrayMaterial={xrayMaterial}
              colorCompId={part.compId}
              onSelect={onSelect}
              onJump={onJump}
            />
          </group>
        ))}
      </>
    )
  }

  // 整 mesh 路径：与 P2b 之前完全一致（PartMesh 单件渲染，参数逐模式保留）
  return (
    <PartMesh
      part={{ compId: 0, geometry, centroid: [0, 0, 0] }}
      mesh={mesh}
      index={index}
      showEdges={showEdges}
      displayMode={displayMode}
      selected={selected}
      clippingPlanes={clippingPlanes}
      xrayMaterial={xrayMaterial}
      colorCompId={0}
      onSelect={onSelect}
      onJump={onJump}
    />
  )
}

/**
 * 单个 mesh/part 的渲染（P2b）：五档显示模式的材质/Edges/选中高亮逐模式保留
 * 原语义。random 模式按 colorCompId 取色；其余模式整 mesh 一色
 * （wire 恒用 comp 0 identity，保持"wire 每 mesh 一色"）。
 */
function PartMesh({
  part,
  mesh,
  index,
  showEdges,
  displayMode,
  selected,
  clippingPlanes,
  xrayMaterial,
  colorCompId,
  onSelect,
  onJump,
}: {
  part: ExplodedPart
  mesh: PreviewMesh
  index: number
  showEdges: boolean
  displayMode: PreviewDisplayMode
  selected: boolean
  clippingPlanes: Plane[]
  xrayMaterial: ShaderMaterial
  colorCompId: number
  onSelect: () => void
  onJump: () => void
}) {
  // r3f 事件自带 delta（pointerdown→click 位移）；拖拽旋转结束时也会派发
  // click，超过 2px 一律不算点击，避免转视角误选中
  function handleClick(event: ThreeEvent<MouseEvent>) {
    if (event.delta > 2) return
    event.stopPropagation()
    onSelect()
  }

  function handleDoubleClick(event: ThreeEvent<MouseEvent>) {
    event.stopPropagation()
    onJump()
  }

  if (displayMode === 'wire') {
    // 消隐线框：三角面用画布底色填充（遮挡背面边线），Edges 只画特征/边界边——
    // 全三角线框在密 mesh 上会糊成一团。填充色必须等于 tokens.css --bg-canvas，
    // 否则遮挡体会在背景上显形；polygonOffset 把填充微微推后，让表面边线压过填充。
    // 每 mesh 按 identity hash 取色（与 random 模式同一套色，一眼认出同一构件）。
    const wireColor = hashColor(componentColorIdentity(mesh.name, index, 0))
    return (
      <mesh geometry={part.geometry} onClick={handleClick} onDoubleClick={handleDoubleClick}>
        <meshBasicMaterial
          color={CANVAS_BG_COLOR}
          side={DoubleSide}
          polygonOffset
          polygonOffsetFactor={1}
          polygonOffsetUnits={1}
          clippingPlanes={clippingPlanes}
        />
        <Edges color={selected ? SELECTION_COLOR : wireColor} threshold={8} clippingPlanes={clippingPlanes} />
      </mesh>
    )
  }

  if (displayMode === 'xray') {
    // ShaderMaterial 不吃 emissive：选中靠 Edges 琥珀叠加（本模式无 showEdges 开关）
    return (
      <mesh geometry={part.geometry} material={xrayMaterial} onClick={handleClick} onDoubleClick={handleDoubleClick}>
        {selected ? <Edges color={SELECTION_COLOR} threshold={8} clippingPlanes={clippingPlanes} /> : null}
      </mesh>
    )
  }

  const isMono = displayMode === 'mono'
  const color =
    displayMode === 'random'
      ? hashColor(componentColorIdentity(mesh.name, index, colorCompId))
      : isMono
        ? MONO_COLOR
        : SOLID_COLOR
  // mono 的材质参数与原分支一致（roughness/metalness/envMapIntensity 不同）
  const shading = isMono
    ? { roughness: 0.7, metalness: 0.0, envMapIntensity: 0.6 }
    : { roughness: 0.5, metalness: 0.05, envMapIntensity: 0.75 }
  return (
    <mesh geometry={part.geometry} onClick={handleClick} onDoubleClick={handleDoubleClick}>
      <meshStandardMaterial
        color={color}
        roughness={shading.roughness}
        metalness={shading.metalness}
        envMapIntensity={shading.envMapIntensity}
        side={DoubleSide}
        emissive={selected ? SELECTION_COLOR : '#000000'}
        emissiveIntensity={0.4}
        clippingPlanes={clippingPlanes}
      />
      {showEdges || selected ? <Edges color={selected ? SELECTION_COLOR : EDGE_COLOR} threshold={18} clippingPlanes={clippingPlanes} /> : null}
    </mesh>
  )
}
