import { describe, expect, test } from 'vitest'
import {
  clampDrawerHeight,
  clampWorkspaceColumns,
  DEFAULT_DRAWER_HEIGHT,
  DEFAULT_WORKSPACE_COLUMNS,
  parseStoredDrawerState,
  parseStoredWorkspaceColumns,
  serializeDrawerState,
  serializeWorkspaceColumns,
} from './resizableWorkspace'

describe('resizable workspace columns', () => {
  test('clamps columns while preserving center editor width', () => {
    const columns = clampWorkspaceColumns({ left: 900, right: 900 }, 1180)

    expect(columns.left).toBeLessThanOrEqual(420)
    expect(columns.right).toBeLessThanOrEqual(640)
    expect(1180 - 20 - columns.left - columns.right).toBeGreaterThanOrEqual(520)
  })

  test('uses wider right minimum in preview workspace mode', () => {
    const columns = clampWorkspaceColumns({ left: 240, right: 300 }, 1320, { previewWorkspaceOpen: true })

    expect(columns.right).toBeGreaterThanOrEqual(360)
    expect(1320 - 20 - columns.left - columns.right).toBeGreaterThanOrEqual(720)
  })

  test('parses stored columns defensively', () => {
    expect(parseStoredWorkspaceColumns('{"left":260,"right":500}')).toEqual({
      left: 260,
      right: 500,
      leftCollapsed: false,
      rightCollapsed: false,
    })
    expect(parseStoredWorkspaceColumns('bad json')).toBeNull()
    expect(parseStoredWorkspaceColumns('{"left":"wide","right":500}')).toBeNull()
  })

  test('parses collapsed flags with fallback to open for legacy values', () => {
    // 旧 schema（无折叠字段）→ 展开
    const legacy = parseStoredWorkspaceColumns('{"left":260,"right":500}')
    expect(legacy?.leftCollapsed).toBe(false)
    expect(legacy?.rightCollapsed).toBe(false)
    // 新 schema
    expect(
      parseStoredWorkspaceColumns('{"left":260,"right":500,"leftCollapsed":true,"rightCollapsed":false}'),
    ).toEqual({ left: 260, right: 500, leftCollapsed: true, rightCollapsed: false })
  })

  test('clamp preserves collapsed flags', () => {
    const columns = clampWorkspaceColumns({ left: 900, right: 900, leftCollapsed: true }, 1180)
    expect(columns.leftCollapsed).toBe(true)
    expect(columns.rightCollapsed).toBe(false)
  })

  test('serializes integer columns with collapsed flags', () => {
    expect(serializeWorkspaceColumns({ left: 260.4, right: 511.8 })).toBe(
      '{"left":260,"right":512,"leftCollapsed":false,"rightCollapsed":false}',
    )
    expect(serializeWorkspaceColumns({ left: 260, right: 320, leftCollapsed: true })).toBe(
      '{"left":260,"right":320,"leftCollapsed":true,"rightCollapsed":false}',
    )
    expect(DEFAULT_WORKSPACE_COLUMNS.left).toBe(240)
  })
})

describe('drawer height helpers (P4-D)', () => {
  test('clamps drawer height to 120px..70% of viewport', () => {
    expect(clampDrawerHeight(60, 800)).toBe(120)
    expect(clampDrawerHeight(180, 800)).toBe(180)
    expect(clampDrawerHeight(900, 800)).toBe(560)
    // 先四舍五入再封顶：560.6 → 561 → 上限 560
    expect(clampDrawerHeight(560.6, 800)).toBe(560)
  })

  test('falls back to a small viewport floor when 70% is below the minimum', () => {
    expect(clampDrawerHeight(300, 100)).toBe(120)
  })

  test('parses stored drawer state defensively', () => {
    expect(parseStoredDrawerState('{"height":220,"collapsed":true}')).toEqual({ height: 220, collapsed: true })
    expect(parseStoredDrawerState('{"height":220}')).toEqual({ height: 220, collapsed: false })
    expect(parseStoredDrawerState('garbage')).toBeNull()
    expect(parseStoredDrawerState('{"height":"tall","collapsed":false}')).toBeNull()
    expect(parseStoredDrawerState('{"height":-5,"collapsed":false}')).toBeNull()
    expect(parseStoredDrawerState(null)).toBeNull()
  })

  test('serializes drawer state with integer height', () => {
    expect(serializeDrawerState({ height: 220.7, collapsed: true })).toBe('{"height":221,"collapsed":true}')
    expect(DEFAULT_DRAWER_HEIGHT).toBe(180)
  })
})
