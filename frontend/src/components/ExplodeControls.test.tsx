import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { ExplodeControls } from './ExplodeControls'

describe('ExplodeControls (P2b)', () => {
  test('toggle shows when inactive; no slider until active', () => {
    render(<ExplodeControls factor={0} onChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: '爆炸' })).toBeTruthy()
    expect(screen.queryByRole('slider')).toBeNull()
  })

  test('clicking the toggle enables with a default spread (0.5)', () => {
    const onChange = vi.fn()
    render(<ExplodeControls factor={0} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: '爆炸' }))
    expect(onChange).toHaveBeenCalledWith(0.5)
  })

  test('active state shows the 0–1 slider with 0.01 steps', () => {
    render(<ExplodeControls factor={0.4} onChange={vi.fn()} />)

    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.value).toBe('0.4')
    expect(slider.min).toBe('0')
    expect(slider.max).toBe('1')
    expect(slider.step).toBe('0.01')
  })

  test('slider changes report the factor; dragging to 0 closes (reports 0)', () => {
    const onChange = vi.fn()
    render(<ExplodeControls factor={0.4} onChange={onChange} />)

    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.27' } })
    expect(onChange).toHaveBeenCalledWith(0.27)

    fireEvent.change(screen.getByRole('slider'), { target: { value: '0' } })
    expect(onChange).toHaveBeenCalledWith(0)
  })

  test('clicking the active toggle closes the explode (reports 0)', () => {
    const onChange = vi.fn()
    render(<ExplodeControls factor={0.4} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: '爆炸' }))
    expect(onChange).toHaveBeenCalledWith(0)
  })
})
