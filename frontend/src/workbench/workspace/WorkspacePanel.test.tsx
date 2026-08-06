import { fireEvent, render, screen } from '@testing-library/react'
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
    onOpenWorkspace: vi.fn(),
    onCloseWorkspace: vi.fn(),
    onRefreshWorkspace: vi.fn(),
    onSearchWorkspace: vi.fn(),
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
    // active 项目有 active class
    const activeItem = screen.getByText('Chair').closest('button')
    expect(activeItem?.className).toContain('active')
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
