export interface WorkspaceColumns {
  left: number
  right: number
  /** P4-D 折叠态：true = 该栏渲染为窄条；缺省视为展开（向后兼容旧值） */
  leftCollapsed?: boolean
  rightCollapsed?: boolean
}

export interface DrawerState {
  height: number
  collapsed: boolean
}

export const WORKSPACE_COLUMNS_STORAGE_KEY = 'openbrep.workbench.columns.v1'
export const DRAWER_STATE_STORAGE_KEY = 'openbrep.workbench.drawer.v1'

export const DEFAULT_WORKSPACE_COLUMNS: WorkspaceColumns = {
  left: 240,
  right: 320,
}

export const DEFAULT_DRAWER_HEIGHT = 180
const DRAWER_HEIGHT_MIN = 120
const DRAWER_HEIGHT_MAX_RATIO = 0.7

const LEFT_MIN = 220
const LEFT_MAX = 420
const RIGHT_MIN = 300
const RIGHT_PREVIEW_MIN = 360
const RIGHT_MAX = 640
const CENTER_MIN = 520
const CENTER_PREVIEW_MIN = 720
const RESIZE_HANDLE_TOTAL = 20

export function clampWorkspaceColumns(
  columns: WorkspaceColumns,
  containerWidth: number,
  options: { previewWorkspaceOpen?: boolean } = {},
): WorkspaceColumns {
  const previewWorkspaceOpen = options.previewWorkspaceOpen ?? false
  const centerMin = previewWorkspaceOpen ? CENTER_PREVIEW_MIN : CENTER_MIN
  const rightMin = previewWorkspaceOpen ? RIGHT_PREVIEW_MIN : RIGHT_MIN
  const availableColumnsWidth = Math.max(containerWidth - RESIZE_HANDLE_TOTAL, 0)
  const safeContainer = Math.max(availableColumnsWidth, LEFT_MIN + rightMin + centerMin)

  let left = clamp(columns.left, LEFT_MIN, LEFT_MAX)
  let right = clamp(columns.right, rightMin, RIGHT_MAX)

  const maxLeftForCenter = safeContainer - right - centerMin
  left = clamp(left, LEFT_MIN, Math.max(LEFT_MIN, Math.min(LEFT_MAX, maxLeftForCenter)))

  const maxRightForCenter = safeContainer - left - centerMin
  right = clamp(right, rightMin, Math.max(rightMin, Math.min(RIGHT_MAX, maxRightForCenter)))

  return {
    left: Math.round(left),
    right: Math.round(right),
    leftCollapsed: columns.leftCollapsed ?? false,
    rightCollapsed: columns.rightCollapsed ?? false,
  }
}

export function parseStoredWorkspaceColumns(raw: string | null): WorkspaceColumns | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<WorkspaceColumns>
    if (!Number.isFinite(parsed.left) || !Number.isFinite(parsed.right)) return null
    return {
      left: Number(parsed.left),
      right: Number(parsed.right),
      // 旧值（无折叠字段）→ 视为展开
      leftCollapsed: parsed.leftCollapsed === true,
      rightCollapsed: parsed.rightCollapsed === true,
    }
  } catch {
    return null
  }
}

export function serializeWorkspaceColumns(columns: WorkspaceColumns): string {
  return JSON.stringify({
    left: Math.round(columns.left),
    right: Math.round(columns.right),
    leftCollapsed: columns.leftCollapsed === true,
    rightCollapsed: columns.rightCollapsed === true,
  })
}

/** 抽屉高度 clamp：120px ~ 视口 70% */
export function clampDrawerHeight(height: number, viewportHeight: number): number {
  const max = Math.max(DRAWER_HEIGHT_MIN, Math.round(viewportHeight * DRAWER_HEIGHT_MAX_RATIO))
  return clamp(Math.round(height), DRAWER_HEIGHT_MIN, max)
}

export function parseStoredDrawerState(raw: string | null): DrawerState | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<DrawerState>
    const height = Number(parsed.height)
    if (!Number.isFinite(height) || height <= 0) return null
    return { height: Math.round(height), collapsed: parsed.collapsed === true }
  } catch {
    return null
  }
}

export function serializeDrawerState(state: DrawerState): string {
  return JSON.stringify({ height: Math.round(state.height), collapsed: state.collapsed === true })
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}
