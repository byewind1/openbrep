import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { workbenchStore } from '../state/workbenchStore'
import { useConfigAutoRefresh } from './useConfigAutoRefresh'

describe('useConfigAutoRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('polls the config revision on an interval and stops after unmount', () => {
    const pollConfigRevision = vi.fn(async () => undefined)
    workbenchStore.setState({ pollConfigRevision })

    const { unmount } = renderHook(() => useConfigAutoRefresh(1000))

    expect(pollConfigRevision).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(pollConfigRevision).toHaveBeenCalledTimes(3)

    unmount()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(pollConfigRevision).toHaveBeenCalledTimes(3)
  })
})
