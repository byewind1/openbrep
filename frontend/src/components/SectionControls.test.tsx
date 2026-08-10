import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { SectionControls } from './SectionControls'

function baseProps(overrides: Partial<Parameters<typeof SectionControls>[0]> = {}) {
  return {
    active: false,
    axis: 'z' as const,
    t: 0.5,
    onToggle: vi.fn(),
    onAxisChange: vi.fn(),
    onTChange: vi.fn(),
    ...overrides,
  }
}

describe('SectionControls (P1c)', () => {
  test('shows the toggle button; inactive state has no axis/slider', () => {
    render(<SectionControls {...baseProps()} />)

    expect(screen.getByRole('button', { name: '剖切' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'X' })).toBeNull()
    expect(screen.queryByRole('slider')).toBeNull()
  })

  test('toggle click calls onToggle and active state shows axis buttons + slider', () => {
    const props = baseProps({ active: true })
    render(<SectionControls {...props} />)

    expect(screen.getByRole('button', { name: 'X' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Y' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Z' })).toBeTruthy()
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.value).toBe('0.5')
    expect(slider.min).toBe('0')
    expect(slider.max).toBe('1')
  })

  test('axis buttons report changes and highlight the active axis', () => {
    const onAxisChange = vi.fn()
    render(<SectionControls {...baseProps({ active: true, axis: 'y', onAxisChange })} />)

    const y = screen.getByRole('button', { name: 'Y' })
    expect(y.className).toContain('active')
    fireEvent.click(screen.getByRole('button', { name: 'X' }))
    expect(onAxisChange).toHaveBeenCalledWith('x')
  })

  test('slider reports position changes', () => {
    const onTChange = vi.fn()
    render(<SectionControls {...baseProps({ active: true, onTChange })} />)

    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.73' } })
    expect(onTChange).toHaveBeenCalledWith(0.73)
  })
})
