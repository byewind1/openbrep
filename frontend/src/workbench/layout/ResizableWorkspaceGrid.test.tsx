import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import { ResizableWorkspaceGrid } from './ResizableWorkspaceGrid'
import { WORKSPACE_COLUMNS_STORAGE_KEY } from './resizableWorkspace'

afterEach(() => {
  window.localStorage.clear()
})

function renderGrid(seed: string | null) {
  if (seed !== null) window.localStorage.setItem(WORKSPACE_COLUMNS_STORAGE_KEY, seed)
  return render(
    <ResizableWorkspaceGrid
      previewWorkspaceOpen={false}
      loading={false}
      left={<div>LEFT-PANEL</div>}
      main={<div>MAIN-PANEL</div>}
      right={<div>RIGHT-PANEL</div>}
    />,
  )
}

function leftWidth(container: HTMLElement) {
  const section = container.querySelector('.workspace-grid') as HTMLElement
  return section.style.getPropertyValue('--workspace-left-width')
}

function rightWidth(container: HTMLElement) {
  const section = container.querySelector('.workspace-grid') as HTMLElement
  return section.style.getPropertyValue('--workspace-right-width')
}

describe('ResizableWorkspaceGrid column collapse (P4-D)', () => {
  test('renders a narrow bar and hides content when a column is collapsed', () => {
    const { container } = renderGrid('{"left":260,"right":500,"leftCollapsed":true,"rightCollapsed":false}')

    expect(leftWidth(container)).toBe('28px')
    expect(rightWidth(container)).toBe('500px')

    // 窄条展开按钮出现（i18n 文案）
    expect(screen.getByRole('button', { name: '展开左栏' })).toBeTruthy()
    // 栏内容保持挂载（折叠只隐藏，不卸载 → 面板内部状态不丢）
    expect(screen.getByText('LEFT-PANEL')).toBeTruthy()
    // 折叠栏的拖柄禁用；右栏拖柄不受影响
    const handles = container.querySelectorAll('.workspace-resize-handle')
    expect((handles[0] as HTMLButtonElement).disabled).toBe(true)
    expect((handles[1] as HTMLButtonElement).disabled).toBe(false)
  })

  test('expand restores the remembered width', () => {
    const { container } = renderGrid('{"left":260,"right":500,"leftCollapsed":true}')

    fireEvent.click(screen.getByRole('button', { name: '展开左栏' }))

    expect(leftWidth(container)).toBe('260px')
    expect(screen.queryByRole('button', { name: '展开左栏' })).toBeNull()
    // 折叠按钮回到拖柄上（收起左栏）
    expect(screen.getByRole('button', { name: '收起左栏' })).toBeTruthy()
  })

  test('collapsing persists the state to localStorage', () => {
    const { container } = renderGrid('{"left":260,"right":500}')

    fireEvent.click(screen.getByRole('button', { name: '收起右栏' }))

    const stored = JSON.parse(window.localStorage.getItem(WORKSPACE_COLUMNS_STORAGE_KEY) ?? '{}')
    expect(stored.rightCollapsed).toBe(true)
    expect(stored.leftCollapsed).toBe(false)
    expect(rightWidth(container)).toBe('28px')
  })

  test('both columns can be collapsed at once', () => {
    const { container } = renderGrid('{"left":260,"right":500,"leftCollapsed":true}')

    fireEvent.click(screen.getByRole('button', { name: '收起右栏' }))

    expect(leftWidth(container)).toBe('28px')
    expect(rightWidth(container)).toBe('28px')
    expect(screen.getByRole('button', { name: '展开左栏' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '展开右栏' })).toBeTruthy()
  })

  test('falls back to expanded defaults on a corrupt stored value', () => {
    const { container } = renderGrid('not-json')

    expect(leftWidth(container)).toBe('240px')
    expect(rightWidth(container)).toBe('320px')
    expect(screen.queryByRole('button', { name: '展开左栏' })).toBeNull()
  })
})
