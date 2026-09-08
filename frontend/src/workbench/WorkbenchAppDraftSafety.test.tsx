import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { workbenchStore } from '../state/workbenchStore'
import { WorkbenchApp } from './WorkbenchApp'

// SF1/R1：WorkbenchApp 层级的接线回归——离开入口共用守卫、命名身份检查、
// Settings 导出 callback 接入 exportHsfProject。
// 重依赖组件 mock 掉，只保留 TopMenu/ProjectOpenControls 与对话框的真实行为。

vi.mock('../state/useWorkbenchStore', () => ({
  useWorkbenchStore: (selector: (state: unknown) => unknown) => selector(workbenchStore.getState()),
}))

vi.mock('../state/uiPrefsStore', () => ({
  useUiPrefsStore: (selector: (state: { locale: string }) => unknown) => selector({ locale: 'zh' }),
}))

vi.mock('./layout/ResizableWorkspaceGrid', () => ({
  ResizableWorkspaceGrid: () => <div data-testid="grid">grid</div>,
}))

vi.mock('./preview/FloatingPreviewWindow', () => ({
  FloatingPreviewWindow: () => null,
}))

vi.mock('./preview/PreviewWorkspaceStage', () => ({
  PreviewWorkspaceStage: () => <div data-testid="preview-stage">preview</div>,
}))

vi.mock('./layout/WorkbenchLeftRail', () => ({
  WorkbenchLeftRail: () => <div data-testid="left-rail">left</div>,
}))

vi.mock('./layout/WorkbenchRightRail', () => ({
  WorkbenchRightRail: () => <div data-testid="right-rail">right</div>,
}))

vi.mock('../components/BottomDrawer', () => ({
  BottomDrawer: () => <div data-testid="bottom-drawer">drawer</div>,
}))

vi.mock('./useConfigAutoRefresh', () => ({
  useConfigAutoRefresh: () => {},
}))

vi.mock('./settings/SettingsDrawer', () => ({
  SettingsDrawer: ({ onExportHsfProject }: { onExportHsfProject: () => void }) => (
    <div data-testid="settings-drawer">
      <button type="button" data-testid="export-hsf-button" onClick={onExportHsfProject}>
        Export HSF
      </button>
    </div>
  ),
}))

function resetStore(patch: Record<string, unknown> = {}) {
  workbenchStore.setState({
    sessionId: 's1',
    projectEpoch: 1,
    project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
    loading: false,
    assistantBusy: false,
    compiling: false,
    sourceActionBusy: false,
    dirtyScripts: {},
    scriptContents: {},
    draftParameters: {},
    needsSaveAs: false,
    compileLog: [],
    lastError: null,
    load: vi.fn(async () => {}),
    ...patch,
  })
}

beforeEach(() => {
  resetStore()
})

async function openProjectMenu() {
  fireEvent.click(screen.getByRole('button', { name: 'Project' }))
  await waitFor(() => expect(screen.getByLabelText('HSF project path')).toBeTruthy())
}

describe('WorkbenchApp SF1/R1 接线', () => {
  test('SettingsDrawer 导出 callback 调用 exportHsfProject（无参，由 action 统一收集草稿）', async () => {
    const exportSpy = vi.fn(async () => true)
    resetStore({ exportHsfProject: exportSpy } as never)

    render(<WorkbenchApp />)
    fireEvent.click(document.querySelector('.settings-trigger') as HTMLElement)
    await waitFor(() => expect(screen.queryByTestId('settings-drawer')).toBeTruthy())
    fireEvent.click(screen.getByTestId('export-hsf-button'))

    await waitFor(() => expect(exportSpy).toHaveBeenCalledTimes(1))
    expect(exportSpy).toHaveBeenCalledWith()
  })

  test('New 入口在有草稿时弹出“取消 / 丢弃并继续”确认，取消后 action 不执行', async () => {
    const newProjectSpy = vi.fn()
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'draft' }, newProject: newProjectSpy } as never)

    render(<WorkbenchApp />)
    await openProjectMenu()
    fireEvent.click(screen.getByRole('button', { name: 'New' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('如需保留')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(newProjectSpy).not.toHaveBeenCalled()
  })

  test('Browse 入口在有草稿时弹出离开确认，取消后 action 不执行', async () => {
    const browseSpy = vi.fn()
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'draft' }, browseProjectDirectory: browseSpy } as never)

    render(<WorkbenchApp />)
    await openProjectMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Browse for an HSF project directory' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('如需保留')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(browseSpy).not.toHaveBeenCalled()
  })

  test('Import GDL/GSM/Blender 入口在有草稿时均弹出离开确认', async () => {
    const importGdlSpy = vi.fn()
    const importGsmSpy = vi.fn()
    const importBlenderSpy = vi.fn()
    resetStore({
      dirtyScripts: { '3d.gdl': true },
      scriptContents: { '3d.gdl': 'draft' },
      importGdlFile: importGdlSpy,
      importGsmFile: importGsmSpy,
      importBlenderScript: importBlenderSpy,
    } as never)

    render(<WorkbenchApp />)
    for (const [label, spy] of [
      ['Import GDL', importGdlSpy],
      ['Import GSM', importGsmSpy],
      ['Import Blender', importBlenderSpy],
    ] as const) {
      await openProjectMenu()
      fireEvent.click(screen.getByRole('button', { name: label }))
      const dialog = await screen.findByRole('dialog')
      expect(dialog.textContent).toContain('如需保留')
      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      expect(spy).not.toHaveBeenCalled()
    }
  })

  test('Recent 项目选择失败时保留草稿', async () => {
    const loadSpy = vi.fn(async () => {
      workbenchStore.setState({ lastError: 'Failed to open HSF project' })
    })
    resetStore({
      dirtyScripts: { '3d.gdl': true },
      scriptContents: { '3d.gdl': 'draft' },
      loadProjectPath: loadSpy,
      recentProjects: [{ path: '/missing', name: 'Missing', parent_dir: '/', exists: false }],
    } as never)

    render(<WorkbenchApp />)
    await openProjectMenu()
    const recent = screen.getByLabelText('Recent HSF projects')
    fireEvent.change(recent, { target: { value: '/missing' } })

    const dialog = await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: '丢弃并继续' }))

    await waitFor(() => expect(loadSpy).toHaveBeenCalledWith('/missing'))
    expect(workbenchStore.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(workbenchStore.getState().scriptContents['3d.gdl']).toBe('draft')
  })
})
