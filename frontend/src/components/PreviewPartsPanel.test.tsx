import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import type { PartViewModel } from './previewParts'
import { PreviewPartsPanel } from './PreviewPartsPanel'

function makeParts(): PartViewModel[] {
  return [
    { meshIndex: 0, meshName: 'BLOCK', color: 'hsl(200, 50%, 60%)', sourceLine: '3d.gdl:42', visible: true },
    { meshIndex: 1, meshName: 'RULED_0', color: 'hsl(120, 55%, 55%)', sourceLine: null, visible: false },
  ]
}

function baseProps(overrides: Partial<Parameters<typeof PreviewPartsPanel>[0]> = {}) {
  return {
    parts: makeParts(),
    selectedIndex: null,
    onSelect: vi.fn(),
    onJump: vi.fn(),
    onToggleHidden: vi.fn(),
    ...overrides,
  }
}

describe('PreviewPartsPanel', () => {
  test('renders a row per part with name, source line and color chip', () => {
    render(<PreviewPartsPanel {...baseProps()} />)

    expect(screen.getByText('BLOCK')).toBeTruthy()
    expect(screen.getByText('3d.gdl:42')).toBeTruthy()
    expect(screen.getByText('RULED_0')).toBeTruthy()
    // chip 色与视图模型一致（jsdom 会把 hsl 归一化为 rgb：hsl(200,50%,60%) = rgb(102,170,204)）
    const chip = screen.getByText('BLOCK').closest('.parts-row')?.querySelector('.parts-chip') as HTMLElement
    expect(chip.style.background).toBe('rgb(102, 170, 204)')
  })

  test('clicking a row selects that part', () => {
    const onSelect = vi.fn()
    render(<PreviewPartsPanel {...baseProps({ onSelect })} />)

    fireEvent.click(screen.getByText('RULED_0'))
    expect(onSelect).toHaveBeenCalledWith(1)
  })

  test('double-clicking a row jumps to source', () => {
    const onJump = vi.fn()
    render(<PreviewPartsPanel {...baseProps({ onJump })} />)

    fireEvent.doubleClick(screen.getByText('RULED_0'))
    expect(onJump).toHaveBeenCalledWith(1)
  })

  test('eye button toggles hidden without selecting the row', () => {
    const onToggleHidden = vi.fn()
    const onSelect = vi.fn()
    render(<PreviewPartsPanel {...baseProps({ onToggleHidden, onSelect })} />)

    fireEvent.click(screen.getByLabelText('隐藏部件'))
    expect(onToggleHidden).toHaveBeenCalledWith(0)
    expect(onToggleHidden).toHaveBeenCalledTimes(1)
    expect(onSelect).not.toHaveBeenCalled()
  })

  test('hidden rows show the show-part action', () => {
    render(<PreviewPartsPanel {...baseProps()} />)
    expect(screen.getByLabelText('显示部件')).toBeTruthy()
  })

  test('jump button only exists for rows with a source line', () => {
    render(<PreviewPartsPanel {...baseProps()} />)
    expect(screen.getAllByLabelText('跳转到源码')).toHaveLength(1)
  })

  test('selected row gets the selected class', () => {
    render(<PreviewPartsPanel {...baseProps({ selectedIndex: 1 })} />)
    const row = screen.getByText('RULED_0').closest('.parts-row')
    expect(row?.className).toContain('selected')
  })
})
