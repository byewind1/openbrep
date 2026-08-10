import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { QualityToggle, ShadowsToggle } from './ViewportVisualControls'

describe('ShadowsToggle (P1b)', () => {
  test('renders with active state and reports toggles', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<ShadowsToggle enabled onToggle={onToggle} />)

    const button = screen.getByRole('button', { name: '阴影' })
    expect(button.className).toContain('active')
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalledTimes(1)

    rerender(<ShadowsToggle enabled={false} onToggle={onToggle} />)
    expect(screen.getByRole('button', { name: '阴影' }).className).not.toContain('active')
  })
})

describe('QualityToggle (P1b)', () => {
  test('renders fast and accurate buttons with the active tier', () => {
    const onChange = vi.fn()
    render(<QualityToggle quality="fast" onChange={onChange} />)

    const fast = screen.getByRole('button', { name: '快速' })
    const accurate = screen.getByRole('button', { name: '精细' })
    expect(fast.className).toContain('active')
    expect(accurate.className).not.toContain('active')

    fireEvent.click(accurate)
    expect(onChange).toHaveBeenCalledWith('accurate')

    fireEvent.click(fast)
    expect(onChange).toHaveBeenCalledWith('fast')
  })

  test('marks accurate active when quality is accurate', () => {
    render(<QualityToggle quality="accurate" onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: '精细' }).className).toContain('active')
    expect(screen.getByRole('button', { name: '快速' }).className).not.toContain('active')
  })
})
