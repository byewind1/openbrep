import { describe, expect, test, vi } from 'vitest'
import { createRevisionActions } from './revisionActions'
import type { WorkbenchApi, WorkbenchGet, WorkbenchSet } from '../workbenchStoreTypes'

function makeContext(project: unknown) {
  const api = {
    listProjectRevisions: vi.fn(async () => ({ ok: true, revisions: [], latest_revision_id: null })),
  } as unknown as WorkbenchApi
  const state: Record<string, unknown> = { project, revisionLoading: false, revisions: [], latestRevisionId: null }
  const set = (partial: unknown) =>
    Object.assign(state, typeof partial === 'function' ? (partial as (s: unknown) => unknown)(state) : partial)
  const get = () => state
  return {
    api,
    get: get as unknown as WorkbenchGet,
    set: set as unknown as WorkbenchSet,
    state,
  }
}

describe('loadRevisions 修复 3：未打开项目不发请求', () => {
  test('project 为 null 时不请求后端（避免无项目 revisions 404 刷红错）', async () => {
    const { api, get, set, state } = makeContext(null)
    await createRevisionActions({ api, get, set }).loadRevisions()

    expect(api.listProjectRevisions).not.toHaveBeenCalled()
    expect(state.revisions).toEqual([])
    expect(state.revisionLoading).toBe(false)
  })

  test('有项目时正常请求', async () => {
    const { api, get, set } = makeContext({ name: 'Chair', path: '/tmp/Chair' })
    await createRevisionActions({ api, get, set }).loadRevisions()

    expect(api.listProjectRevisions).toHaveBeenCalled()
  })
})
