import type { Preview2DPayload } from '../api/types'

/**
 * 2D 视口几何数学（P3c）。
 *
 * 纯函数模块：viewBox 状态（View2D）+ fit/zoomAt/panBy/clamp 计算，
 * 与 React 组件解耦，可直接单测。坐标一律用 SVG viewBox 单位。
 */

export interface View2D {
  minX: number
  minY: number
  width: number
  height: number
}

export interface Bounds2D {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

/** fit 视图的外边距比例（与 P3c 前 computeBounds 的 0.08 一致）。 */
export const VIEW2D_DEFAULT_PADDING = 0.08
/** 缩放钳制：视图宽度/高度相对 fit 的最小与最大比例。 */
export const VIEW2D_MIN_SCALE = 0.02
export const VIEW2D_MAX_SCALE = 20

export function computeBounds2D(preview: Preview2DPayload | null): Bounds2D | null {
  const points: Array<[number, number]> = []
  for (const line of preview?.lines ?? []) {
    points.push(line.from, line.to)
  }
  for (const polygon of preview?.polygons ?? []) {
    points.push(...polygon)
  }
  for (const circle of preview?.circles ?? []) {
    points.push([circle.cx - circle.r, circle.cy - circle.r], [circle.cx + circle.r, circle.cy + circle.r])
  }
  for (const arc of preview?.arcs ?? []) {
    points.push([arc.cx - arc.r, arc.cy - arc.r], [arc.cx + arc.r, arc.cy + arc.r])
  }

  if (!points.length) {
    return null
  }

  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  return {
    minX: Math.min(...xs),
    maxX: Math.max(...xs),
    minY: Math.min(...ys),
    maxY: Math.max(...ys),
  }
}

/** 生成能完整容纳 bounds 的初始视图（含 padding，最小尺寸兜底 0.1）。 */
export function fitView2D(bounds: Bounds2D, padding: number = VIEW2D_DEFAULT_PADDING): View2D {
  const width = Math.max(bounds.maxX - bounds.minX, 0.1)
  const height = Math.max(bounds.maxY - bounds.minY, 0.1)
  const pad = Math.max(width, height) * padding
  return {
    minX: bounds.minX - pad,
    minY: bounds.minY - pad,
    width: width + pad * 2,
    height: height + pad * 2,
  }
}

export function toViewBoxString(view: View2D): string {
  return `${view.minX} ${view.minY} ${view.width} ${view.height}`
}

/**
 * 以 viewBox 坐标 (cx, cy) 为锚点缩放 factor 倍（factor > 1 放大）。
 * 锚点处的内容在缩放前后保持在同一屏幕位置。纯几何变换，不钳制。
 */
export function zoomAt(view: View2D, cx: number, cy: number, factor: number): View2D {
  if (!Number.isFinite(factor) || factor <= 0) {
    return view
  }
  return {
    minX: cx - (cx - view.minX) / factor,
    minY: cy - (cy - view.minY) / factor,
    width: view.width / factor,
    height: view.height / factor,
  }
}

/** 平移 dx/dy（viewBox 单位）。 */
export function panBy(view: View2D, dx: number, dy: number): View2D {
  return {
    ...view,
    minX: view.minX + dx,
    minY: view.minY + dy,
  }
}

/**
 * 把视图宽高钳制在 [fit*minScale, fit*maxScale] 区间，保持视图中心不动。
 * 用于限制滚轮缩放到离谱的倍数。
 */
export function clampView2D(
  view: View2D,
  fit: View2D,
  minScale: number = VIEW2D_MIN_SCALE,
  maxScale: number = VIEW2D_MAX_SCALE,
): View2D {
  const centerX = view.minX + view.width / 2
  const centerY = view.minY + view.height / 2
  const minWidth = fit.width * minScale
  const maxWidth = fit.width * maxScale
  const minHeight = fit.height * minScale
  const maxHeight = fit.height * maxScale
  const width = Math.min(Math.max(view.width, minWidth), maxWidth)
  const height = Math.min(Math.max(view.height, minHeight), maxHeight)
  return {
    minX: centerX - width / 2,
    minY: centerY - height / 2,
    width,
    height,
  }
}

/** 把屏幕像素偏移（在宽 wPx × 高 hPx 的 SVG 上）换算成 viewBox 偏移。 */
export function pixelsToViewBox(view: View2D, dxPx: number, dyPx: number, wPx: number, hPx: number): { dx: number; dy: number } {
  if (wPx <= 0 || hPx <= 0) {
    return { dx: 0, dy: 0 }
  }
  return {
    dx: (dxPx / wPx) * view.width,
    dy: (dyPx / hPx) * view.height,
  }
}
