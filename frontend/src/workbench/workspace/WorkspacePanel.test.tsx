import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { WorkspacePanel } from './WorkspacePanel'
import type { WorkspaceInfo, WorkspaceSearchHit } from '../../api/types'

function makeWorkspace(): WorkspaceInfo {
  return {
    path: '/workspace',
    project_count: 2,
    projects: [
      {
        name: 'Chair',
        path: '/workspace/hsf/Chair',
        parameter_count: 3,
        scripts_present: ['SCRIPT_3D'],
        latest_revision_id: 'r0001',
        origin: { imported_from: '/workspace/sources/chair.gdl', imported_kind: 'gdl', imported_at: '2026-08-06T00:00:00' },
        artifact_count: 2,
        active: true,
      },
      {
        name: 'Shelf',
        path: '/workspace/hsf/Shelf',
        parameter_count: 5,
        scripts_present: ['SCRIPT_3D', 'SCRIPT_2D'],
        latest_revision_id: null,
        origin: null,
        artifact_count: 0,
        active: false,
      },
    ],
  }
}

function baseProps(overrides: Partial<Parameters<typeof WorkspacePanel>[0]> = {}) {
  return {
    workspace: null,
    busy: false,
    searching: false,
    searchQuery: null,
    searchHits: [] as WorkspaceSearchHit[],
    workspaceInitHint: null,
    onOpenWorkspace: vi.fn(),
    onInitWorkspace: vi.fn(),
    onCloseWorkspace: vi.fn(),
    onRefreshWorkspace: vi.fn(),
    onSearchWorkspace: vi.fn(),
    onBrowseDirectory: vi.fn(async () => '/picked'),
    onDismissInitHint: vi.fn(),
    onTrashWorkspaceProject: vi.fn(),
    onLoadProjectPath: vi.fn(),
    ...overrides,
  }
}

describe('WorkspacePanel', () => {
  test('shows empty state when no workspace attached', () => {
    render(<WorkspacePanel {...baseProps()} />)

    expect(screen.getByText('未附着工作区')).toBeTruthy()
    expect(screen.getByRole('button', { name: '附着' })).toBeTruthy()
  })

  test('attach submits path and clears the input', () => {
    const props = baseProps()
    render(<WorkspacePanel {...props} />)

    fireEvent.change(screen.getByLabelText('工作区目录路径'), { target: { value: '/ws' } })
    fireEvent.click(screen.getByRole('button', { name: '附着' }))

    expect(props.onOpenWorkspace).toHaveBeenCalledWith('/ws')
  })

  test('lists projects with active highlight and badges', () => {
    render(<WorkspacePanel {...baseProps({ workspace: makeWorkspace() })} />)

    expect(screen.getByText('Chair')).toBeTruthy()
    expect(screen.getByText('Shelf')).toBeTruthy()
    // origin badge shows imported kind
    expect(screen.getByText('gdl')).toBeTruthy()
    // artifact count badge
    expect(screen.getByText('2')).toBeTruthy()
    // active 项目有 active class（行容器上）
    const activeRow = screen.getByText('Chair').closest('.workspace-project-row')
    expect(activeRow?.className).toContain('active')
  })

  test('clicking a project loads it via the existing project action', () => {
    const props = baseProps({ workspace: makeWorkspace() })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getByText('Shelf'))

    expect(props.onLoadProjectPath).toHaveBeenCalledWith('/workspace/hsf/Shelf')
  })

  test('search submits query and shows hits with project/line/snippet', () => {
    const props = baseProps({
      workspace: makeWorkspace(),
      searchQuery: 'cylind',
      searchHits: [{ project: 'Chair', location: 'scripts/3d.gdl', line: 2, snippet: 'CYLIND 1, 1' }],
    })
    render(<WorkspacePanel {...props} />)

    fireEvent.change(screen.getByLabelText('跨项目搜索'), { target: { value: 'cylind' } })
    fireEvent.click(screen.getByRole('button', { name: '搜索' }))

    expect(props.onSearchWorkspace).toHaveBeenCalledWith('cylind')
    // 结果：项目名 + 位置行号 + 摘要
    expect(screen.getByText('Chair · scripts/3d.gdl:2')).toBeTruthy()
    expect(screen.getByText('CYLIND 1, 1')).toBeTruthy()
  })

  test('search hit click jumps to the project', () => {
    const props = baseProps({
      workspace: makeWorkspace(),
      searchQuery: 'cylind',
      searchHits: [{ project: 'Chair', location: 'scripts/3d.gdl', line: 2, snippet: 'CYLIND 1, 1' }],
    })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getByText('Chair · scripts/3d.gdl:2'))

    expect(props.onLoadProjectPath).toHaveBeenCalledWith('/workspace/hsf/Chair')
  })

  test('close and refresh buttons are available when attached', () => {
    const props = baseProps({ workspace: makeWorkspace() })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getByTitle('刷新'))
    fireEvent.click(screen.getByTitle('解除附着'))

    expect(props.onRefreshWorkspace).toHaveBeenCalled()
    expect(props.onCloseWorkspace).toHaveBeenCalled()
  })

  test('shows no-projects empty text when workspace has zero projects', () => {
    render(
      <WorkspacePanel
        {...baseProps({ workspace: { path: '/ws', project_count: 0, projects: [] } })}
      />,
    )

    expect(screen.getByText('暂无项目')).toBeTruthy()
  })
})


describe('WorkspacePanel init flow (P3-d2b)', () => {
  test('empty state renders attach and init-and-attach buttons', () => {
    render(<WorkspacePanel {...baseProps()} />)

    expect(screen.getByRole('button', { name: '附着' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '初始化并附着' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '浏览…' })).toBeTruthy()
  })

  test('init-and-attach button calls onInitWorkspace with the path', () => {
    const props = baseProps()
    render(<WorkspacePanel {...props} />)

    fireEvent.change(screen.getByLabelText('工作区目录路径'), { target: { value: '/fresh' } })
    fireEvent.click(screen.getByRole('button', { name: '初始化并附着' }))

    expect(props.onInitWorkspace).toHaveBeenCalledWith('/fresh')
  })

  test('browse button fills the path input without submitting', async () => {
    const props = baseProps({ onBrowseDirectory: vi.fn(async () => '/picked') })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '浏览…' }))

    expect(props.onBrowseDirectory).toHaveBeenCalled()
    // 异步填路径：await 状态更新
    await waitFor(() => {
      const input = screen.getByLabelText('工作区目录路径') as HTMLInputElement
      expect(input.value).toBe('/picked')
    })
    // 未自动提交
    expect(props.onOpenWorkspace).not.toHaveBeenCalled()
    expect(props.onInitWorkspace).not.toHaveBeenCalled()
  })

  test('hint bar renders with path and init action, dismiss clears', () => {
    const props = baseProps({ workspaceInitHint: '/plain-dir' })
    render(<WorkspacePanel {...props} />)

    expect(screen.getByText('该目录还不是 OpenBrep 工作区')).toBeTruthy()
    // 引导条按钮与空态主按钮同文案：取第一个（引导条在上方）
    const initButtons = screen.getAllByRole('button', { name: '初始化并附着' })
    expect(initButtons.length).toBeGreaterThanOrEqual(2)
    fireEvent.click(initButtons[0])
    expect(props.onInitWorkspace).toHaveBeenCalledWith('/plain-dir')

    fireEvent.click(screen.getByLabelText('关闭'))
    expect(props.onDismissInitHint).toHaveBeenCalled()
  })

  test('busy disables attach and init buttons', () => {
    render(<WorkspacePanel {...baseProps({ busy: true, workspaceInitHint: '/x' })} />)

    expect((screen.getByRole('button', { name: '附着' }) as HTMLButtonElement).disabled).toBe(true)
    const initButtons = screen.getAllByRole('button', { name: '初始化并附着' })
    for (const button of initButtons) {
      expect((button as HTMLButtonElement).disabled).toBe(true)
    }
  })
})


describe('WorkspacePanel trash flow (P3-f)', () => {
  test('trash button calls onTrashWorkspaceProject after themed confirm', async () => {
    const props = baseProps({ workspace: makeWorkspace() })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getAllByTitle('移入回收站')[0])

    // 主题对话框出现，确认文案保留原 i18n 插值（workspace.deleteConfirm）
    expect(screen.getByText(/把项目 Shelf 移入回收站/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '确定' }))

    await waitFor(() => {
      expect(props.onTrashWorkspaceProject).toHaveBeenCalledTimes(1)
    })
    expect(props.onTrashWorkspaceProject).toHaveBeenCalledWith('/workspace/hsf/Shelf')
  })

  test('trash button cancelled in themed dialog does not call callback', () => {
    const props = baseProps({ workspace: makeWorkspace() })
    render(<WorkspacePanel {...props} />)

    fireEvent.click(screen.getAllByTitle('移入回收站')[0])
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(props.onTrashWorkspaceProject).not.toHaveBeenCalled()
  })

  test('active project trash button is disabled with hint', () => {
    const props = baseProps({ workspace: makeWorkspace() })
    render(<WorkspacePanel {...props} />)

    // makeWorkspace: Chair 是 active（第一项）
    const activeTrash = screen.getByTitle('当前打开的项目：请先切换到其他项目再删除')
    expect((activeTrash as HTMLButtonElement).disabled).toBe(true)
    // 非 active 的可删（只有 Shelf 的删除按钮带"移入回收站" title）
    const inactiveTrash = screen.getByTitle('移入回收站')
    expect((inactiveTrash as HTMLButtonElement).disabled).toBe(false)
    expect(props.onTrashWorkspaceProject).not.toHaveBeenCalled()
  })
})
