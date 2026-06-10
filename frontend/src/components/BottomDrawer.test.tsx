import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
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
