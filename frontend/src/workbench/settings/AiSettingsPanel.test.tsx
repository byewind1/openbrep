import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AiSettingsPanel save-and-verify', () => {
  test('refreshes one incomplete cc-switch provider without selecting a model', async () => {
    const onModelChange = vi.fn().mockResolvedValue(undefined)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/entry')) {
        return new Response(JSON.stringify({
          ok: true,
          entry: 'local',
          entries: [],
          local_hint: { detected: true, state: 'ready', models: 1, home_kind: 'user_default' },
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/status')) {
        return new Response(JSON.stringify({
          ok: true,
          state: 'ready',
          connected: true,
          codex_available: true,
          account: null,
          entry: 'local',
          model_available: true,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      const refreshed = url.endsWith('/models/refresh')
      return new Response(JSON.stringify({
        ok: true,
        cc_switch_detected: true,
        providers: [{
          id: 'geili',
          name: 'Geili',
          is_current: true,
          catalog_source: refreshed ? 'runtime' : 'config_default',
          catalog_complete: refreshed,
          runnable: true,
        }],
        models: [{
          id: 'openai-codex/ccswitch/geili/gpt-5.6-sol',
          label: 'GPT-5.6 Sol',
          model: 'gpt-5.6-sol',
          source: 'cc_switch',
          provider_id: 'geili',
          provider_label: 'Geili',
          catalog_source: refreshed ? 'runtime' : 'config_default',
          catalog_complete: refreshed,
        }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'openai-codex/ccswitch/geili/gpt-5.6-sol',
          codex_entry: 'local',
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue({ ok: true })}
        onModelChange={onModelChange}
      />,
    )

    const refresh = await screen.findByRole('button', { name: '刷新 Geili 模型目录' })
    expect(screen.getByText('目录未完整同步')).toBeTruthy()
    fireEvent.click(refresh)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/settings/llm/codex/models/refresh',
      expect.objectContaining({ method: 'POST' }),
    ))
    expect(onModelChange).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('目录未完整同步')).toBeNull())
  })

  test('shows separate API and ChatGPT Codex connection cards', () => {
    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue({ ok: true })}
      />,
    )

    expect(screen.getByTestId('llm-connection-wizard')).toBeTruthy()
    expect(screen.getByTestId('openai-api-card')).toBeTruthy()
    expect(screen.getByTestId('chatgpt-codex-card')).toBeTruthy()
  })

  test('opens the Codex status area while starting ChatGPT login', async () => {
    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue({ ok: true })}
      />,
    )

    fireEvent.click(screen.getByTestId('codex-model-drawer-open'))
    await waitFor(() => expect(screen.getByTestId('codex-toggle').getAttribute('aria-expanded')).toBe('true'))
    expect(screen.queryByTestId('codex-model-drawer')).toBeNull()
  })

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

  test('custom provider model also offers the key editor (unified registry)', async () => {
    const onSaveApiKey = vi.fn().mockResolvedValue({ ok: true })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'deepseek-v4-flash',
          models: ['deepseek-v4-flash'],
          model_groups: {
            custom: [
              {
                id: 'deepseek-v4-flash',
                label: 'deepseek-v4-flash',
                kind: 'custom',
                provider: 'opencode-go',
                api_base: 'https://opencode.ai/zen/go/v1',
                has_api_key: true,
              },
            ],
            official: [],
          },
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue({ ok: true, message: 'LLM connection OK' })}
        onSaveApiKey={onSaveApiKey}
      />,
    )

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-oc' } })
    fireEvent.click(screen.getByText('保存 Key'))

    await waitFor(() => expect(onSaveApiKey).toHaveBeenCalledWith('deepseek-v4-flash', 'sk-oc'))
  })

  test('ollama model hides the key editor', () => {
    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'ollama/qwen3:8b',
          models: ['ollama/qwen3:8b'],
          model_groups: {
            custom: [],
            official: [
              { id: 'ollama/qwen3:8b', label: 'ollama/qwen3:8b', kind: 'official', provider: 'ollama', has_api_key: true },
            ],
          },
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
        onSaveApiKey={vi.fn()}
      />,
    )

    expect(screen.queryByLabelText('API Key')).toBeNull()
  })
})
