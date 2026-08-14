import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { AiSettingsPanel } from './AiSettingsPanel'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

vi.mock('../../api/client', () => ({
  fetchCodexStatus: vi.fn(),
  codexLoginStart: vi.fn(),
  codexLoginCancel: vi.fn(),
  codexLoginDeviceCode: vi.fn(),
  codexRestart: vi.fn(),
  codexLogout: vi.fn(),
  fetchCodexModels: vi.fn(),
}))

import {
  codexLoginCancel,
  codexLoginDeviceCode,
  codexLoginStart,
  codexLogout,
  codexRestart,
  fetchCodexModels,
  fetchCodexStatus,
} from '../../api/client'

const mockedStatus = vi.mocked(fetchCodexStatus)
const mockedLogin = vi.mocked(codexLoginStart)
const mockedCancel = vi.mocked(codexLoginCancel)
const mockedDeviceCode = vi.mocked(codexLoginDeviceCode)
const mockedRestart = vi.mocked(codexRestart)
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
      model: 'deepseek-chat',
      model_available: true,
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
      model: 'deepseek-chat',
      model_available: true,
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
  test('login failure from status shows actionable hint', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_out',
      codex_available: true,
      connected: false,
      account: null,
      login_error: 'ChatGPT 登录未完成或已取消，请重试，或改用设备码登录。',
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    const el = await screen.findByTestId('codex-login-error')
    expect(el.textContent ?? '').toMatch(/设备码/)
  })

  // ── D2：取消 / 设备码 / 崩溃重启 / 额度 ─────────────────────────────────

  test('cancel pending login returns to signed out', async () => {
    mockedLogin.mockResolvedValue({ ok: true, state: 'login_started', method: 'chatgpt' })
    mockedCancel.mockResolvedValue({ ok: true, state: 'signed_out' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByTestId('codex-login-button'))
    expect(await screen.findByTestId('codex-login-pending')).toBeTruthy()
    fireEvent.click(screen.getByTestId('codex-login-cancel'))
    await waitFor(() => expect(mockedCancel).toHaveBeenCalledTimes(1))
    // 取消后回到 signed_out：登录按钮重新出现
    expect(await screen.findByTestId('codex-login-button')).toBeTruthy()
  })

  test('device code login is explicit and shows verification info', async () => {
    mockedDeviceCode.mockResolvedValue({
      ok: true,
      state: 'login_started',
      method: 'chatgptDeviceCode',
      verification_url: 'https://example.test/device',
      user_code: 'ABCD-EFGH',
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    // 设备码按钮是显式选择：点「连接我的 ChatGPT」不会静默切到设备码
    const deviceButton = await screen.findByTestId('codex-device-code-button')
    fireEvent.click(deviceButton)
    await waitFor(() => expect(mockedDeviceCode).toHaveBeenCalledTimes(1))
    expect(mockedLogin).not.toHaveBeenCalled()
    // 展示验证网址与设备码（完成授权所必需；loginId 不外传）
    expect(await screen.findByText('ABCD-EFGH')).toBeTruthy()
    expect(screen.getByText('https://example.test/device')).toBeTruthy()
    const panel = screen.getByTestId('codex-section')
    expect(panel.textContent ?? '').not.toMatch(/loginId|authUrl|token|jwt/i)
  })

  test('crashed state shows restart button and restart recovers', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'crashed',
      codex_available: true,
      connected: false,
      account: null,
      restartable: true,
      error: 'Codex app-server 进程异常退出。点击「重启」恢复连接。',
    })
    mockedRestart.mockResolvedValue({ ok: true, state: 'signed_out' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    expect(await screen.findByTestId('codex-crashed')).toBeTruthy()
    fireEvent.click(screen.getByTestId('codex-restart-button'))
    await waitFor(() => expect(mockedRestart).toHaveBeenCalledTimes(1))
    // 重启后恢复 signed_out：登录按钮出现
    expect(await screen.findByTestId('codex-login-button')).toBeTruthy()
  })

  test('version incompatible shows upgrade guidance without login', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'version_incompatible',
      codex_available: true,
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

    expect(await screen.findByTestId('codex-version-incompatible')).toBeTruthy()
    expect(screen.queryByTestId('codex-login-button')).toBeNull()
  })

  test('quota exhausted shows actionable message with masked usage', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'quota_exhausted',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      rate_limits: {
        reached: true,
        reached_type: 'rate_limit_reached',
        used_percent: 100,
        plan_type: 'pro',
        credits: { has_credits: false, unlimited: false },
      },
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ model: 'openai-codex/gpt-5.6-luna' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    expect(await screen.findByTestId('codex-quota-exhausted')).toBeTruthy()
    // 脱敏用量摘要：只有百分比/触顶，没有余额等内部字段
    expect(await screen.findByTestId('codex-rate-limits')).toBeTruthy()
    const panel = screen.getByTestId('codex-section')
    const text = panel.textContent ?? ''
    expect(text).not.toMatch(/balance|123\.45|resetCredit/i)
  })

  test('rate limits masked summary shown when signed in', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      rate_limits: {
        reached: false,
        used_percent: 12,
        plan_type: 'pro',
        credits: { has_credits: true, unlimited: false },
      },
    })
    mockedModels.mockResolvedValue({ ok: true, models: [] })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    expect(await screen.findByTestId('codex-rate-limits')).toBeTruthy()
    expect(screen.getByText(/12%/)).toBeTruthy()
    const text = screen.getByTestId('codex-rate-limits').textContent ?? ''
    expect(text).not.toMatch(/balance|123\.45|limitId|grantedAt/i)
  })
})

  test('current codex model missing from account catalog shows unavailable (P0-4)', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-terra',
      model_available: false,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [{ id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' }],
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ model: 'openai-codex/gpt-5.6-terra' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    // 已登录但当前模型不在账户目录 → 模型不可用提示出现
    expect(await screen.findByText(/当前 Codex 模型不可用/)).toBeTruthy()
  })
