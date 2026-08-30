import { describe, expect, test } from 'vitest'
import type { AssistantMessage } from '../api/types'
import { buildAssistantHistory } from './actions/assistantActions'

/** HF4：store assistantMessages → 后端 history 载荷的组装规则。 */
function msg(role: 'user' | 'assistant', content: string, extras: Partial<AssistantMessage> = {}): AssistantMessage {
  return { role, content, ...extras }
}

describe('buildAssistantHistory (HF4)', () => {
  test('maps to {role, content} and strips card-only fields', () => {
    const messages = [
      msg('user', '加一趟漏窗', {
        images: [{ name: 'p1.png', b64: 'aGk=', mime: 'image/png' }],
      }),
      msg('assistant', '已增加。', {
        changedFiles: ['scripts/3d.gdl'],
        verification: { passed: true, checks: [] } as unknown as AssistantMessage['verification'],
        acceptance: { summary_lines: [], geometry_delta: { status: 'ok' }, checks: [] },
        visionExtractions: [{ token: '图1', schema_name: 'lattice_window' }],
        thinkingSteps: [],
        images: [{ name: 'p1.png', b64: 'aGk=', mime: 'image/png' }],
      }),
    ]
    expect(buildAssistantHistory(messages)).toEqual([
      { role: 'user', content: '加一趟漏窗' },
      { role: 'assistant', content: '已增加。' },
    ])
  })

  test('skips pending placeholders (Thinking... prefix)', () => {
    const messages = [
      msg('assistant', 'Thinking...\n- Inspecting the loaded HSF project.'),
      msg('assistant', 'Thinking...\n📝 修改计划已生成，请确认后执行。'),
      msg('assistant', '真实回答'),
    ]
    expect(buildAssistantHistory(messages)).toEqual([{ role: 'assistant', content: '真实回答' }])
  })

  test('skips error messages and keeps interrupted assistant messages', () => {
    const messages = [
      msg('assistant', 'LLM 配置错误：API Key 缺失', { errorCategory: 'llm' }),
      msg('assistant', '编译失败：syntax error', { errorCategory: 'compile' }),
      msg('assistant', '普通错误', { errorCategory: 'general' }),
      msg('assistant', '⏹ 已中断', { interrupted: true }),
      msg('assistant', '正常答复'),
    ]
    expect(buildAssistantHistory(messages)).toEqual([
      { role: 'assistant', content: '⏹ 已中断' },
      { role: 'assistant', content: '正常答复' },
    ])
  })

  test('truncates to the most recent 24 messages (= 12 轮, matches backend trim_history_messages guard)', () => {
    const messages: AssistantMessage[] = []
    for (let i = 0; i < 26; i++) {
      messages.push({ role: 'user', content: `u${i}` }, { role: 'assistant', content: `a${i}` })
    }
    const history = buildAssistantHistory(messages)
    expect(history).toHaveLength(24)
    expect(history[0]).toEqual({ role: 'user', content: 'u14' })
    expect(history.at(-1)).toEqual({ role: 'assistant', content: 'a25' })
  })

  test('keeps a full session of exactly 24 messages intact', () => {
    const messages: AssistantMessage[] = []
    for (let i = 0; i < 12; i++) {
      messages.push({ role: 'user', content: `u${i}` }, { role: 'assistant', content: `a${i}` })
    }
    const history = buildAssistantHistory(messages)
    expect(history).toHaveLength(24)
    expect(history[0]).toEqual({ role: 'user', content: 'u0' })
    expect(history.at(-1)).toEqual({ role: 'assistant', content: 'a11' })
  })

  test('accepts a custom limit', () => {
    const messages = [
      msg('user', '1'),
      msg('assistant', '2'),
      msg('user', '3'),
      msg('assistant', '4'),
    ]
    expect(buildAssistantHistory(messages, 2)).toEqual([
      { role: 'user', content: '3' },
      { role: 'assistant', content: '4' },
    ])
  })

  test('skips empty content and unknown roles', () => {
    const messages = [
      msg('user', '   '),
      { role: 'system', content: 'sys' } as unknown as AssistantMessage,
      msg('user', '有效提问'),
    ]
    expect(buildAssistantHistory(messages)).toEqual([{ role: 'user', content: '有效提问' }])
  })

  test('keeps [图N] tokens in content (no image b64 re-send)', () => {
    const messages = [
      msg('user', '按图调整\n[图1] 冰裂纹', { images: [{ name: 'p1.png', mime: 'image/png', b64: 'aGk=' }] }),
      msg('assistant', '已按图调整。'),
    ]
    expect(buildAssistantHistory(messages)).toEqual([
      { role: 'user', content: '按图调整\n[图1] 冰裂纹' },
      { role: 'assistant', content: '已按图调整。' },
    ])
  })
})