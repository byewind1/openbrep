import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { BACKEND_RECOVERED_MESSAGE, createBackendHealth } from './backendHealth'
import type { WorkbenchGet, WorkbenchSet } from './workbenchStoreTypes'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return {
    ...original,
    probeBackend: vi.fn().mockResolvedValue(false),
    setApiHealthListener: vi.fn(),
  }
})

import { probeBackend } from '../api/client'

function makeStore() {
  const state: Record<string, unknown> = {
    backendError: null,
    backendNotice: null,
    lastError: null,
  }
  const set = (partial: Record<string, unknown> | ((s: unknown) => Record<string, unknown>) | unknown) =>
    Object.assign(state, typeof partial === 'function' ? (partial as (s: unknown) => unknown)(state) : partial)
  const get = () => state
  return {
    state,
    set: set as unknown as WorkbenchSet,
    get: get as unknown as WorkbenchGet,
  }
}

describe('backendHealth 看门狗', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(probeBackend).mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  test('down 事件：分型文案含启动指引，并启动 3s 恢复轮询', () => {
    const { state, set, get } = makeStore()
    const health = createBackendHealth({ get, set })
    health.handleEvent({ kind: 'down' })

    expect(state.backendError).toMatchObject({ kind: 'down' })
    expect(String(state.lastError)).toContain('后端服务未运行')
    expect(String(state.lastError)).toContain('obr7 --daemon')
    expect(String(state.lastError)).toContain('obr7 --status')

    vi.advanceTimersByTime(3100)
    expect(probeBackend).toHaveBeenCalled()
  })

  test('starting 事件：502/504 文案为"启动中"', () => {
    const { state, set, get } = makeStore()
    const health = createBackendHealth({ get, set })
    health.handleEvent({ kind: 'starting', status: 502 })

    expect(state.backendError).toMatchObject({ kind: 'starting' })
    expect(String(state.lastError)).toContain('启动中')
  })

  test('timeout 事件：文案为"响应超时"', () => {
    const { state, set, get } = makeStore()
    const health = createBackendHealth({ get, set })
    health.handleEvent({ kind: 'timeout' })

    expect(state.backendError).toMatchObject({ kind: 'timeout' })
    expect(String(state.lastError)).toContain('超时')
  })

  test('down 之后 ok：清错误、显示已恢复、停轮询，提示数秒后自动消失', () => {
    const { state, set, get } = makeStore()
    const health = createBackendHealth({ get, set })
    health.handleEvent({ kind: 'down' })
    vi.mocked(probeBackend).mockClear()

    health.handleEvent({ kind: 'ok' })

    expect(state.backendError).toBeNull()
    expect(state.lastError).toBeNull()
    expect(state.backendNotice).toBe(BACKEND_RECOVERED_MESSAGE)

    vi.advanceTimersByTime(6200)  // 3s 轮询应已停止
    expect(probeBackend).not.toHaveBeenCalled()

    vi.advanceTimersByTime(4200)  // 提示自动消失
    expect(state.backendNotice).toBeNull()
  })

  test('ok 且本来就没错误：不产生恢复提示', () => {
    const { state, set, get } = makeStore()
    const health = createBackendHealth({ get, set })
    health.handleEvent({ kind: 'ok' })
    expect(state.backendNotice).toBeNull()
    expect(state.lastError).toBeNull()
  })
})
