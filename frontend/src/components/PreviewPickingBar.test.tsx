import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import type { PreviewMesh } from '../api/types'
import { useUiPrefsStore } from '../state/uiPrefsStore'
import { PreviewPickingBar } from './PreviewPickingBar'
import { makeSelection } from './previewPicking'

function meshWithSource(): PreviewMesh {
  return {
    name: 'BLOCK',
    vertices: [],
    faces: [],
    source_ref: { script_type: '3d', line: 42, command: 'BLOCK', label: '' },
  }
}

describe('PreviewPickingBar', () => {
  test('shows the resolved source summary and an enabled jump button', () => {
    const onJump = vi.fn()
    render(<PreviewPickingBar selection={makeSelection(0, meshWithSource())} onJump={onJump} onDismiss={vi.fn()} />)

    expect(screen.getByText('3d.gdl:42 · BLOCK')).toBeTruthy()
    const jump = screen.getByRole('button', { name: '跳转到源码' }) as HTMLButtonElement
    expect(jump.disabled).toBe(false)
    fireEvent.click(jump)
    expect(onJump).toHaveBeenCalledTimes(1)
  })

  test('shows "no source trace" and disables jump for meshes without source_ref', () => {
    const selection = makeSelection(0, { name: 'RULED_0', vertices: [], faces: [] })
    render(<PreviewPickingBar selection={selection} onJump={vi.fn()} onDismiss={vi.fn()} />)

    expect(screen.getByText('无源码溯源')).toBeTruthy()
    const jump = screen.getByRole('button', { name: '跳转到源码' }) as HTMLButtonElement
    expect(jump.disabled).toBe(true)
  })

  test('dismiss button clears the selection', () => {
    const onDismiss = vi.fn()
    render(<PreviewPickingBar selection={makeSelection(0, meshWithSource())} onJump={vi.fn()} onDismiss={onDismiss} />)

    fireEvent.click(screen.getByLabelText('取消选中'))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  test('renders in english locale', () => {
    act(() => useUiPrefsStore.setState({ locale: 'en' }))
    try {
      render(<PreviewPickingBar selection={makeSelection(0, meshWithSource())} onJump={vi.fn()} onDismiss={vi.fn()} />)
      expect(screen.getByRole('button', { name: 'Jump to source' })).toBeTruthy()
    } finally {
      act(() => useUiPrefsStore.setState({ locale: 'zh' }))
    }
  })
})
