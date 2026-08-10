import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { PreviewGhostToggle } from './PreviewGhostToggle'

describe('PreviewGhostToggle (P2a)', () => {
  test('disabled when no ghost snapshot is available, with explanatory title', () => {
    render(<PreviewGhostToggle available={false} active={false} onToggle={vi.fn()} />)

    const button = screen.getByRole('button', { name: '对比' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.title).toContain('无任务前版本')
  })

  test('enabled when a ghost is available and reports clicks', () => {
    const onToggle = vi.fn()
    render(<PreviewGhostToggle available active={false} onToggle={onToggle} />)

    const button = screen.getByRole('button', { name: '对比' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  test('active state adds the active class', () => {
    render(<PreviewGhostToggle available active onToggle={vi.fn()} />)
    expect(screen.getByRole('button', { name: '对比' }).className).toContain('active')
  })
})
