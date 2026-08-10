import type { PreviewBounds } from './previewCamera'

/**
 * 剖切面纯逻辑（P1c）：轴向映射、t↔坐标换算、剖切平面参数。
 * 坐标系约定：模型在 <group position={bounds.center}> 内渲染、顶点已减
 * bounds.center 居中，故"模型局部坐标"沿轴向跨度为 -size/2 .. +size/2；
 * 剖切平面是 world 空间（three.js 材质 clippingPlanes 的语义）。
 */

export type SectionAxis = 'x' | 'y' | 'z'

export interface SectionState {
  axis: SectionAxis
  /** 归一化位置 [0,1]：0 = 沿轴向最小值，1 = 最大值 */
  t: number
}

export function axisIndex(axis: SectionAxis): number {
  return axis === 'x' ? 0 : axis === 'y' ? 1 : 2
}

/** 轴向单位向量 */
export function axisVector(axis: SectionAxis): [number, number, number] {
  return axis === 'x' ? [1, 0, 0] : axis === 'y' ? [0, 1, 0] : [0, 0, 1]
}

/** 归一化 t 边界 clamp（NaN 兜底 0；±Infinity 分别夹到 0/1） */
export function clampT(t: number): number {
  if (Number.isNaN(t)) return 0
  return Math.min(1, Math.max(0, t))
}

/** 归一化 t → 模型局部坐标（-size/2 + t*size，clamp 后） */
export function localCoordForT(bounds: PreviewBounds, axis: SectionAxis, t: number): number {
  const half = bounds.size[axisIndex(axis)] / 2
  return -half + clampT(t) * bounds.size[axisIndex(axis)]
}

/** 模型局部坐标 → 归一化 t（clamp [0,1]；size=0 时兜底 0） */
export function tForLocalCoord(bounds: PreviewBounds, axis: SectionAxis, coord: number): number {
  const size = bounds.size[axisIndex(axis)]
  if (size <= 0) return 0
  return clampT((coord + size / 2) / size)
}

export interface SectionPlaneParams {
  /** world 空间法线（指向被剖掉的一侧） */
  normal: [number, number, number]
  /** three.Plane(normal, constant)：dot(normal, p) + constant > 0 的半空间被裁掉 */
  constant: number
}

/**
 * 剖切平面参数（world 空间，含 bounds.center 偏移）。
 * 约定：t=0 无裁切（平面在最小值边缘），t 增大从"负侧"向"正侧"推掉
 * 模型——保留坐标 ≥ 平面位置的一侧（刀推过即吃掉）。
 */
export function sectionPlaneParams(bounds: PreviewBounds, section: SectionState): SectionPlaneParams {
  const ai = axisIndex(section.axis)
  const localCoord = localCoordForT(bounds, section.axis, section.t)
  const worldCoord = localCoord + bounds.center[ai]
  const normal = axisVector(section.axis)
  return {
    // 裁掉 world[ai] < worldCoord 的一侧 → normal 指向 -axis，constant = +worldCoord；
    // `-v || 0` 把 -0 归零，避免 toEqual 的 -0/+0 区分
    normal: normal.map((v) => -v || 0) as [number, number, number],
    constant: worldCoord,
  }
}
