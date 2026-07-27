import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { AiSettingsPanel } from './AiSettingsPanel'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

function makeSettings(overrides: Partial<LlmSettings> = {}): LlmSettings {
  return {
    model: 'deepseek-chat',
    model_available: true,
    models: ['deepseek-chat'],
    api_key: '***',
    api_base: '',
    max_retries: 5,
    assistant_settings: '',
    model_groups: { custom: [], official: [] },
    ...overrides,
  }
}

describe('AiSettingsPanel save-and-verify', () => {
  test('saving a bad key still saves, then surfaces the connection failure', async () => {
    const onSaveApiKey = vi.fn().mockResolvedValue({ ok: true })
    const failed: LlmConnectionTestResult = {
      ok: false,
      error: 'LLM API Key 无效或被拒绝：认证未通过',
      detail: 'RuntimeError: LLM API Key 无效或被拒绝：认证未通过',
      duration_ms: 120,
    }
    const onTestConnection = vi.fn().mockResolvedValue(failed)

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={onTestConnection}
        onSaveApiKey={onSaveApiKey}
      />,
    )

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-wrong' } })
    fireEvent.click(screen.getByText('保存 Key'))

    // key 始终保存；随后自动验证并当场告知失败（含可复制的错误详情）
    await waitFor(() => expect(onSaveApiKey).toHaveBeenCalledWith('deepseek-chat', 'sk-wrong'))
    await waitFor(() => expect(onTestConnection).toHaveBeenCalled())
    expect(await screen.findByText(/Key 已保存，但连接失败/)).toBeTruthy()
    expect(screen.getByTestId('llm-test-error')).toBeTruthy()
  })

  test('saving a good key shows connection OK with duration', async () => {
    const onSaveApiKey = vi.fn().mockResolvedValue({ ok: true })
    const ok: LlmConnectionTestResult = { ok: true, message: 'LLM connection OK', duration_ms: 88 }

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue(ok)}
        onSaveApiKey={onSaveApiKey}
      />,
    )

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-good' } })
    fireEvent.click(screen.getByText('保存 Key'))

    expect(await screen.findByText(/连接正常 \(88 ms\)/)).toBeTruthy()
  })
})
