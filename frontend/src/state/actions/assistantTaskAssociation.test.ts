import { describe, expect, it } from 'vitest'
import { createWorkbenchStore } from '../workbenchStore'
import type { AssistantHistoryItem } from '../../api/types'
import type { WorkbenchApi } from '../workbenchStore'

/**
 * RF04（返修派单 P1）：保存所有聊天结果与任务关联，异常可复盘。
 *
 * - 咨询/失败/取消等全部终端分支立即落盘聊天正文与 task_ref；
 * - 重开加载时从任务索引发现"已开始未结束"任务并合成条目（不伪造最终答复）。
 */

function projectFixture() {
  return { name: 'Chair', source: 'hsf', path: '/workspace/Chair' }
}

function baseApi(overrides: Partial<WorkbenchApi> = {}, saved: AssistantHistoryItem[][] = []): WorkbenchApi {
  return {
    listProjectScripts: async () => ({ ok: true, scripts: [] }),
    fetchSnapshot: async () => ({
      ok: true, session_id: 's1', project_epoch: 1,
      project: projectFixture(), parameters: [],
    }),
    saveAssistantHistory: async (messages: AssistantHistoryItem[]) => {
      saved.push(messages)
      return { ok: true, count: messages.length }
    },
    ...overrides,
  } as unknown as WorkbenchApi
}

describe('RF04 聊天结果与任务关联落盘', () => {
  it('咨询轮完成后立即保存，assistant 消息带 task_ref', async () => {
    const saved: AssistantHistoryItem[][] = []
    const api = baseApi({
      conversationTurn: async (body: Record<string, unknown>) => {
        if (body.phase === 'prepare') {
          return { ok: true, result_kind: 'advice', turn_id: 'turn-adv',
            project_epoch: 1, assistant: { kind: 'advisor', reply: 'A 是宽度。' } } as never
        }
        return { ok: false, error: 'unexpected' }
      },
    }, saved)
    const store = createWorkbenchStore(api)
    store.setState({ project: projectFixture() as never, llmSettings: {
      ...store.getState().llmSettings, conversation_entry: 'unified' } })
    await store.getState().sendChat('什么是GDL')
    expect(saved.length).toBe(1)
    const assistantSaved = saved[0].find((m) => m.role === 'assistant')
    expect(assistantSaved?.content).toBe('A 是宽度。')
    expect(assistantSaved?.meta?.task_ref?.turn_id).toBe('turn-adv')
  })

  it('失败轮也保存（异常可复盘）', async () => {
    const saved: AssistantHistoryItem[][] = []
    const api = baseApi({
      conversationTurn: async (body: Record<string, unknown>) => {
        if (body.phase === 'prepare') {
          return { ok: true, result_kind: 'ready_to_execute', turn_id: 'turn-fail',
            project_epoch: 1, task_intent: 'MODIFY' }
        }
        return { ok: false, result_kind: 'failed', turn_id: 'turn-fail', code: 'EXECUTION_FAILED',
          project_epoch: 1, error: '执行过程中发生错误。' } as never
      },
    }, saved)
    const store = createWorkbenchStore(api)
    store.setState({ project: projectFixture() as never, llmSettings: {
      ...store.getState().llmSettings, conversation_entry: 'unified' } })
    await store.getState().sendChat('把A改成2')
    expect(saved.length).toBeGreaterThanOrEqual(1)
    const last = saved.at(-1) as AssistantHistoryItem[]
    expect(last.some((m) => m.role === 'assistant' && m.meta?.task_ref?.turn_id === 'turn-fail')).toBe(true)
  })

  it('重开加载：从任务索引合成未完成任务条目（不伪造答复）', async () => {
    const api = baseApi({
      listAssistantHistory: async () => ({ ok: true, messages: [] }),
      listTurnEvents: async () => ({ ok: true, turns: [
        { turn_id: 'turn-crash', terminal: false, last_kind: 'tool_started',
          started_at: '2026-10-05T10:00:00+00:00', message: '把A改成2' },
      ] }),
      fetchTurnEvents: async (turnId: string) => ({
        ok: true, turn_id: turnId, events: [
          { seq: 1, event_id: 'e1', timestamp: '', kind: 'accepted', message: '把A改成2' },
          { seq: 2, event_id: 'e2', timestamp: '', kind: 'tool_started', tool_name: 'update_script' },
        ],
      }),
    })
    const store = createWorkbenchStore(api)
    store.setState({ project: projectFixture() as never })
    await store.getState().loadAssistantHistory()
    const messages = store.getState().assistantMessages
    expect(messages).toHaveLength(1)
    expect(messages[0].content).toContain('任务未完成')
    expect(messages[0].content).toContain('没有最终答复')
    expect(messages[0].turnTaskRef?.turn_id).toBe('turn-crash')
    expect(messages[0].thinkingSteps?.length).toBeGreaterThan(0)
  })

  it('已有关联消息（task_ref）的 turn 不重复合成', async () => {
    const api = baseApi({
      listAssistantHistory: async () => ({ ok: true, messages: [
        { role: 'assistant', content: '进行中',
          task_ref: { turn_id: 'turn-live', schema_version: 1 } },
      ] }),
      listTurnEvents: async () => ({ ok: true, turns: [
        { turn_id: 'turn-live', terminal: false, last_kind: 'preparing' },
      ] }),
      fetchTurnEvents: async () => ({ ok: true, events: [] }),
    })
    const store = createWorkbenchStore(api)
    store.setState({ project: projectFixture() as never })
    await store.getState().loadAssistantHistory()
    expect(store.getState().assistantMessages).toHaveLength(1)
  })
})
