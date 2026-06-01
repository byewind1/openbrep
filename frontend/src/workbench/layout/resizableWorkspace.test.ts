import { describe, expect, test } from 'vitest'
import {
  clampWorkspaceColumns,
  DEFAULT_WORKSPACE_COLUMNS,
  parseStoredWorkspaceColumns,
  serializeWorkspaceColumns,
} from './resizableWorkspace'

describe('resizable workspace columns', () => {
  test('clamps columns while preserving center editor width', () => {
    const columns = clampWorkspaceColumns({ left: 900, right: 900 }, 1180)

    expect(columns.left).toBeLessThanOrEqual(420)
    expect(columns.right).toBeLessThanOrEqual(640)
    expect(1180 - 12 - columns.left - columns.right).toBeGreaterThanOrEqual(520)
  })

  test('uses wider right minimum in preview workspace mode', () => {
    const columns = clampWorkspaceColumns({ left: 240, right: 300 }, 1320, { previewWorkspaceOpen: true })

    expect(columns.right).toBeGreaterThanOrEqual(360)
    expect(1320 - 12 - columns.left - columns.right).toBeGreaterThanOrEqual(720)
  })

  test('parses stored columns defensively', () => {
    expect(parseStoredWorkspaceColumns('{"left":260,"right":500}')).toEqual({ left: 260, right: 500 })
    expect(parseStoredWorkspaceColumns('bad json')).toBeNull()
    expect(parseStoredWorkspaceColumns('{"left":"wide","right":500}')).toBeNull()
  })

  test('serializes integer columns', () => {
    expect(serializeWorkspaceColumns({ left: 260.4, right: 511.8 })).toBe('{"left":260,"right":512}')
    expect(DEFAULT_WORKSPACE_COLUMNS.left).toBe(240)
  })
})
