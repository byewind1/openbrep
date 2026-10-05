import { describe, expect, it } from 'vitest'
import {
  eventToThinkingStep,
  hydrateHistoryMessages,
  taskEventsToThinkingSteps,
  withHistoryMeta,
} from './assistantActions'
import type { AssistantMessage, AssistantStreamEvent, TaskEvent } from '../../api/types'

/**
 * 卡01（任务反馈与连续对话修复）：前端复盘契约冻结——预期红灯回归。
 *
 * 原始事故：时间线 thinkingSteps 只活在内存里，withHistoryMeta 不保存、
 * hydrateHistoryMessages 不恢复 → 重开项目后“旧记录未保存执行过程”，
 * 无法复盘失败、中断与部分修改。
 *
 * 卡05 已实现保存/恢复：it.fails 预期红灯已按 strict 纪律转为正式测试。
 */

const steps: NonNullable<AssistantMessage['thinkingSteps']> = [
  { type: 'status', stage: 'plan', message: '正在准备执行步骤…' },
  { type: 'tool_call', stage: 'think', message: 'update_script', detail: 'scripts/3d.gdl', ok: true },
  { type: 'status', stage: 'compile', message: '✅ 编译通过', ok: true },
]

describe('任务时间线历史往返（卡01 冻结 → 卡05 转绿）', () => {
  it('withHistoryMeta 保存 thinking_steps 元数据', () => {
    const message: AssistantMessage = {
      role: 'assistant',
      content: '已加一层板，编译通过。',
      thinkingSteps: [...steps],
    }
    const saved = withHistoryMeta(message)
    expect(saved.meta?.thinking_steps).toEqual(steps)
  })

  it('刷新后 hydrateHistoryMessages 恢复完整时间线步骤', () => {
    const message: AssistantMessage = {
      role: 'assistant',
      content: '已加一层板，编译通过。',
      thinkingSteps: [...steps],
    }
    const saved = withHistoryMeta(message)
    // 模拟后端 list → 前端 hydrate（meta 原样返回）
    const restored = hydrateHistoryMessages([
      { role: 'assistant', content: saved.content, meta: saved.meta } as AssistantMessage,
    ])
    expect(restored[0].thinkingSteps).toEqual(steps)
  })

  it('旧记录没有 thinking_steps 时不编造历史（已有行为冻结）', () => {
    const restored = hydrateHistoryMessages([
      { role: 'assistant', content: '旧答复' } as AssistantMessage,
    ])
    expect(restored[0].thinkingSteps).toBeUndefined()
  })
})

describe('task_ref 与任务事件恢复（卡05）', () => {
  it('withHistoryMeta 持久化 task_ref，hydrate 恢复关联', () => {
    const message: AssistantMessage = {
      role: 'assistant',
      content: '已完成修改。',
      turnTaskRef: { turn_id: 'turn-9', run_id: 'r_1', schema_version: 1 },
      thinkingSteps: [{ type: 'status', stage: 'done', message: '完成' }],
    }
    const saved = withHistoryMeta(message)
    expect(saved.meta?.task_ref).toEqual({ turn_id: 'turn-9', run_id: 'r_1', schema_version: 1 })
    const restored = hydrateHistoryMessages([
      { role: 'assistant', content: saved.content, meta: saved.meta } as AssistantMessage,
    ])
    expect(restored[0].turnTaskRef?.turn_id).toBe('turn-9')
    expect(restored[0].thinkingSteps).toHaveLength(1)
  })

  it('taskEventsToThinkingSteps：start/finish 三态 + 验证 + 部分完成', () => {
    const events: TaskEvent[] = [
      { seq: 1, event_id: 'e1', timestamp: '', kind: 'accepted' },
      { seq: 2, event_id: 'e2', timestamp: '', kind: 'tool_started', tool_name: 'update_script' },
      { seq: 3, event_id: 'e3', timestamp: '', kind: 'tool_finished', tool_name: 'update_script', state: 'succeeded', summary: '已更新 scripts/3d.gdl' },
      { seq: 4, event_id: 'e4', timestamp: '', kind: 'tool_started', tool_name: 'compile_script' },
      { seq: 5, event_id: 'e5', timestamp: '', kind: 'tool_finished', tool_name: 'compile_script', state: 'failed', summary: '编译失败' },
      { seq: 6, event_id: 'e6', timestamp: '', kind: 'verification', stage: 'compile', state: 'succeeded' },
      { seq: 7, event_id: 'e7', timestamp: '', kind: 'completed', state: 'partial', message: '部分修改' },
    ]
    const steps = taskEventsToThinkingSteps(events)
    // RF05：start/finish 收束为同一行（4 个工具行 = 2 start + 2 finish）
    expect(steps).toHaveLength(4)
    const [finish1, finish2, verify, done] = steps
    expect(finish1.ok).toBe(true)
    expect(finish1.detail).toBe('已更新 scripts/3d.gdl')
    expect(finish2.ok).toBe(false)
    expect(finish2.message).toContain('compile_script')
    expect(verify.ok).toBe(true)
    expect(done.message).toContain('部分完成')
    expect(done.ok).toBe(false)
  })
})

describe('RF02 公开 commentary（canonical 事件）', () => {
  it('public_commentary 事件显示正文，不泛化为思考中', () => {
    const step = eventToThinkingStep({
      type: 'public_commentary',
      data: { message: '我先检查参数结构，再改。' },
    } as unknown as AssistantStreamEvent)
    expect(step?.message).toBe('💬 我先检查参数结构，再改。')
  })

  it('canonical preparing/verification/tool_finished 与复盘同形状', () => {
    const preparing = eventToThinkingStep({
      type: 'preparing',
      data: { stage: 'understand', message: '🤔 正在理解你的修改意图…' },
    } as unknown as AssistantStreamEvent)
    expect(preparing?.message).toContain('理解')
    const verify = eventToThinkingStep({
      type: 'verification',
      data: { state: 'succeeded', message: '编译通过' },
    } as unknown as AssistantStreamEvent)
    expect(verify?.ok).toBe(true)
    const finish = eventToThinkingStep({
      type: 'tool_finished',
      data: { tool_name: 'update_script', state: 'succeeded', summary: '已更新' },
    } as unknown as AssistantStreamEvent)
    expect(finish?.ok).toBe(true)
    expect(finish?.detail).toBe('已更新')
  })
})
