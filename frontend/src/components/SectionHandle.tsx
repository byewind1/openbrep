import { TransformControls } from '@react-three/drei'
import { useMemo, useRef } from 'react'
import type { Group, Object3D } from 'three'
import { Color, DoubleSide, PlaneGeometry, ShaderMaterial } from 'three'
import type { PreviewBounds } from './previewCamera'
import { axisIndex, localCoordForT, tForLocalCoord, type SectionAxis, type SectionState } from './previewSection'

// 手柄材质：半透明琥珀平面，depthWrite=false，logdepth chunk 与对数深度缓冲
// 共存（照抄 xray 材质的 logdepthbuf 模式）。
const handleMaterial = new ShaderMaterial({
  uniforms: {
    uColor: { value: new Color('#f59e0b') },
    uAlpha: { value: 0.22 },
  },
  vertexShader: /* glsl */ `
    #include <common>
    #include <logdepthbuf_pars_vertex>
    void main() {
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      #include <logdepthbuf_vertex>
    }
  `,
  fragmentShader: /* glsl */ `
    #include <common>
    #include <logdepthbuf_pars_fragment>
    uniform vec3 uColor;
    uniform float uAlpha;
    void main() {
      #include <logdepthbuf_fragment>
      gl_FragColor = vec4(uColor, uAlpha);
    }
  `,
  transparent: true,
  depthWrite: false,
  side: DoubleSide,
})

const handlePlaneGeometry = new PlaneGeometry(1, 1)

// 平面朝向（局部旋转）：默认 PlaneGeometry 法线 +Z，转到对应轴的法线方向
const PLANE_ROTATION: Record<SectionAxis, [number, number, number]> = {
  x: [0, Math.PI / 2, 0], // 法线 +X
  y: [-Math.PI / 2, 0, 0], // 法线 +Y
  z: [0, 0, 0], // 法线 +Z
}

interface SectionHandleProps {
  section: SectionState
  bounds: PreviewBounds
  /** 拖拽/滑杆联动：传新的 t（[0,1]） */
  onTChange: (t: number) => void
  /** TransformControls 交互中标记（gizmo 点击不应触发空白清选中） */
  gizmoActiveRef: React.MutableRefObject<boolean>
}

/**
 * 剖切手柄（P1c）：半透明平面显示剖切位置与朝向 + TransformControls 单轴
 * 平移。渲染在场景根（TransformControls 的 gizmo 按附着对象的**世界坐标**
 * 定位，必须处于恒等变换节点下）；gizmo 拖拽时 drei 自动禁用 makeDefault
 * OrbitControls（dragging-changed 事件），无需手工锁。
 */
export function SectionHandle({ section, bounds, onTChange, gizmoActiveRef }: SectionHandleProps) {
  const handleRef = useRef<Group | null>(null)
  const { axis, t } = section

  // 手柄世界坐标：bounds.center + 局部偏移（模型在 group 内居中渲染）
  const position = useMemo(() => {
    const local = localCoordForT(bounds, axis, t)
    const p: [number, number, number] = [bounds.center[0], bounds.center[1], bounds.center[2]]
    p[axisIndex(axis)] += local
    return p
  }, [axis, t, bounds])

  const planeSize = useMemo(() => {
    // 平面覆盖模型横截面：去掉轴向后的两维取大者
    const ai = axisIndex(axis)
    const other = [0, 1, 2].filter((i) => i !== ai).map((i) => bounds.size[i])
    return Math.max(other[0] ?? 1, other[1] ?? 1, 1) * 1.25
  }, [axis, bounds])

  function syncTFromHandle() {
    const handle = handleRef.current
    if (!handle) return
    const localCoord = handle.position.getComponent(axisIndex(axis)) - bounds.center[axisIndex(axis)]
    onTChange(tForLocalCoord(bounds, axis, localCoord))
  }

  return (
    <>
      <group ref={handleRef} position={position}>
        {/* 平面本身：r3f onClick stopPropagation → 点击不触发 Canvas
            onPointerMissed 清选中；也挡住"穿过剖切面选到后面 mesh" */}
        <mesh
          geometry={handlePlaneGeometry}
          material={handleMaterial}
          rotation={PLANE_ROTATION[axis]}
          scale={[planeSize, planeSize, 1]}
          onClick={(event) => event.stopPropagation()}
        />
      </group>
      <TransformControls
        object={handleRef as unknown as React.RefObject<Object3D>}
        mode="translate"
        axis={axis.toUpperCase()}
        showX={axis === 'x'}
        showY={axis === 'y'}
        showZ={axis === 'z'}
        size={0.75}
        onChange={syncTFromHandle}
        onMouseDown={() => {
          gizmoActiveRef.current = true
        }}
        onMouseUp={() => {
          // r3f 的 click（onPointerMissed）在 pointerup 之后、定时器之前派发：
          // setTimeout(0) 让标记跨过本次 click，之后才复位
          setTimeout(() => {
            gizmoActiveRef.current = false
          }, 0)
        }}
      />
    </>
  )
}
