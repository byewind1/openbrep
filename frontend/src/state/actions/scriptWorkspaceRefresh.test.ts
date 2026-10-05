import { describe, expect, it } from 'vitest'
import { createWorkbenchStore } from '../workbenchStore'
import type { WorkbenchApi } from '../workbenchStore'

/**
 * 卡02（任务反馈与连续对话修复）：前端代次消费。
 *
 * refreshProjectWorkspace 的快照分支必须回填 sessionId/projectEpoch——
 * 后端代次变化（如 Save As / 重开会话）后，客户端不能继续以旧代次发起
 * prepare；身份不一致（长请求期间项目/后端会话已切换）时整块跳过，
 * 不把旧项目快照写进新项目状态。
 */

function baseSnapshot() {
  return {
    ok: true,
    session_id: 'backend-2',
    project_epoch: 7,
    project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
    parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
  }
}

function makeApi(overrides: Partial<WorkbenchApi> = {}): WorkbenchApi {
  return {
    listProjectScripts: async () => ({ ok: true, scripts: [] }),
    fetchSnapshot: async () => baseSnapshot(),
    ...overrides,
  } as unknown as WorkbenchApi
}

describe('refreshProjectWorkspace 代次回填（卡02）', () => {
  it('快照分支回填 sessionId/projectEpoch，收敛代次漂移', async () => {
    const store = createWorkbenchStore(makeApi())
    store.setState({ sessionId: 'backend-1', projectEpoch: 6 })
    await store.getState().refreshProjectWorkspace({ refreshPreview: false, refreshParameters: true })
    expect(store.getState().sessionId).toBe('backend-2')
    expect(store.getState().projectEpoch).toBe(7)
  })

  it('身份不一致时不接入快照（防跨项目污染）', async () => {
    let store!: ReturnType<typeof createWorkbenchStore>
    const api = makeApi({
      listProjectScripts: async () => {
        // loadScripts 期间项目/会话被切换（模拟用户在长请求中切换）
        store.setState({ sessionId: 'backend-9', projectEpoch: 99 })
        return { ok: true, scripts: [] }
      },
    })
    store = createWorkbenchStore(api)
    store.setState({ sessionId: 'backend-1', projectEpoch: 6 })
    await store.getState().refreshProjectWorkspace({ refreshPreview: false, refreshParameters: true })
    // 快照（backend-2 / epoch 7）不得覆盖已切换后的状态（backend-9 / epoch 99）
    expect(store.getState().sessionId).toBe('backend-9')
    expect(store.getState().projectEpoch).toBe(99)
  })
})
