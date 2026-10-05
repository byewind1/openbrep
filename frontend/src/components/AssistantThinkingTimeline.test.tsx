import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AssistantThinkingTimeline } from './AssistantThinkingTimeline'
import { closeRunningSteps, pushThinkingStep } from '../state/actions/assistantActions'
import type { AssistantThinkingStep } from '../api/types'

/**
 * 卡01（任务反馈与连续对话修复）：时间线复盘契约冻结——预期红灯回归。
 *
 * 原始事故：时间线只渲染 slice(-12)，“…还有 N 步”按钮只把可见 12 步标记为
 * 展开，早期步骤永远无法查看 → 超过 12 步的执行过程不可复盘。
 *
 * 卡05 已实现“真正的查看全部”：it.fails 预期红灯已转为正式测试。
 */

function toolStep(index: number): AssistantThinkingStep {
  return {
    type: 'tool_call',
    stage: 'think',
    message: `tool_${index}`,
    detail: `detail-${index}`,
    ok: true,
  }
}

describe('AssistantThinkingTimeline 查看全部（卡01 冻结 → 卡05 转绿）', () => {
  it('超过 12 步时“查看全部”展开全部早期步骤', () => {
    const steps = Array.from({ length: 18 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    expect(screen.getByText(/还有 6 步/)).toBeTruthy()
    fireEvent.click(screen.getByText(/还有 6 步/))
    // 全部 18 步可见，包括最早的 tool_0（今天 slice(-12) 只显示 tool_6..tool_17）
    expect(screen.getByText('tool_0')).toBeTruthy()
    expect(screen.getByText('tool_5')).toBeTruthy()
    expect(screen.getByText('tool_17')).toBeTruthy()
  })

  it('展开后可收起回最近 12 步', () => {
    const steps = Array.from({ length: 18 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    fireEvent.click(screen.getByText(/还有 6 步/))
    expect(screen.getByText('tool_0')).toBeTruthy()
    fireEvent.click(screen.getByText(/收起，只看最近 12 步/))
    expect(screen.queryByText('tool_0')).toBeNull()
    expect(screen.getByText('tool_6')).toBeTruthy()
  })

  it('旧记录未保存执行过程时显示明确提示，不编造历史', () => {
    render(<AssistantThinkingTimeline steps={[]} stale />)
    expect(screen.getByText('旧记录未保存执行过程')).toBeTruthy()
  })

  it('默认只显示最近 12 步（已有行为冻结）', () => {
    const steps = Array.from({ length: 18 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    expect(screen.queryByText('tool_0')).toBeNull()
    expect(screen.getByText('tool_6')).toBeTruthy()
    expect(screen.getByText('tool_17')).toBeTruthy()
  })
})

// ── RF05：等待计时按最后有效进展，工具步骤真实收束 ──────────────

const NOW = Date.now()

function atStep(secondsAgo: number, overrides: Partial<AssistantThinkingStep> = {}): AssistantThinkingStep {
  return { type: 'status', stage: 'think', message: '事件', at: NOW - secondsAgo * 1000, ...overrides }
}

describe('RF05 等待计时（按最后有效进展）', () => {
  it('连续真实事件未超15秒不误报静默（总耗时超过15秒也照常推进）', () => {
    const steps = [atStep(30), atStep(3, { message: '最近事件' })]
    render(<AssistantThinkingTimeline steps={steps} busy startedAt={NOW - 30_000} />)
    expect(screen.queryByText(/等待模型响应/)).toBeNull()
    expect(screen.queryByText(/工具仍在运行/)).toBeNull()
  })

  it('最后事件超过15秒提示等待模型响应', () => {
    const steps = [atStep(30), atStep(20, { message: '最近事件' })]
    render(<AssistantThinkingTimeline steps={steps} busy startedAt={NOW - 40_000} />)
    expect(screen.getByText(/等待模型响应/)).toBeTruthy()
  })

  it('最后事件是未返回工具时提示工具仍在运行（不是等模型）', () => {
    const steps = [
      atStep(30, { type: 'tool_call', message: 'update_script', toolCallId: 'c1' }),
      atStep(20, { type: 'tool_call', message: 'compile_script', toolCallId: 'c2' }),
    ]
    render(<AssistantThinkingTimeline steps={steps} busy startedAt={NOW - 40_000} />)
    expect(screen.getByText(/工具仍在运行/)).toBeTruthy()
  })

  it('非 busy（取消/完成）不显示等待计时', () => {
    const steps = [atStep(60, { type: 'tool_call', message: 'update_script', toolCallId: 'c1' })]
    render(<AssistantThinkingTimeline steps={steps} startedAt={NOW - 70_000} />)
    expect(screen.queryByText(/等待模型响应/)).toBeNull()
    expect(screen.queryByText(/工具仍在运行/)).toBeNull()
  })
})

describe('RF05 工具步骤收束（start/finish 同一行）', () => {
  it('pushThinkingStep：同 toolCallId 的 start+finish 收束为同一行并带耗时', () => {
    const steps: AssistantThinkingStep[] = []
    pushThinkingStep(steps, { type: 'tool_call', message: 'update_script', toolCallId: 'c1' })
    pushThinkingStep(steps, { type: 'tool_call', message: 'update_script', toolCallId: 'c1', ok: true, detail: '已更新', durationMs: 120 })
    expect(steps).toHaveLength(1)
    expect(steps[0].ok).toBe(true)
    expect(steps[0].detail).toBe('已更新')
    expect(steps[0].durationMs).toBe(120)
  })

  it('多次同名工具不混淆（按 tool_call_id 区分）', () => {
    const steps: AssistantThinkingStep[] = []
    pushThinkingStep(steps, { type: 'tool_call', message: 'compile_script', toolCallId: 'a1' })
    pushThinkingStep(steps, { type: 'tool_call', message: 'compile_script', toolCallId: 'a2' })
    pushThinkingStep(steps, { type: 'tool_call', message: 'compile_script', toolCallId: 'a2', ok: false, detail: '失败' })
    expect(steps).toHaveLength(2)
    expect(steps[0].ok).toBeUndefined()
    expect(steps[1].ok).toBe(false)
  })

  it('closeRunningSteps：取消后无旋转假进度', () => {
    const steps: AssistantThinkingStep[] = [
      { type: 'tool_call', message: 'update_script', toolCallId: 'c1', ok: true },
      { type: 'tool_call', message: 'compile_script', toolCallId: 'c2' },
    ]
    const closed = closeRunningSteps(steps)
    expect(closed[1].ok).toBe(false)
    expect(closed[1].message).toContain('未完成')
  })

  it('taskEventsToThinkingSteps 恢复路径：终止时未完成工具不再显示运行中', async () => {
    const { taskEventsToThinkingSteps } = await import('../state/actions/assistantActions')
    const steps = taskEventsToThinkingSteps([
      { seq: 1, event_id: 'e1', timestamp: '', kind: 'tool_started', tool_name: 'update_script', tool_call_id: 'c1' },
      { seq: 2, event_id: 'e2', timestamp: '', kind: 'tool_started', tool_name: 'compile_script', tool_call_id: 'c2' },
      { seq: 3, event_id: 'e3', timestamp: '', kind: 'tool_finished', tool_name: 'update_script', tool_call_id: 'c1', state: 'succeeded' },
    ] as never)
    const compile = steps.find((s) => s.message.startsWith('compile_script'))
    expect(compile?.ok).toBe(false)
    expect(compile?.message).toContain('未完成')
    const update = steps.find((s) => s.message === 'update_script')
    expect(update?.ok).toBe(true)
  })
})

describe('RF05 长记录分页可达', () => {
  it('130 步：分页加载可达全部，最终可收起', () => {
    const steps = Array.from({ length: 130 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    expect(screen.queryByText('tool_0')).toBeNull()
    // 默认最近 12 条（tool_118..129）；每页向更早翻 50 条
    fireEvent.click(screen.getByText(/还有 118 步/))
    expect(screen.getByText('tool_68')).toBeTruthy()
    expect(screen.queryByText('tool_67')).toBeNull()
    fireEvent.click(screen.getByText(/还有 68 步/))
    expect(screen.getByText('tool_18')).toBeTruthy()
    fireEvent.click(screen.getByText(/还有 18 步/))
    expect(screen.getByText('tool_0')).toBeTruthy()
    expect(screen.getByText('tool_129')).toBeTruthy()
    fireEvent.click(screen.getByText(/收起/))
    expect(screen.queryByText('tool_0')).toBeNull()
    expect(screen.getByText('tool_118')).toBeTruthy()
  })
})
