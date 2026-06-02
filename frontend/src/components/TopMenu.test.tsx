import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { TopMenu } from './TopMenu'

function renderTopMenu(overrides: Partial<Parameters<typeof TopMenu>[0]> = {}) {
  const props: Parameters<typeof TopMenu>[0] = {
    project: { name: 'Shelf', source: 'hsf', path: '/workspace/Shelf' },
    projectControls: <button type="button">Project</button>,
    hasDraftChanges: true,
    onApply: vi.fn(),
    onCompile: vi.fn(),
    onMockCompile: vi.fn(),
    onSave: vi.fn(),
    onOpenSettings: vi.fn(),
    applying: false,
    loading: false,
    compiling: false,
    saving: false,
    hasDirtyScript: false,
    lastError: null,
    onClearError: vi.fn(),
    ...overrides,
  }

  render(<TopMenu {...props} />)
  return props
}

describe('TopMenu', () => {
  test('keeps primary workbench actions directly available in one toolbar', () => {
    const props = renderTopMenu()

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    fireEvent.click(screen.getByTestId('compile-button'))
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(props.onSave).toHaveBeenCalledTimes(1)
    expect(props.onCompile).toHaveBeenCalledTimes(1)
    expect(props.onOpenSettings).toHaveBeenCalledTimes(1)
    expect(props.onApply).toHaveBeenCalledTimes(1)
  })

  test('groups secondary build actions behind the Build menu', () => {
    const props = renderTopMenu()

    expect(screen.queryByTestId('mock-compile-button')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Build' }))
    fireEvent.click(screen.getByTestId('mock-compile-button'))

    expect(props.onMockCompile).toHaveBeenCalledTimes(1)
  })
})
