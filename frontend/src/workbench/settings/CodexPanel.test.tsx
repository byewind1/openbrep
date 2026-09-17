import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { AiSettingsPanel } from './AiSettingsPanel'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

vi.mock('../../api/client', () => ({
  fetchCodexStatus: vi.fn(),
  fetchCodexEntry: vi.fn(),
  saveCodexEntry: vi.fn(),
  codexLoginStart: vi.fn(),
  codexLoginCancel: vi.fn(),
  codexLoginDeviceCode: vi.fn(),
  codexRestart: vi.fn(),
  codexLogout: vi.fn(),
  fetchCodexModels: vi.fn(),
}))

import {
  fetchCodexEntry,
  codexLoginCancel,
  codexLoginDeviceCode,
  codexLoginStart,
  codexLogout,
  codexRestart,
  fetchCodexModels,
  fetchCodexStatus,
  saveCodexEntry,
} from '../../api/client'

const mockedStatus = vi.mocked(fetchCodexStatus)
const mockedEntry = vi.mocked(fetchCodexEntry)
const mockedSaveEntry = vi.mocked(saveCodexEntry)
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
    mockedEntry.mockResolvedValue({
      ok: true,
      entry: 'managed',
      entries: [
        { entry: 'local', label: 'Codex 本机配置', auth_source: 'codex_config', recommended: true },
        {
          entry: 'managed',
          label: 'ChatGPT 账户登录（OpenBrep 托管）',
          auth_source: 'openbrep_managed',
          recommended: false,
        },
      ],
      local_hint: { detected: false, state: 'unconfigured', models: 0, home_kind: 'user_default' },
    })
    mockedSaveEntry.mockResolvedValue({ ok: true, entry: 'local' })
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
    fireEvent.click(await screen.findByTestId('codex-toggle'))
    const button = await screen.findByTestId('codex-login-button')
    fireEvent.click(button)
    await waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('codex-login-pending')).toBeTruthy()
    const panel = screen.getByTestId('codex-section')
    expect(panel.textContent ?? '').not.toMatch(/authUrl|loginId|token|jwt/i)
  })

  test('top connection card starts ChatGPT login instead of only expanding settings', async () => {
    mockedLogin.mockResolvedValue({ ok: true, state: 'login_started' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByTestId('codex-model-drawer-open'))
    await waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('codex-login-pending')).toBeTruthy()
    expect(screen.getByTestId('codex-model-drawer-open').textContent).toMatch(/登录中/)
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

  test('drawer keeps model, effort, and real connection verification in one flow', async () => {
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
      models: [{
        id: 'openai-codex/gpt-5.6-luna',
        label: 'GPT-5.6 Luna',
        model: 'gpt-5.6-luna',
        supported_reasoning_efforts: [{ effort: 'high', description: 'Deep' }],
      }],
    })
    const onModelChange = vi.fn().mockResolvedValue(undefined)
    const onTestConnection = vi.fn().mockResolvedValue({ ok: true, message: 'LLM connection OK', duration_ms: 42 })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={onTestConnection}
        onModelChange={onModelChange}
      />,
    )

    await screen.findByText(/jo\*\*\*@example\.com/)
    fireEvent.click(screen.getByTestId('codex-model-drawer-open'))
    const drawer = await screen.findByTestId('codex-model-drawer')
    fireEvent.click(within(drawer).getByRole('option', { name: /GPT-5.6 Luna/ }))

    const confirm = within(drawer).getByTestId('codex-drawer-confirm')
    fireEvent.change(within(confirm).getByLabelText('推理强度（reasoning effort）'), { target: { value: 'high' } })
    fireEvent.click(within(confirm).getByRole('button', { name: '连接并验证' }))

    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna', 'high'))
    await waitFor(() => expect(onTestConnection).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna', 'high'))
    expect(await screen.findByText(/Codex 已连接/)).toBeTruthy()
    expect(screen.queryByTestId('codex-model-drawer')).toBeNull()
  })

  test('failed Codex turn verification does not save the selected model', async () => {
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
    const onModelChange = vi.fn().mockResolvedValue(undefined)
    const onTestConnection = vi.fn().mockResolvedValue({ ok: false, error: 'Codex turn failed' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={onTestConnection}
        onModelChange={onModelChange}
      />,
    )

    await screen.findByText(/jo\*\*\*@example\.com/)
    fireEvent.click(screen.getByTestId('codex-model-drawer-open'))
    const drawer = await screen.findByTestId('codex-model-drawer')
    fireEvent.click(within(drawer).getByRole('option', { name: /GPT-5.6 Luna/ }))
    fireEvent.click(within(drawer).getByRole('button', { name: '连接并验证' }))

    expect(await within(drawer).findByText('Codex turn failed')).toBeTruthy()
    expect(onModelChange).not.toHaveBeenCalled()
    expect(screen.getByTestId('codex-model-drawer')).toBeTruthy()
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

    fireEvent.click(await screen.findByTestId('codex-toggle'))
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

    fireEvent.click(await screen.findByTestId('codex-toggle'))
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

    fireEvent.click(await screen.findByTestId('codex-toggle'))
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
    fireEvent.click(await screen.findByTestId('codex-toggle'))
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

    fireEvent.click(await screen.findByTestId('codex-toggle'))
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

  test('D6: current codex model shows effort options from catalog and explicit save', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-luna',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [
        {
          id: 'openai-codex/gpt-5.6-luna',
          label: 'GPT-5.6 Luna',
          model: 'gpt-5.6-luna',
          supported_reasoning_efforts: [
            { effort: 'low', description: 'Fastest' },
            { effort: 'medium', description: 'Balanced' },
            { effort: 'high', description: 'Deep' },
          ],
          default_reasoning_effort: 'medium',
        },
        {
          id: 'openai-codex/gpt-5.6-terra',
          label: 'GPT-5.6 Terra',
          model: 'gpt-5.6-terra',
          supported_reasoning_efforts: [
            { effort: 'medium', description: 'Balanced' },
            { effort: 'high', description: 'Deep' },
          ],
          default_reasoning_effort: 'high',
        },
      ],
    })
    const onModelChange = vi.fn().mockResolvedValue(undefined)

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'openai-codex/gpt-5.6-luna',
          reasoning_effort: 'medium',
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
        onModelChange={onModelChange}
      />,
    )

    // effort 选项来自 model/list（supported_reasoning_efforts），不硬编码
    const effortRow = await screen.findByTestId('codex-effort-row')
    const select = effortRow.querySelector('select') as HTMLSelectElement
    expect(select).toBeTruthy()
    expect(select.value).toBe('medium')
    // 切换 draft 后不自动保存（draft + 显式 Save）
    fireEvent.change(select, { target: { value: 'high' } })
    expect(onModelChange).not.toHaveBeenCalled()
    // 显式保存才调用后端（model + effort 一起）
    fireEvent.click(screen.getByTestId('codex-effort-save'))
    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna', 'high'))
    expect(await screen.findByTestId('codex-effort-feedback')).toBeTruthy()
  })

  test('D6: model switch confirm lets user pick effort; backend rejects unsupported combo (no silent replace)', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-luna',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [
        {
          id: 'openai-codex/gpt-5.6-luna',
          label: 'GPT-5.6 Luna',
          model: 'gpt-5.6-luna',
          supported_reasoning_efforts: [
            { effort: 'low', description: 'Fastest' },
            { effort: 'medium', description: 'Balanced' },
            { effort: 'high', description: 'Deep' },
          ],
        },
        {
          id: 'openai-codex/gpt-5.6-terra',
          label: 'GPT-5.6 Terra',
          model: 'gpt-5.6-terra',
          supported_reasoning_efforts: [
            { effort: 'medium', description: 'Balanced' },
            { effort: 'high', description: 'Deep' },
          ],
        },
      ],
    })
    const onModelChange = vi.fn().mockResolvedValue(undefined)

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'openai-codex/gpt-5.6-luna',
          reasoning_effort: 'high',
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
        onModelChange={onModelChange}
      />,
    )

    // 切到 terra（不支持 high）：确认面板出现 terra 的 effort 选项
    fireEvent.click(await screen.findByText('GPT-5.6 Terra'))
    const confirm = await screen.findByTestId('codex-model-confirm')
    const select = confirm.querySelector('select') as HTMLSelectElement
    expect(select).toBeTruthy()
    // 默认 draft 为空（不静默继承旧 effort）；用户选 medium
    fireEvent.change(select, { target: { value: 'medium' } })
    fireEvent.click(screen.getByText('确认切换'))
    // model + 用户显式选择的 effort 一起保存
    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith('openai-codex/gpt-5.6-terra', 'medium'))
    // UI 不展示任何 raw chain-of-thought（无 reasoning 字段渲染）
    const panel = screen.getByTestId('codex-section').textContent ?? ''
    expect(panel).not.toMatch(/chain-of-thought|reasoning_text|commentary/i)
  })

  test('D6: save effort failure surfaces backend error and keeps draft (no silent replace)', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-luna',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [
        {
          id: 'openai-codex/gpt-5.6-luna',
          label: 'GPT-5.6 Luna',
          model: 'gpt-5.6-luna',
          supported_reasoning_efforts: [
            { effort: 'low', description: 'Fastest' },
            { effort: 'medium', description: 'Balanced' },
            { effort: 'high', description: 'Deep' },
          ],
        },
      ],
    })
    const onModelChange = vi
      .fn()
      .mockRejectedValue(new Error('模型不支持 reasoning effort「high」。请重新选择后再保存。'))

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'openai-codex/gpt-5.6-luna',
          reasoning_effort: 'low',
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
        onModelChange={onModelChange}
      />,
    )

    const effortRow = await screen.findByTestId('codex-effort-row')
    const select = effortRow.querySelector('select') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'high' } })
    fireEvent.click(screen.getByTestId('codex-effort-save'))
    // 后端拒绝 → 错误反馈展示，draft 不被静默替换
    await waitFor(() => expect(screen.getByTestId('codex-effort-feedback').textContent ?? '').toMatch(/不支持/))
    expect(onModelChange).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna', 'high')
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

describe('D10 Codex MODIFY capability note', () => {
  test('settings page explains experimental MODIFY boundary', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-luna',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [{ id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' }],
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ model: 'openai-codex/gpt-5.6-luna' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    // 设置页说明 experimental 边界，并明确依赖本机 CLI 与 ChatGPT 账号
    expect(await screen.findByTestId('codex-modify-note')).toBeTruthy()
    expect((await screen.findByTestId('codex-modify-note')).textContent).toMatch(/MODIFY/)
    expect((await screen.findByTestId('codex-modify-note')).textContent).toMatch(/观察期|experimental/i)
    expect((await screen.findByTestId('codex-modify-note')).textContent).toMatch(/CLI|ChatGPT/)
  })
})

describe('D9 Codex Auto routing opt-in', () => {
  test('defaults to Fixed and writes only after explicit Save', async () => {
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'signed_in',
      codex_available: true,
      connected: true,
      account: { email_masked: 'jo***@example.com', plan_type: 'pro' },
      model: 'openai-codex/gpt-5.6-luna',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [{
        id: 'openai-codex/gpt-5.6-luna',
        label: 'GPT-5.6 Luna',
        model: 'gpt-5.6-luna',
        supported_reasoning_efforts: [{ effort: 'low' }, { effort: 'high' }],
      }],
    })
    const onModelChange = vi.fn().mockResolvedValue(undefined)

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({
          model: 'openai-codex/gpt-5.6-luna',
          reasoning_effort: 'low',
        })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn().mockResolvedValue({ ok: true } as LlmConnectionTestResult)}
        onModelChange={onModelChange}
      />,
    )

    const row = await screen.findByTestId('codex-routing-mode-row')
    const select = row.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('fixed')
    expect((screen.getByTestId('codex-routing-mode-save') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(select, { target: { value: 'auto' } })
    expect(onModelChange).not.toHaveBeenCalled()
    expect(screen.getByTestId('codex-routing-mode-hint').textContent).toMatch(/D8/)
    fireEvent.click(screen.getByTestId('codex-routing-mode-save'))
    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith(
      'openai-codex/gpt-5.6-luna',
      'low',
      'auto',
    ))
  })
})

describe('双入口 Codex 链路（2026-09-17）', () => {
  test('默认托管入口：展示入口选择、当前链路与来源，切换前不写盘', async () => {
    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    const select = (await screen.findByTestId('codex-entry-select')) as HTMLSelectElement
    expect(select.value).toBe('managed')
    expect(screen.getByTestId('codex-entry-active').textContent).toMatch(/OpenBrep 托管/)
    expect((screen.getByTestId('codex-entry-save') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(select, { target: { value: 'local' } })
    // 改 draft 绝不隐式写配置（设置页统一 draft + 显式保存）
    expect(mockedSaveEntry).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('codex-entry-save'))
    await waitFor(() => expect(mockedSaveEntry).toHaveBeenCalledWith('local'))
    expect(await screen.findByTestId('codex-entry-feedback')).toBeTruthy()
  })

  test('本机配置入口已就绪：状态卡显示 home 来源与认证来源，不显示 OpenBrep 登录按钮', async () => {
    mockedEntry.mockResolvedValue({
      ok: true,
      entry: 'managed',
      entries: [],
      local_hint: { detected: true, state: 'ready', models: 2, home_kind: 'user_default' },
    })
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'ready',
      codex_available: true,
      connected: true,
      account: null,
      entry: 'local',
      entry_label: 'Codex 本机配置',
      codex_home_kind: 'user_default',
      auth_source: 'codex_config',
      models_source: 'model_catalog_json',
      provider: 'deepseek',
      model: 'openai-codex/deepseek-v4-flash',
      model_available: true,
    })
    mockedModels.mockResolvedValue({
      ok: true,
      models: [
        {
          id: 'openai-codex/deepseek-v4-flash',
          label: 'DeepSeek V4 Flash',
          model: 'deepseek-v4-flash',
          source: 'codex_config',
        },
      ],
    })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ model: 'openai-codex/deepseek-v4-flash', codex_entry: 'local' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    const active = await screen.findByTestId('codex-entry-active')
    expect(active.textContent).toMatch(/本机配置/)
    expect(active.textContent).toMatch(/本机默认位置/)
    expect(active.textContent).toMatch(/认证：你的 Codex 配置/)
    expect(active.textContent).toMatch(/deepseek/)
    // 本机入口不接管认证：没有 OpenBrep 的登录按钮
    expect(screen.queryByTestId('codex-login-button')).toBeNull()
    // 模型来源在列表标题与抽屉里都标注清楚
    expect(screen.getByTestId('codex-models-label').textContent).toMatch(/Codex 配置/)
    fireEvent.click(screen.getByTestId('codex-model-drawer-open'))
    expect((await screen.findByTestId('codex-drawer-source')).textContent).toMatch(/来自 Codex 配置/)
  })

  test('本机配置缺失：三态提示可操作，并提供切换到托管登录的显式入口', async () => {
    mockedEntry.mockResolvedValue({
      ok: true,
      entry: 'local',
      entries: [],
      local_hint: { detected: false, state: 'unconfigured', models: 0, home_kind: 'user_default' },
    })
    mockedStatus.mockResolvedValue({
      ok: true,
      state: 'unconfigured',
      codex_available: true,
      connected: false,
      account: null,
      entry: 'local',
      codex_home_kind: 'user_default',
      auth_source: 'codex_config',
      error: '未检测到本机 Codex 配置。请先在终端运行 codex login 完成 Codex CLI 初始化，再回到这里刷新。',
    })
    mockedLogin.mockResolvedValue({ ok: true, state: 'login_started' })
    mockedSaveEntry.mockResolvedValue({ ok: true, entry: 'managed' })

    render(
      <AiSettingsPanel
        llmSettings={makeSettings({ codex_entry: 'local' })}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    const hint = await screen.findByTestId('codex-unconfigured')
    expect(hint.textContent).toMatch(/codex login/)
    expect(screen.queryByTestId('codex-login-button')).toBeNull()

    // 「连接我的 ChatGPT」在本机入口下 = 显式切到托管入口再登录（绝不静默切换）
    fireEvent.click(await screen.findByTestId('codex-model-drawer-open'))
    await waitFor(() => expect(mockedSaveEntry).toHaveBeenCalledWith('managed'))
    await waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('codex-login-pending')).toBeTruthy()
  })

  test('入口清单一律来自后端枚举，前端不硬编码入口名', async () => {
    render(
      <AiSettingsPanel
        llmSettings={makeSettings()}
        onOpenConfig={() => {}}
        onTestConnection={vi.fn()}
      />,
    )

    await waitFor(() => expect(mockedEntry).toHaveBeenCalled())
    const options = Array.from(
      (await screen.findByTestId('codex-entry-select')).querySelectorAll('option'),
    ).map((option) => option.value)
    expect(options).toEqual(['local', 'managed'])
  })
})
