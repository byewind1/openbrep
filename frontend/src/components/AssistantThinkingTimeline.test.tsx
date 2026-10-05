import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AssistantThinkingTimeline } from './AssistantThinkingTimeline'
import type { AssistantThinkingStep } from '../api/types'

/**
 * 卡01（任务反馈与连续对话修复）：时间线复盘契约冻结——预期红灯回归。
 *
 * 原始事故：时间线只渲染 slice(-12)，“…还有 N 步”按钮只把可见 12 步标记为
 * 展开，早期步骤永远无法查看 → 超过 12 步的执行过程不可复盘。
 *
 * 用 it.fails 记录预期红灯：卡05 实现“真正的查看全部”后必须换成 it。
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
  it.fails('超过 12 步时“查看全部”展开全部早期步骤', () => {
    const steps = Array.from({ length: 18 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    expect(screen.getByText(/还有 6 步/)).toBeTruthy()
    fireEvent.click(screen.getByText(/还有 6 步/))
    // 全部 18 步可见，包括最早的 tool_0（今天 slice(-12) 只显示 tool_6..tool_17）
    expect(screen.getByText('tool_0')).toBeTruthy()
    expect(screen.getByText('tool_5')).toBeTruthy()
    expect(screen.getByText('tool_17')).toBeTruthy()
  })

  it('默认只显示最近 12 步（已有行为冻结）', () => {
    const steps = Array.from({ length: 18 }, (_, i) => toolStep(i))
    render(<AssistantThinkingTimeline steps={steps} />)
    expect(screen.queryByText('tool_0')).toBeNull()
    expect(screen.getByText('tool_6')).toBeTruthy()
    expect(screen.getByText('tool_17')).toBeTruthy()
  })
})
