import { describe, expect, it } from 'vitest'
import { hydrateHistoryMessages, withHistoryMeta } from './assistantActions'
import type { AssistantMessage } from '../../api/types'

/**
 * 卡01（任务反馈与连续对话修复）：前端复盘契约冻结——预期红灯回归。
 *
 * 原始事故：时间线 thinkingSteps 只活在内存里，withHistoryMeta 不保存、
 * hydrateHistoryMessages 不恢复 → 重开项目后“旧记录未保存执行过程”，
 * 无法复盘失败、中断与部分修改。
 *
 * 用 it.fails 记录预期红灯：卡05 实现保存/恢复后必须把 it.fails 换成 it
 * （strict 转绿纪律与 Python 侧 xfail(strict=True) 一致）。
 */

const steps: NonNullable<AssistantMessage['thinkingSteps']> = [
  { type: 'status', stage: 'plan', message: '正在准备执行步骤…' },
  { type: 'tool_call', stage: 'think', message: 'update_script', detail: 'scripts/3d.gdl', ok: true },
  { type: 'status', stage: 'compile', message: '✅ 编译通过', ok: true },
]

describe('任务时间线历史往返（卡01 冻结 → 卡05 转绿）', () => {
  it.fails('withHistoryMeta 保存 thinking_steps 元数据', () => {
    const message: AssistantMessage = {
      role: 'assistant',
      content: '已加一层板，编译通过。',
      thinkingSteps: [...steps],
    }
    const saved = withHistoryMeta(message)
    expect(saved.meta?.thinking_steps).toEqual(steps)
  })

  it.fails('刷新后 hydrateHistoryMessages 恢复完整时间线步骤', () => {
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
