import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { ProjectOpenControls } from './ProjectOpenControls'

describe('ProjectOpenControls', () => {
  test('opens an existing recent project from the top project controls', () => {
    const onLoadProjectPath = vi.fn()

    render(
      <ProjectOpenControls
        project={{ name: 'Demo Bookshelf', source: 'demo' }}
        loading={false}
        recentProjects={[
          { path: '/workspace/Chair', name: 'Display Chair', parent_dir: '/workspace', exists: true },
          { path: '/workspace/Missing', exists: false },
        ]}
        onNewProject={vi.fn()}
        onLoadProjectPath={onLoadProjectPath}
        onBrowseProjectDirectory={vi.fn()}
        onImportGdlFile={vi.fn()}
        onImportGsmFile={vi.fn()}
        onSaveProjectAs={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Project' }))
    const recent = screen.getByLabelText('Recent HSF projects')
    fireEvent.change(recent, { target: { value: '/workspace/Chair' } })

    expect(onLoadProjectPath).toHaveBeenCalledWith('/workspace/Chair')
    expect(within(recent).getByText('Display Chair')).toBeTruthy()
    expect(within(recent).getByText('Missing')).toBeTruthy()
  })

  test('exposes new and save-as project actions', () => {
    const onNewProject = vi.fn()
    const onSaveProjectAs = vi.fn()

    render(
      <ProjectOpenControls
        project={{ name: 'Untitled GDL Object', source: 'untitled' }}
        loading={false}
        recentProjects={[]}
        onNewProject={onNewProject}
        onLoadProjectPath={vi.fn()}
        onBrowseProjectDirectory={vi.fn()}
        onImportGdlFile={vi.fn()}
        onImportGsmFile={vi.fn()}
        onSaveProjectAs={onSaveProjectAs}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Project' }))
    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    fireEvent.click(screen.getByRole('button', { name: 'Project' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save As' }))

    expect(onNewProject).toHaveBeenCalledTimes(1)
    expect(onSaveProjectAs).toHaveBeenCalledTimes(1)
  })

  test('keeps path open controls inside the project menu instead of always visible', () => {
    render(
      <ProjectOpenControls
        project={{ name: 'Shelf', source: 'hsf', path: '/workspace/Shelf' }}
        loading={false}
        recentProjects={[]}
        onNewProject={vi.fn()}
        onLoadProjectPath={vi.fn()}
        onBrowseProjectDirectory={vi.fn()}
        onImportGdlFile={vi.fn()}
        onImportGsmFile={vi.fn()}
        onSaveProjectAs={vi.fn()}
      />,
    )

    expect(screen.queryByLabelText('HSF project path')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Project' }))

    expect(screen.getByLabelText('HSF project path')).toBeTruthy()
  })
})
