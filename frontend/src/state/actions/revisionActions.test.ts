import { describe, expect, test, vi } from 'vitest'
import { createRevisionActions } from './revisionActions'
import type { DeliveryPresentation } from '../../api/types'
import type { WorkbenchApi, WorkbenchGet, WorkbenchSet } from '../workbenchStoreTypes'

function makeContext(initial: Record<string, unknown>) {
  const api = {
    listProjectRevisions: vi.fn(async () => ({ ok: true, revisions: [], latest_revision_id: null })),
    saveProjectRevision: vi.fn(async () => ({
      ok: true,
      revision: { revision_id: 'r0009' },
      latest_revision_id: 'r0009',
    })),
    restoreProjectRevision: vi.fn(async (revisionId: string, draftPolicy?: string | null) => ({
      ok: true,
      restored_revision_id: revisionId,
      project: { name: 'Chair', path: '/tmp/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [] },
      warnings: [],
      restore: {
        revision_id: revisionId,
        draft_policy: draftPolicy ?? null,
        hsf_reloaded: true,
        preview_cleared: true,
      },
    })),
    getProjectRevisionDiff: vi.fn(async (fromId: string, toId: string) => ({
      ok: true,
      from_revision_id: fromId,
      to_revision_id: toId,
      diff: '--- r0001/scripts/3d.gdl\n+++ r0002/scripts/3d.gdl\n',
      changed: true,
    })),
  } as unknown as WorkbenchApi
  const state: Record<string, unknown> = {
    project: { name: 'Chair', path: '/tmp/Chair' },
    revisionLoading: false,
    revisions: [],
    latestRevisionId: null,
    lastError: null,
    compileLog: [] as string[],
    dirtyScripts: {} as Record<string, boolean>,
    draftParameters: {} as Record<string, unknown>,
    scriptContents: {} as Record<string, string>,
    pendingDeliveryContinue: null,
    sendChat: vi.fn(async () => {}),
    loadScripts: vi.fn(async () => {}),
    loadRevisions: vi.fn(async () => {}),
    compilerSettings: { mode: 'mock', converter_path: '', output_dir: '' },
    llmSettings: {},
    ...initial,
  }
  const set = (partial: unknown) => {
    const patch = typeof partial === 'function' ? (partial as (s: unknown) => unknown)(state) : partial
    Object.assign(state, patch)
  }
  const get = () => state
  return { api, get: get as unknown as WorkbenchGet, set: set as unknown as WorkbenchSet, state }
}

function draftsOf(state: Record<string, unknown>) {
  return {
    dirty: state.dirtyScripts as Record<string, boolean>,
    contents: state.scriptContents as Record<string, string>,
    params: state.draftParameters as Record<string, unknown>,
    log: state.compileLog as string[],
  }
}

const partialDelivery: DeliveryPresentation = {
  state: 'partial_change',
  status: 'incomplete',
  unlinked: false,
  headline: '未完成，存在部分修改',
  reason: '任务中断或验证未完成，存在部分修改；after 版本未创建',
  show_success_badge: false,
  show_before_after: false,
  show_changed_files: true,
  can_recover: true,
  can_continue: true,
  can_view_diff: true,
  recover_revision_id: 'r0001',
  before_revision_id: 'r0001',
  after_revision_id: null,
  changed_files: ['scripts/3d.gdl'],
  run_id: 'r_20260918_120000_abc123',
  error_code: null,
  check_status: 'unknown',
  version_status: 'skipped',
  original_instruction: '把层板数改成 5',
  continued_from: null,
}

describe('loadRevisions 修复 3：未打开项目不发请求', () => {
  test('project 为 null 时不请求后端（避免无项目 revisions 404 刷红错）', async () => {
    const { api, get, set, state } = makeContext({ project: null })
    await createRevisionActions({ api, get, set }).loadRevisions()

    expect(api.listProjectRevisions).not.toHaveBeenCalled()
    expect(state.revisions).toEqual([])
    expect(state.revisionLoading).toBe(false)
  })

  test('有项目时正常请求', async () => {
    const { api, get, set } = makeContext({ project: { name: 'Chair', path: '/tmp/Chair' } })
    await createRevisionActions({ api, get, set }).loadRevisions()

    expect(api.listProjectRevisions).toHaveBeenCalled()
  })
})

describe('ST03 restoreRevision 草稿保护', () => {
  test('U02：有草稿且未指定 draftPolicy → 拒绝调用 API，草稿不变', async () => {
    const { api, get, set, state } = makeContext({
      dirtyScripts: { '3d.gdl': true },
      scriptContents: { '3d.gdl': 'PRIM 1' },
      draftParameters: { A: 2 },
    })
    await createRevisionActions({ api, get, set }).restoreRevision('r0001')

    expect(api.restoreProjectRevision).not.toHaveBeenCalled()
    const d = draftsOf(state)
    expect(d.dirty).toEqual({ '3d.gdl': true })
    expect(d.params).toEqual({ A: 2 })
    expect(d.contents['3d.gdl']).toBe('PRIM 1')
    expect(String(state.lastError)).toContain('draft')
  })

  test('U03 keep：恢复后磁盘 HSF 重载，编辑器草稿叠回且 dirty 保持', async () => {
    const { api, get, set, state } = makeContext({
      dirtyScripts: { '3d.gdl': true },
      scriptContents: { '3d.gdl': 'PRIM 99' },
      draftParameters: { A: 5 },
    })
    await createRevisionActions({ api, get, set }).restoreRevision('r0001', { draftPolicy: 'keep' })

    expect(api.restoreProjectRevision).toHaveBeenCalledWith('r0001', 'keep')
    const d = draftsOf(state)
    expect(d.dirty['3d.gdl']).toBe(true)
    expect(d.contents['3d.gdl']).toBe('PRIM 99')
    expect(d.params).toEqual({ A: 5 })
    expect(state.pendingDeliveryContinue).toBeNull()
    expect(String(d.log[0])).toContain('Restored revision r0001')
    expect(String(d.log[0])).toContain('drafts kept')
  })

  test('discard：API 收到 discard，草稿被 hydrate 清空', async () => {
    const { api, get, set, state } = makeContext({
      dirtyScripts: { '3d.gdl': true },
      scriptContents: { '3d.gdl': 'PRIM 99' },
      draftParameters: { A: 5 },
    })
    await createRevisionActions({ api, get, set }).restoreRevision('r0001', { draftPolicy: 'discard' })

    expect(api.restoreProjectRevision).toHaveBeenCalledWith('r0001', 'discard')
    const d = draftsOf(state)
    expect(d.dirty).toEqual({})
    expect(d.params).toEqual({})
  })

  test('无草稿时可不传 draftPolicy（向后兼容 RevisionPanel 清洁路径）', async () => {
    const { api, get, set, state } = makeContext({})
    await createRevisionActions({ api, get, set }).restoreRevision('r0002')

    expect(api.restoreProjectRevision).toHaveBeenCalledWith('r0002', null)
    expect(String(draftsOf(state).log[0])).toBe('Restored revision r0002')
  })
})

describe('ST03 recoverDeliveryBefore / viewRevisionDiff / continueDelivery', () => {
  test('U01 recover：partial delivery 恢复到 before_revision_id', async () => {
    const { api, get, set } = makeContext({})
    const ok = await createRevisionActions({ api, get, set }).recoverDeliveryBefore(partialDelivery, {
      draftPolicy: 'discard',
    })
    expect(ok).toBe(true)
    expect(api.restoreProjectRevision).toHaveBeenCalledWith('r0001', 'discard')
  })

  test('U01 unlinked/partial 无 recover id 时拒绝', async () => {
    const { api, get, set, state } = makeContext({})
    const ok = await createRevisionActions({ api, get, set }).recoverDeliveryBefore({
      ...partialDelivery,
      can_recover: false,
      recover_revision_id: null,
    })
    expect(ok).toBe(false)
    expect(api.restoreProjectRevision).not.toHaveBeenCalled()
    expect(String(state.lastError)).toContain('No recoverable')
  })

  test('viewRevisionDiff 调用 diff API', async () => {
    const { api, get, set } = makeContext({})
    const text = await createRevisionActions({ api, get, set }).viewRevisionDiff('r0001', 'r0002')
    expect(api.getProjectRevisionDiff).toHaveBeenCalledWith('r0001', 'r0002')
    expect(text).toContain('r0001')
  })

  test('U06 continueDelivery：带回原始指令并写入 pendingDeliveryContinue', async () => {
    const { api, get, set, state } = makeContext({})
    await createRevisionActions({ api, get, set }).continueDelivery({
      originRunId: 'r_20260918_120000_abc123',
      originalInstruction: '把层板数改成 5',
    })
    expect(state.pendingDeliveryContinue).toEqual({
      origin_run_id: 'r_20260918_120000_abc123',
      original_instruction: '把层板数改成 5',
      intent: undefined,
    })
    expect(state.sendChat).toHaveBeenCalledWith('把层板数改成 5')
  })

  test('U06 缺原 run 或原指令时拒绝继续', async () => {
    const { api, get, set, state } = makeContext({})
    const actions = createRevisionActions({ api, get, set })
    await actions.continueDelivery({ originRunId: null, originalInstruction: '把层板数改成 5' })
    expect(String(state.lastError)).toContain('run id')
    await actions.continueDelivery({ originRunId: 'r_old', originalInstruction: '   ' })
    expect(String(state.lastError)).toContain('instruction')
  })
})
