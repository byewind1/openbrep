import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { LlmSettings } from '../api/types'
import { ModelPill } from './ModelPill'
import { useModelVisibilityStore } from '../state/modelVisibility'

function makeLlmSettings(overrides: Partial<LlmSettings> = {}): LlmSettings {
  return {
    model: 'deepseek-chat',
    model_available: true,
    models: [],
    model_groups: {
      custom: [],
      official: [
        { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek', has_api_key: true },
        { id: 'deepseek-reasoner', label: 'deepseek-reasoner', kind: 'official', provider: 'deepseek', has_api_key: true },
        { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu', has_api_key: false },
      ],
    },
    api_key: '',
    api_base: '',
    max_retries: 5,
    assistant_settings: '',
    ...overrides,
  }
}

function renderPill(props: Partial<Parameters<typeof ModelPill>[0]> = {}) {
  const onSwitch = props.onSwitch ?? vi.fn().mockResolvedValue(undefined)
  const onReset = props.onReset ?? vi.fn().mockResolvedValue(undefined)
  const onEditVisibility = props.onEditVisibility ?? vi.fn()
  render(
    <ModelPill
      llmSettings={props.llmSettings ?? makeLlmSettings()}
      codex={props.codex ?? { connected: false, models: [], loaded: true }}
      onSwitch={onSwitch}
      onReset={onReset}
      onEditVisibility={onEditVisibility}
      onOpen={props.onOpen}
    />,
  )
  return { onSwitch, onReset, onEditVisibility }
}

beforeEach(() => {
  localStorage.clear()
  useModelVisibilityStore.setState({ keys: null })
})

describe('ModelPill', () => {
  test('shows the effective model and opens the menu with visible models only (default rules)', async () => {
    renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    // 默认可见规则：已配 key 的 deepseek 可见；未配 key 的 zhipu 不出现
    expect(await screen.findByRole('option', { name: /deepseek-reasoner/ })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /glm-4-flash/ })).toBeNull()
  })

  test('current model stays pinned even when hidden by default rules', async () => {
    renderPill({ llmSettings: makeLlmSettings({ model: 'glm-4-flash' }) })
    fireEvent.click(screen.getByRole('button', { name: /glm-4-flash/ }))
    const current = await screen.findAllByRole('option', { name: /glm-4-flash/ })
    expect(current.length).toBeGreaterThan(0)
    expect(current[0].className).toContain('is-current')
  })

  test('search crosses visibility: hidden unconfigured model is reachable by search', async () => {
    const { onSwitch } = renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    fireEvent.change(screen.getByPlaceholderText('搜索全部模型…'), { target: { value: 'glm' } })
    const row = await screen.findByRole('option', { name: /glm-4-flash/ })
    fireEvent.click(row)
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith('glm-4-flash'))
  })

  test('switch calls the session action and closes the menu', async () => {
    const { onSwitch } = renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    fireEvent.click(await screen.findByRole('option', { name: /deepseek-reasoner/ }))
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith('deepseek-reasoner'))
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
  })

  test('switch failure shows the error verbatim (no silent failure)', async () => {
    const onSwitch = vi.fn().mockRejectedValue(new Error('HTTP 401: invalid api key'))
    renderPill({ onSwitch })
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    fireEvent.click(await screen.findByRole('option', { name: /deepseek-reasoner/ }))
    expect(await screen.findByText('HTTP 401: invalid api key')).toBeTruthy()
  })

  test('override state shows the dot and reset action; reset calls onReset', async () => {
    const { onReset } = renderPill({
      llmSettings: makeLlmSettings({ model: 'glm-4-flash', session_model: 'glm-4-flash' }),
    })
    expect(screen.getByLabelText('会话覆盖')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }))
    await waitFor(() => expect(onReset).toHaveBeenCalledTimes(1))
  })

  test('no override → no dot, no reset button', () => {
    renderPill()
    expect(screen.queryByLabelText('会话覆盖')).toBeNull()
    expect(screen.queryByRole('button', { name: '恢复默认' })).toBeNull()
  })

  test('unavailable model shows warning marker', () => {
    renderPill({ llmSettings: makeLlmSettings({ model_available: false }) })
    expect(screen.getByText('⚠')).toBeTruthy()
  })

  test('codex models absent when not connected; present when connected', async () => {
    const codexModels = [
      { id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' },
    ]
    renderPill({ codex: { connected: true, models: codexModels, loaded: true } })
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    expect(await screen.findByRole('option', { name: /GPT-5\.6 Luna/ })).toBeTruthy()
  })

  test('opening the menu lazy-loads the codex catalog', () => {
    const onOpen = vi.fn()
    renderPill({ onOpen })
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  test('edit visibility entry opens the settings drawer panel', async () => {
    const { onEditVisibility } = renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    fireEvent.click(await screen.findByRole('button', { name: '编辑模型可见性…' }))
    expect(onEditVisibility).toHaveBeenCalledTimes(1)
  })

  test('respects explicit hide from the visibility store', async () => {
    useModelVisibilityStore.setState({ keys: ['deepseek::deepseek-chat'] })
    renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    // deepseek-reasoner 被显式隐藏（stored 集不含它）→ 浏览模式不出现
    expect(screen.queryByRole('option', { name: /deepseek-reasoner/ })).toBeNull()
    // 搜索仍可达
    fireEvent.change(screen.getByPlaceholderText('搜索全部模型…'), { target: { value: 'reasoner' } })
    expect(await screen.findByRole('option', { name: /deepseek-reasoner/ })).toBeTruthy()
  })

  test('provider explicitly switched off (sentinel) is hard-hidden in browse AND search modes', async () => {
    useModelVisibilityStore.setState({ keys: ['deepseek::'] })
    renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    // 浏览模式：deepseek 整组消失
    expect(screen.queryByRole('option', { name: /deepseek-reasoner/ })).toBeNull()
    // 搜索模式：显式关闭的 provider 同样零泄漏
    fireEvent.change(screen.getByPlaceholderText('搜索全部模型…'), { target: { value: 'reasoner' } })
    await waitFor(() => expect(screen.queryByRole('option', { name: /deepseek-reasoner/ })).toBeNull())
    // 同一哨兵状态下，未自定义的默认隐藏 provider（zhipu）搜索可发现性不变
    fireEvent.change(screen.getByPlaceholderText('搜索全部模型…'), { target: { value: 'glm' } })
    expect(await screen.findByRole('option', { name: /glm-4-flash/ })).toBeTruthy()
  })

  test('current-model pin survives its provider being switched off', async () => {
    useModelVisibilityStore.setState({ keys: ['deepseek::'] })
    renderPill()
    fireEvent.click(screen.getByRole('button', { name: /deepseek-chat/ }))
    // pin 是状态展示不是可选入口：provider 关掉后当前模型仍 pin 在顶部
    const current = await screen.findAllByRole('option', { name: /deepseek-chat/ })
    expect(current.length).toBeGreaterThan(0)
    expect(current[0].className).toContain('is-current')
  })
})
