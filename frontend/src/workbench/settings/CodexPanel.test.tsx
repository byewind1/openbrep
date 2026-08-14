import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { AiSettingsPanel } from './AiSettingsPanel'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

vi.mock('../../api/client', () => ({
  fetchCodexStatus: vi.fn(),
  codexLoginStart: vi.fn(),
  codexLogout: vi.fn(),
  fetchCodexModels: vi.fn(),
}))

import { codexLoginStart, codexLogout, fetchCodexModels, fetchCodexStatus } from '../../api/client'

const mockedStatus = vi.mocked(fetchCodexStatus)
const mockedLogin = vi.mocked(codexLoginStart)
const mockedLogout = vi.mocked(codexLogout)
const mockedModels = vi.mocked(fetchCodexModels)

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

describe('AiSettingsPanel Codex BYOA section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_out',
      codex_available: true,
      connected: false,
      account: null,
    })
    mockedModels.mockResolvedValue({ ok: true, models: [] })
  })

  test('signed out shows the login button and starts the browser flow only', async () => {
    mockedLogin.mockResolvedValue({ ok: true, state: 'login_started' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    // 登录按钮只触发终端用户 browser flow；不显示任何 token/URL
    const button = await screen.findByTestId('codex-login-button')
    fireEvent.click(button)
    await waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('codex-login-pending')).toBeTruthy()
    const panel = screen.getByTestId('codex-section')
    expect(panel.textContent ?? '').not.toMatch(/authUrl|loginId|token|jwt/i)
  })

  test('signed in shows masked account, models and explicit save confirm', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [
        { id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' },
        { id: 'openai-codex/gpt-5.6-terra', label: 'GPT-5.6 Terra', model: 'gpt-5.6-terra' },
      ],
    })
    const onModelChange = vi.fn().mockResolvedValue(undefined)

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
        onModelChange={onModelChange}
      />,
    )

    expect(await screen.findByText(/jo\*\*\*@example\.com/)).toBeTruthy()
    // 模型来自 model/list（动态），id 是 provider-qualified 的 openai-codex/<model>
    fireEvent.click(await screen.findByText('GPT-5.6 Luna'))
    expect(await screen.findByTestId('codex-model-confirm')).toBeTruthy()
    // 显式确认（Save）后才持久化——不能点击模型即自动保存
    expect(onModelChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('确认切换'))
    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna'))
  })

  test('no CLI state shows install guidance and no login button', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'no_cli',
      codex_available: false,
      connected: false,
      account: null,
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    expect(await screen.findByTestId('codex-no-cli')).toBeTruthy()
    expect(screen.queryByTestId('codex-login-button')).toBeNull()
  })

  test('current codex model is unavailable while signed out (fail closed)', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_out',
      codex_available: true,
      connected: false,
      account: null,
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ model: 'openai-codex/gpt-5.6-luna' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    expect(await screen.findByText(/当前 Codex 模型不可用/)).toBeTruthy()
    // 未登录不显示 API Key 编辑器
    expect(screen.queryByLabelText('API Key')).toBeNull()
  })

  test('logout clears models and returns to signed out', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [{ id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' }],
    })
    mockedLogout.mockResolvedValue({ ok: true, state: 'signed_out' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByText('断开连接'))
    await waitFor(() => expect(mockedLogout).toHaveBeenCalledTimes(1))
    // 退出后模型列表消失（fail closed，不 fallback）
    await waitFor(() => expect(screen.queryByText('GPT-5.6 Luna')).toBeNull())
    expect(await screen.findByTestId('codex-login-button')).toBeTruthy()
  })
})
