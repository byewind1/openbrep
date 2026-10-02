import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { PreviewWorkspaceStage } from './PreviewWorkspaceStage'

// 三个舞台常驻 DOM（display 切换）：lazy 的 PreviewViewport/Preview2DViewport/ScriptEditor
// 即使隐藏也会挂载，直接 mock 掉避免引入 r3f/jsdom WebGL 依赖。
vi.mock('../../components/PreviewViewport', () => ({
  PreviewViewport: () => null,
}))

vi.mock('../../components/Preview2DViewport', () => ({
  Preview2DViewport: () => null,
}))

vi.mock('../../components/ScriptEditor', () => ({
  ScriptEditor: () => null,
}))

function baseProps(overrides: Partial<Parameters<typeof PreviewWorkspaceStage>[0]> = {}) {
  return {
    centerView: 'editor' as const,
    preview: null,
    preview2d: null,
    warnings: [],
    activeScriptName: null,
    activeScriptContent: '',
    hasDirtyScript: false,
    hasDirtyScripts: false,
    activeFocusLine: null,
    activeFocusEndLine: null,
    activeFocusKey: null,
    onCenterViewChange: vi.fn(),
    onFloatPreview: vi.fn(),
    onChangeScript: vi.fn(),
    onLoadPreview2D: vi.fn(),
    ...overrides,
  }
}

describe('PreviewWorkspaceStage editor empty state (P4-C)', () => {
  test('shows guidance when no script is loaded', () => {
    render(<PreviewWorkspaceStage {...baseProps()} />)

    expect(screen.getByText('未打开脚本')).toBeTruthy()
    expect(screen.getByText(/从工作区打开项目，或用 AI 生成/)).toBeTruthy()
  })

  test('does not show the empty state once a script is open', () => {
    render(
      <PreviewWorkspaceStage
        {...baseProps({ activeScriptName: '3d.gdl', activeScriptContent: 'BLOCK A, B, ZZYZX' })}
      />,
    )

    expect(screen.queryByText('未打开脚本')).toBeNull()
  })
})

describe('PreviewWorkspaceStage center view tabs', () => {
  test('renders the 脚本/3D/2D tab bar with the current view active', () => {
    render(<PreviewWorkspaceStage {...baseProps({ centerView: '3d' })} />)

    const tablist = screen.getByRole('tablist', { name: '中间区视图' })
    expect(tablist).toBeTruthy()
    expect(screen.getByRole('tab', { name: '脚本' }).getAttribute('aria-selected')).toBe('false')
    expect(screen.getByRole('tab', { name: '3D' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: '2D' }).getAttribute('aria-selected')).toBe('false')
  })

  test('clicking a tab switches the center view', () => {
    const onCenterViewChange = vi.fn()
    render(<PreviewWorkspaceStage {...baseProps({ onCenterViewChange })} />)

    fireEvent.click(screen.getByRole('tab', { name: '3D' }))

    expect(onCenterViewChange).toHaveBeenCalledWith('3d')
  })

  test('switching to 2D triggers a load when no 2D preview is cached', () => {
    const onCenterViewChange = vi.fn()
    const onLoadPreview2D = vi.fn()
    render(<PreviewWorkspaceStage {...baseProps({ onCenterViewChange, onLoadPreview2D })} />)

    fireEvent.click(screen.getByRole('tab', { name: '2D' }))

    expect(onLoadPreview2D).toHaveBeenCalledTimes(1)
    expect(onCenterViewChange).toHaveBeenCalledWith('2d')
  })

  test('switching to 2D does not reload when a 2D preview already exists', () => {
    const onLoadPreview2D = vi.fn()
    render(
      <PreviewWorkspaceStage
        {...baseProps({
          onLoadPreview2D,
          preview2d: { lines: [], polygons: [], circles: [], arcs: [], warnings: [] },
        })}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '2D' }))

    expect(onLoadPreview2D).not.toHaveBeenCalled()
  })

  test('only the active stage is visible; the other two stay mounted but hidden', () => {
    const { container } = render(<PreviewWorkspaceStage {...baseProps({ centerView: '2d' })} />)

    const preview3d = container.querySelector('.preview-workspace-stage') as HTMLElement
    const preview2d = container.querySelector('.preview-2d-stage') as HTMLElement
    const editor = container.querySelector('.editor-stage') as HTMLElement

    expect(preview3d.classList.contains('stage-hidden')).toBe(true)
    expect(preview2d.classList.contains('stage-hidden')).toBe(false)
    expect(editor.classList.contains('stage-hidden')).toBe(true)
  })
})
