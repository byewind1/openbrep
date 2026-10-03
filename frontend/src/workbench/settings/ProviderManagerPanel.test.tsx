import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { ProviderManagerPanel, type ProviderManagerPanelProps } from './ProviderManagerPanel'
import type {
  LlmConnectionTestResult,
  LlmProviderDraft,
  LlmProviderInfo,
  LlmProviderTemplate,
  LlmProviderWriteResult,
} from '../../api/types'

function makeProviderInfo(name: string, overrides: Partial<LlmProviderInfo> = {}): LlmProviderInfo {
  return {
    name,
    api: `https://${name}.example/v1`,
    api_mode: 'chat_completions',
    default_model: '',
    models: [`${name}-main`],
    model_entries: [{ alias: `${name}-main`, model: `${name}-main` }],
    model_count: 1,
    has_api_key: true,
    key_display: 'tes…7890',
    is_codex: false,
    credential: { location: 'entry', form: 'direct', resolvable: true },
    ...overrides,
  }
}

function makeTemplates(): LlmProviderTemplate[] {
  return [
    { name: 'zhipu', api: 'https://open.bigmodel.cn/api/paas/v4', api_mode: 'chat_completions', models: ['glm-4.6'] },
    { name: 'deepseek', api: 'https://api.deepseek.com/v1', api_mode: 'chat_completions', models: ['deepseek-v4-pro'] },
  ]
}

function okResult(overrides: Partial<LlmProviderWriteResult> = {}): LlmProviderWriteResult {
  return { ok: true, revision: 'rev-2', ...overrides }
}

function makeProps(overrides: Partial<ProviderManagerPanelProps> = {}): ProviderManagerPanelProps {
  return {
    providers: [],
    providerTemplates: makeTemplates(),
    loaded: true,
    conflict: null,
    onLoadProviders: vi.fn().mockResolvedValue(undefined),
    onCreateProvider: vi.fn().mockResolvedValue(okResult()),
    onUpdateProvider: vi.fn().mockResolvedValue(okResult()),
    onDeleteProvider: vi.fn().mockResolvedValue(okResult({ deleted: 'relay' })),
    onTestDraft: vi.fn().mockResolvedValue({ ok: true, message: 'LLM connection OK', model: 'x', duration_ms: 12 } as LlmConnectionTestResult),
    ...overrides,
  }
}

describe('ProviderManagerPanel (卡06)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('未加载时懒加载列表', async () => {
    const onLoadProviders = vi.fn().mockResolvedValue(undefined)
    render(<ProviderManagerPanel {...makeProps({ loaded: false, onLoadProviders })} />)

    await waitFor(() => expect(onLoadProviders).toHaveBeenCalledTimes(1))
  })

  test('模板网格预填草稿：端点/协议/模型带入，名称留空由用户起', () => {
    render(<ProviderManagerPanel {...makeProps()} />)

    fireEvent.click(screen.getByTestId('provider-template-zhipu'))

    const name = screen.getByTestId('provider-name') as HTMLInputElement
    const api = screen.getByTestId('provider-api') as HTMLInputElement
    const apiMode = screen.getByTestId('provider-api-mode') as HTMLSelectElement
    expect(name.value).toBe('')
    expect(api.value).toBe('https://open.bigmodel.cn/api/paas/v4')
    expect(apiMode.value).toBe('chat_completions')
    expect(screen.getByText('glm-4.6')).toBeTruthy()
  })

  test('自定义空白卡打开空草稿表单', () => {
    render(<ProviderManagerPanel {...makeProps()} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))

    expect((screen.getByTestId('provider-name') as HTMLInputElement).value).toBe('')
    expect(screen.getByTestId('provider-model-tags').textContent).toContain('尚无模型')
  })

  test('手输模型保存：draft 携带 alias/model 对，Save 调创建并带 expected_revision 由数据层处理', async () => {
    const onCreateProvider = vi.fn().mockResolvedValue(okResult())
    render(<ProviderManagerPanel {...makeProps({ onCreateProvider })} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'my-relay' } })
    fireEvent.change(screen.getByTestId('provider-model-input'), { target: { value: 'real-model-id' } })
    fireEvent.change(screen.getByTestId('provider-alias-input'), { target: { value: 'my-alias' } })
    fireEvent.click(screen.getByTestId('provider-model-add'))

    expect(screen.getByTestId('provider-model-tags').textContent).toContain('my-alias (real-model-id)')

    fireEvent.click(screen.getByTestId('provider-save'))

    await waitFor(() => expect(onCreateProvider).toHaveBeenCalledTimes(1))
    const draft = vi.mocked(onCreateProvider).mock.calls[0][0] as LlmProviderDraft
    expect(draft.name).toBe('my-relay')
    expect(draft.models).toEqual([{ alias: 'my-alias', model: 'real-model-id' }])
  })

  test('key 不回显：编辑已存 provider 时 key 输入为空', () => {
    render(<ProviderManagerPanel {...makeProps({ providers: [makeProviderInfo('relay')] })} />)

    fireEvent.click(screen.getByTestId('provider-edit-relay'))

    const keyInput = screen.getByTestId('provider-api-key') as HTMLInputElement
    expect(keyInput.type).toBe('password')
    expect(keyInput.value).toBe('')
  })

  test('编辑未触碰 models 时 draft 不携带 models（保真既有 alias/model 对）', async () => {
    const onUpdateProvider = vi.fn().mockResolvedValue(okResult())
    render(<ProviderManagerPanel {...makeProps({ providers: [makeProviderInfo('relay')] , onUpdateProvider })} />)

    fireEvent.click(screen.getByTestId('provider-edit-relay'))
    fireEvent.change(screen.getByTestId('provider-api-key'), { target: { value: 'test-new-key-0001' } })
    fireEvent.click(screen.getByTestId('provider-save'))

    await waitFor(() => expect(onUpdateProvider).toHaveBeenCalledTimes(1))
    const [name, draft] = vi.mocked(onUpdateProvider).mock.calls[0]
    expect(name).toBe('relay')
    expect(draft.api_key).toBe('test-new-key-0001')
    expect(draft.models).toBeUndefined()
    expect(draft.api).toBe('https://relay.example/v1')
  })

  test('dirty 离开确认：拒绝时草稿保留', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<ProviderManagerPanel {...makeProps()} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'half-typed' } })
    fireEvent.click(screen.getByTestId('provider-cancel'))

    expect(confirmSpy).toHaveBeenCalled()
    expect(screen.getByTestId('provider-form')).toBeTruthy()
    confirmSpy.mockRestore()
  })

  test('dirty 离开确认：接受时草稿关闭', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<ProviderManagerPanel {...makeProps()} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'half-typed' } })
    fireEvent.click(screen.getByTestId('provider-cancel'))

    expect(screen.queryByTestId('provider-form')).toBeNull()
    confirmSpy.mockRestore()
  })

  test('config_modified 冲突：草稿保留 + 内联错误提示', async () => {
    const onCreateProvider = vi.fn().mockResolvedValue({
      ok: false,
      code: 'config_modified',
      error: '配置文件已被外部修改，请刷新设置后重试（当前草稿已保留）。',
    })
    render(<ProviderManagerPanel {...makeProps({ onCreateProvider, conflict: null })} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'my-relay' } })
    fireEvent.click(screen.getByTestId('provider-save'))

    await waitFor(() => expect(screen.getByTestId('provider-form-error').textContent).toContain('外部修改'))
    // 草稿未被清空
    expect((screen.getByTestId('provider-name') as HTMLInputElement).value).toBe('my-relay')
  })

  test('conflict prop 显示横幅并可刷新', async () => {
    const onLoadProviders = vi.fn().mockResolvedValue(undefined)
    render(
      <ProviderManagerPanel {...makeProps({ conflict: '配置已被外部修改，请刷新后重试。', onLoadProviders })} />,
    )

    const banner = screen.getByTestId('provider-conflict')
    expect(banner.textContent).toContain('外部修改')

    fireEvent.click(within(banner).getByRole('button'))
    await waitFor(() => expect(onLoadProviders).toHaveBeenCalled())
  })

  test('草稿测试走副本通道：draft + 模型引用交给 onTestDraft，不影响已存配置', async () => {
    const onTestDraft = vi
      .fn()
      .mockResolvedValue({ ok: true, message: 'LLM connection OK', model: 'my-relay/m1', duration_ms: 9 })
    render(<ProviderManagerPanel {...makeProps({ onTestDraft })} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'my-relay' } })
    fireEvent.change(screen.getByTestId('provider-model-input'), { target: { value: 'm1' } })
    fireEvent.click(screen.getByTestId('provider-model-add'))
    fireEvent.click(screen.getByTestId('provider-test'))

    await waitFor(() => expect(onTestDraft).toHaveBeenCalledTimes(1))
    const [draft, model] = vi.mocked(onTestDraft).mock.calls[0]
    expect(model).toBe('my-relay/m1')
    expect((draft as LlmProviderDraft).name).toBe('my-relay')
    expect(screen.getByTestId('provider-test-result').textContent).toContain('连接成功')
  })

  test('无模型时草稿测试提示先填模型', async () => {
    render(<ProviderManagerPanel {...makeProps()} />)

    fireEvent.click(screen.getByTestId('provider-template-custom'))
    fireEvent.change(screen.getByTestId('provider-name'), { target: { value: 'my-relay' } })
    fireEvent.click(screen.getByTestId('provider-test'))

    await waitFor(() => expect(screen.getByTestId('provider-form-error').textContent).toContain('模型'))
  })

  test('删除确认后调用删除；in_use 返回渲染可读 refs', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onDeleteProvider = vi.fn().mockResolvedValue({
      ok: false,
      code: 'in_use',
      error: '服务商 relay 仍被引用。',
      refs: [
        { location: 'llm.model', blocking: true, context: 'relay/main', detail: '当前默认模型指向该服务商。' },
        { location: 'frontend_visibility', blocking: false, context: 'relay', detail: '提示' },
      ],
    })
    render(
      <ProviderManagerPanel {...makeProps({ providers: [makeProviderInfo('relay')], onDeleteProvider })} />,
    )

    fireEvent.click(screen.getByTestId('provider-delete-relay'))

    await waitFor(() => expect(onDeleteProvider).toHaveBeenCalledWith('relay'))
    const refsBlock = screen.getByTestId('provider-delete-error')
    expect(refsBlock.textContent).toContain('llm.model')
    expect(refsBlock.textContent).toContain('relay/main')
    expect(refsBlock.textContent).toContain('frontend_visibility')
    confirmSpy.mockRestore()
  })

  test('列表行渲染凭据徽标、模型数与掩码 key_display', () => {
    render(
      <ProviderManagerPanel
        {...makeProps({
          providers: [
            makeProviderInfo('relay'),
            makeProviderInfo('pooled', {
              credential: { location: 'entry', form: 'pool', resolvable: true },
              key_display: '池×2',
            }),
          ],
        })}
      />,
    )

    expect(screen.getByTestId('provider-credential-relay').textContent).toContain('条目凭据')
    expect(screen.getByTestId('provider-credential-pooled').textContent).toContain('凭据池')
    expect(screen.getByTestId('provider-row-relay').textContent).toContain('1 个模型')
    expect(screen.getByTestId('provider-row-relay').textContent).toContain('tes…7890')
  })

  test('codex 保留条目不渲染编辑/删除按钮', () => {
    render(
      <ProviderManagerPanel
        {...makeProps({ providers: [makeProviderInfo('openai-codex', { is_codex: true, key_display: '', has_api_key: false })] })}
      />,
    )

    expect(screen.queryByTestId('provider-edit-openai-codex')).toBeNull()
    expect(screen.queryByTestId('provider-delete-openai-codex')).toBeNull()
  })
})
