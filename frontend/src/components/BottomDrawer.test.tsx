import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { BottomDrawer } from './BottomDrawer'
import type { MockCompileResponse } from '../api/types'

function makeResult(overrides: Partial<MockCompileResponse> = {}): MockCompileResponse {
  return {
    success: true,
    mode: 'mock',
    issues: [],
    duration_ms: 12,
    ...overrides,
  }
}

describe('BottomDrawer compile status', () => {
  test('shows passed badge after a successful compile', () => {
    render(<BottomDrawer warnings={[]} compileLog={[]} mockCompileResult={makeResult()} />)

    expect(screen.getByText('✓ Passed')).toBeTruthy()
  })

  test('shows failed badge and the compile error message', () => {
    render(
      <BottomDrawer
        warnings={[]}
        compileLog={[]}
        mockCompileResult={makeResult({ success: false, error: 'LP_XMLConverter not found' })}
      />,
    )

    expect(screen.getByText('✗ Failed')).toBeTruthy()
    expect(screen.getByText('LP_XMLConverter not found')).toBeTruthy()
  })

  test('shows running badge while compiling', () => {
    render(<BottomDrawer warnings={[]} compileLog={[]} mockCompileResult={null} compiling />)

    expect(screen.getByText('● Compiling…')).toBeTruthy()
  })

  test('shows no badge before the first compile', () => {
    render(<BottomDrawer warnings={[]} compileLog={[]} mockCompileResult={null} />)

    expect(screen.queryByText('✓ Passed')).toBeNull()
    expect(screen.queryByText('✗ Failed')).toBeNull()
    expect(screen.getByText('Not compiled')).toBeTruthy()
  })
})

describe('BottomDrawer tabs', () => {
  test('merges Compile and Diagnostics into a single Compile tab', () => {
    render(<BottomDrawer warnings={[]} compileLog={[]} mockCompileResult={null} />)

    expect(screen.getByRole('button', { name: 'Compile' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Revision' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Diagnostics' })).toBeNull()
    expect(screen.queryByText('Diagnostics')).toBeNull()
  })
})

describe('BottomDrawer auto-switch on failed compile', () => {
  function renderOnPreview(warnings: string[], result: MockCompileResponse | null) {
    const view = render(
      <BottomDrawer warnings={warnings} compileLog={[]} mockCompileResult={result} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    return view
  }

  test('switches to Compile when a new failed result arrives', () => {
    const { rerender } = renderOnPreview(['preview warn'], null)
    expect(screen.getByText(/preview warn/)).toBeTruthy()

    rerender(
      <BottomDrawer
        warnings={['preview warn']}
        compileLog={[]}
        mockCompileResult={makeResult({ success: false, error: 'boom' })}
      />,
    )

    expect(screen.getByText('✗ Failed')).toBeTruthy()
    expect(screen.getByText('boom')).toBeTruthy()
    expect(screen.queryByText(/preview warn/)).toBeNull()
  })

  test('does not switch tabs for a new successful result', () => {
    const { rerender } = renderOnPreview(['preview warn'], null)

    rerender(<BottomDrawer warnings={['preview warn']} compileLog={[]} mockCompileResult={makeResult()} />)

    expect(screen.getByText(/preview warn/)).toBeTruthy()
    expect(screen.queryByText('✓ Passed')).toBeNull()
  })

  test('does not re-switch for an already-handled result reference', () => {
    const failed = makeResult({ success: false, error: 'boom' })
    const { rerender } = renderOnPreview(['preview warn'], failed)

    rerender(<BottomDrawer warnings={['preview warn']} compileLog={[]} mockCompileResult={failed} />)

    expect(screen.getByText(/preview warn/)).toBeTruthy()
    expect(screen.queryByText('✗ Failed')).toBeNull()
  })
})

describe('BottomDrawer diagnostic pills and stacking', () => {
  test('renders severity pills in the summary', () => {
    render(
      <BottomDrawer
        warnings={[]}
        compileLog={[]}
        mockCompileResult={makeResult({
          success: false,
          issues: [
            { severity: 'error', script: 'a.gdl', line: 1, message: 'e1' },
            { severity: 'warning', script: 'a.gdl', line: 2, message: 'w1' },
            { severity: 'info', script: 'a.gdl', line: 3, message: 'i1' },
          ],
        })}
      />,
    )

    expect(screen.getByText('1 error')).toBeTruthy()
    expect(screen.getByText('1 warning')).toBeTruthy()
    expect(screen.getByText('1 info')).toBeTruthy()
  })

  test('stacks duplicate issues into one row with a ×N badge', () => {
    const duplicate = { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' }
    render(
      <BottomDrawer
        warnings={[]}
        compileLog={[]}
        mockCompileResult={makeResult({ success: false, issues: [duplicate, duplicate] })}
      />,
    )

    expect(screen.getAllByText(/FOR\/NEXT mismatch/)).toHaveLength(1)
    expect(screen.getByText('×2')).toBeTruthy()
  })

  test('clicking a stacked row selects the first retained issue', () => {
    const onIssueSelect = vi.fn()
    const duplicate = { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' }
    render(
      <BottomDrawer
        warnings={[]}
        compileLog={[]}
        mockCompileResult={makeResult({ success: false, issues: [duplicate, duplicate] })}
        onIssueSelect={onIssueSelect}
      />,
    )

    fireEvent.click(screen.getByText('scripts/3d.gdl:12 - FOR/NEXT mismatch'))
    expect(onIssueSelect).toHaveBeenCalledTimes(1)
    expect(onIssueSelect).toHaveBeenCalledWith(duplicate)
  })
})
