import type { PreviewPayload } from '../api/types'
import { resolveSourceRef } from './previewPicking'

/**
 * Blender 式随机分色 + 部件列表视图模型（P1d）。
 * 纯逻辑：FNV-1a hash → HSL 取色、部件视图模型、可见性过滤。
 * 不依赖 DOM/three，可直接单测。
 */

/** FNV-1a 32-bit：确定性字符串 hash（无外部依赖） */
export function hashString(input: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

/** 原始色相（未避让）：hash → [0, 360) */
export function rawHueFromHash(hash: number): number {
  return hash % 360
}

/**
 * 选中琥珀（#ffc94d，h≈42°）避让：落在 h ∈ [35,55] 时偏移 +180°，
 * 保证随机分色永远与 P1a 的选中高亮可区分。
 */
export function avoidSelectionAmber(hue: number): number {
  return hue >= 35 && hue <= 55 ? (hue + 180) % 360 : hue
}

/** 部件标识 → 稳定色相（同标识同色；同一项目多次刷新不跳色） */
export function hashHue(identity: string): number {
  return avoidSelectionAmber(rawHueFromHash(hashString(identity)))
}

/** 饱和度 [40, 65]%（柔和不刺眼，对标 Blender workbench random） */
export function hashSaturation(identity: string): number {
  return 40 + (hashString(`${identity}:s`) % 26)
}

/** 亮度 [50, 70]% */
export function hashLightness(identity: string): number {
  return 50 + (hashString(`${identity}:l`) % 21)
}

/** 部件标识 → CSS hsl 颜色（three.js 材质与面板 chip 通用） */
export function hashColor(identity: string): string {
  return `hsl(${hashHue(identity)}, ${hashSaturation(identity)}%, ${hashLightness(identity)}%)`
}

/**
 * 着色单元标识：random 模式连通域分件后按 compId 取色。
 * 同一 mesh 的不同 comp、不同 mesh 的同名 comp 都不会共用 identity。
 */
export function componentColorIdentity(meshName: string, meshIndex: number, compId: number): string {
  return `${meshName}#${meshIndex}:${compId}`
}

export type PartColorMode = 'random' | 'flat'

export interface PartViewModel {
  /** 对应 PreviewPayload.meshes 下标（与 P1a 拾取 selection 同粒度） */
  meshIndex: number
  meshName: string
  /** 该部件当前显示色（random = hash 色；flat = 模式主色） */
  color: string
  /** 源码行定位（"3d.gdl:42"）；无 source_ref 时为 null */
  sourceLine: string | null
  visible: boolean
}

/**
 * 部件列表视图模型：每行 = 一个 mesh。
 * random 模式的 chip 色取该 mesh 连通域 comp 0 的 hash 色
 * （非合并 mesh 的 comp 0 即整体色，与渲染一致）。
 */
export function buildPartsView(
  preview: PreviewPayload | null,
  mode: PartColorMode,
  flatColor: string | null,
  hidden: ReadonlySet<number>,
): PartViewModel[] {
  if (!preview) return []
  return preview.meshes.map((mesh, meshIndex) => {
    const source = resolveSourceRef(mesh.source_ref)
    return {
      meshIndex,
      meshName: mesh.name,
      color: mode === 'random' ? hashColor(componentColorIdentity(mesh.name, meshIndex, 0)) : (flatColor ?? '#8595ab'),
      sourceLine: source ? `${source.scriptName}:${source.line}` : null,
      visible: !hidden.has(meshIndex),
    }
  })
}

/**
 * 可见性过滤后的预览输入：隐藏部件不参与 fit bounds。
 * 不改 previewCamera.ts 的 computePreviewBounds 签名，由调用方包一层 useMemo。
 */
export function filterVisibleMeshes(
  preview: PreviewPayload | null,
  hidden: ReadonlySet<number>,
): PreviewPayload | null {
  if (!preview) return null
  return { ...preview, meshes: preview.meshes.filter((_, index) => !hidden.has(index)) }
}
