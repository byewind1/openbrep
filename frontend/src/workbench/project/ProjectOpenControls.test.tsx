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
        onLoadProjectPath={onLoadProjectPath}
        onBrowseProjectDirectory={vi.fn()}
        onImportGdlFile={vi.fn()}
        onImportGsmFile={vi.fn()}
      />,
    )

    const recent = screen.getByLabelText('Recent HSF projects')
    fireEvent.change(recent, { target: { value: '/workspace/Chair' } })

    expect(onLoadProjectPath).toHaveBeenCalledWith('/workspace/Chair')
    expect(within(recent).getByText('Display Chair')).toBeTruthy()
    expect(within(recent).getByText('Missing')).toBeTruthy()
  })
})
