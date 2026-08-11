import { render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { PreviewWorkspaceStage } from './PreviewWorkspaceStage'

// 两个舞台常驻 DOM（display 切换）：lazy 的 PreviewViewport/ScriptEditor
// 即使隐藏也会挂载，直接 mock 掉避免引入 r3f/jsdom WebGL 依赖。
vi.mock('../../components/PreviewViewport', () => ({
  PreviewViewport: () => null,
}))

vi.mock('../../components/ScriptEditor', () => ({
  ScriptEditor: () => null,
}))

function baseProps(overrides: Partial<Parameters<typeof PreviewWorkspaceStage>[0]> = {}) {
  return {
    previewWorkspaceOpen: false,
    preview: null,
    warnings: [],
    activeScriptName: null,
    activeScriptContent: '',
    hasDirtyScript: false,
    hasDirtyScripts: false,
    activeFocusLine: null,
    activeFocusEndLine: null,
    activeFocusKey: null,
    onCollapsePreview: vi.fn(),
    onFloatPreview: vi.fn(),
    onChangeScript: vi.fn(),
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
