export interface WorkspaceColumns {
  left: number
  right: number
}

export const WORKSPACE_COLUMNS_STORAGE_KEY = 'openbrep.workbench.columns.v1'

export const DEFAULT_WORKSPACE_COLUMNS: WorkspaceColumns = {
  left: 240,
  right: 320,
}

const LEFT_MIN = 220
const LEFT_MAX = 420
const RIGHT_MIN = 300
const RIGHT_PREVIEW_MIN = 360
const RIGHT_MAX = 640
const CENTER_MIN = 520
const CENTER_PREVIEW_MIN = 720
const RESIZE_HANDLE_TOTAL = 12

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

  return { left: Math.round(left), right: Math.round(right) }
}

export function parseStoredWorkspaceColumns(raw: string | null): WorkspaceColumns | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<WorkspaceColumns>
    if (!Number.isFinite(parsed.left) || !Number.isFinite(parsed.right)) return null
    return { left: Number(parsed.left), right: Number(parsed.right) }
  } catch {
    return null
  }
}

export function serializeWorkspaceColumns(columns: WorkspaceColumns): string {
  return JSON.stringify({ left: Math.round(columns.left), right: Math.round(columns.right) })
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}
