import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { Preview2DPayload } from '../api/types'
import { Preview2DViewport } from './Preview2DViewport'
import { fitView2D, toViewBoxString } from './preview2dView'

function samplePreview(): Preview2DPayload {
  return {
    lines: [{ from: [0, 0], to: [4, 0] }],
    polygons: [[[0, 0], [2, 0], [2, 2], [0, 2]]],
    circles: [{ cx: 1, cy: 1, r: 0.5 }],
    arcs: [],
  }
}

function svgElement() {
  return screen.getByRole('img', { name: '2D preview' }) as unknown as SVGSVGElement
}

function mockSvgSize(svg: SVGSVGElement, width = 400, height = 400) {
  Object.defineProperty(svg, 'clientWidth', { value: width, configurable: true })
  Object.defineProperty(svg, 'clientHeight', { value: height, configurable: true })
  svg.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width, height, right: width, bottom: height } as DOMRect)
}

describe('Preview2DViewport (P3c)', () => {
  test('renders initial fit viewBox for a payload', () => {
    const preview = samplePreview()
    render(<Preview2DViewport preview={preview} warnings={[]} />)

    const fit = fitView2D({ minX: 0, minY: 0, maxX: 4, maxY: 2 })
    expect(svgElement().getAttribute('viewBox')).toBe(toViewBoxString(fit))
    // 实体计数：1 line + 1 polygon + 1 circle
    expect(screen.getByText('3 entities')).toBeTruthy()
  })

  test('shows empty state without geometry', () => {
    render(<Preview2DViewport preview={null} warnings={['w1']} />)
    expect(screen.getByText('No 2D geometry')).toBeTruthy()
    expect(screen.getByText('0 entities | 1 warnings')).toBeTruthy()
  })

  test('wheel zooms in anchored at the cursor', () => {
    const preview = samplePreview()
    render(<Preview2DViewport preview={preview} warnings={[]} />)
    const svg = svgElement()
    mockSvgSize(svg)

    const before = svg.getAttribute('viewBox')!
    fireEvent.wheel(svg, { deltaY: -120, clientX: 200, clientY: 200 })
    const after = svg.getAttribute('viewBox')!

    expect(after).not.toBe(before)
    // 光标在 viewBox 中心 → 缩放后中心点对应的 viewBox 坐标不变
    const [bMinX, bMinY, bW, bH] = before.split(' ').map(Number)
    const [aMinX, aMinY, aW, aH] = after.split(' ').map(Number)
    expect((200 / 400) * bW + bMinX).toBeCloseTo((200 / 400) * aW + aMinX, 6)
    expect((200 / 400) * bH + bMinY).toBeCloseTo((200 / 400) * aH + aMinY, 6)
    expect(aW).toBeLessThan(bW)
  })

  test('double click resets to fit viewBox after zoom', () => {
    const preview = samplePreview()
    render(<Preview2DViewport preview={preview} warnings={[]} />)
    const svg = svgElement()
    mockSvgSize(svg)

    fireEvent.wheel(svg, { deltaY: -120, clientX: 200, clientY: 200 })
    expect(svg.getAttribute('viewBox')).not.toBe(toViewBoxString(fitView2D({ minX: 0, minY: 0, maxX: 4, maxY: 2 })))

    fireEvent.doubleClick(svg)
    expect(svg.getAttribute('viewBox')).toBe(toViewBoxString(fitView2D({ minX: 0, minY: 0, maxX: 4, maxY: 2 })))
  })

  test('drag pans the view', () => {
    const preview = samplePreview()
    render(<Preview2DViewport preview={preview} warnings={[]} />)
    const svg = svgElement()
    mockSvgSize(svg)

    const before = svg.getAttribute('viewBox')!
    fireEvent.pointerDown(svg, { button: 0, clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(svg, { clientX: 140, clientY: 120, pointerId: 1 })
    fireEvent.pointerUp(svg, { clientX: 140, clientY: 120, pointerId: 1 })
    const after = svg.getAttribute('viewBox')!

    const [bMinX, bMinY, bW, bH] = before.split(' ').map(Number)
    const [aMinX, aMinY, aW, aH] = after.split(' ').map(Number)
    // 向右下拖 40/20px → 视口内容向右下移（viewBox 原点反方向）
    expect(aMinX).toBeCloseTo(bMinX - (40 / 400) * bW, 6)
    expect(aMinY).toBeCloseTo(bMinY - (20 / 400) * bH, 6)
    expect(aW).toBeCloseTo(bW, 9)
    expect(aH).toBeCloseTo(bH, 9)
  })
})
