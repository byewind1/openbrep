import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { LlmSettings } from '../../api/types'
import { ModelVisibilityPanel } from './ModelVisibilityPanel'
import { readStoredVisibilityKeys, useModelVisibilityStore } from '../../state/modelVisibility'

function makeGroups(): Pick<LlmSettings, 'model_groups'> {
  return {
    model_groups: {
      custom: [
        { id: 'ymg/deepseek-v3', label: 'ymg/deepseek-v3', kind: 'custom', provider: 'ymg', has_api_key: true },
      ],
      official: [
        { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek', has_api_key: true },
        { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu', has_api_key: false },
      ],
    },
  }
}

function renderPanel(props: Partial<Parameters<typeof ModelVisibilityPanel>[0]> = {}) {
  const onSelect = props.onSelect ?? vi.fn()
  render(
    <ModelVisibilityPanel
      llmSettings={props.llmSettings ?? makeGroups()}
      codex={props.codex ?? { connected: false, models: [] }}
      query={props.query ?? ''}
      currentId={props.currentId ?? 'deepseek-chat'}
      pendingModel={props.pendingModel ?? null}
      disabled={props.disabled ?? false}
      onSelect={onSelect}
    />,
  )
  return { onSelect }
}

beforeEach(() => {
  localStorage.clear()
  useModelVisibilityStore.setState({ keys: null })
})

describe('ModelVisibilityPanel', () => {
  test('renders sections grouped by provider with default-rule switch states', () => {
    renderPanel()
    expect(screen.getByText('自定义')).toBeTruthy()
    expect(screen.getByText('官方')).toBeTruthy()
    // 默认规则：ymg/deepseek 已配置 → 开；zhipu 未配 key → 关
    expect(screen.getByRole('switch', { name: /ymg\/deepseek-v3/ }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: /deepseek-chat/ }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: /glm-4-flash/ }).getAttribute('aria-checked')).toBe('false')
  })

  test('toggle only changes visibility (persisted to localStorage), never selects a model', () => {
    const { onSelect } = renderPanel()
    const toggle = screen.getByRole('switch', { name: /glm-4-flash/ })
    fireEvent.click(toggle)
    expect(onSelect).not.toHaveBeenCalled()
    expect(readStoredVisibilityKeys(localStorage)).toContain('zhipu::glm-4-flash')
    // deepseek 默认值随种子保留
    expect(readStoredVisibilityKeys(localStorage)).toContain('deepseek::deepseek-chat')
  })

  test('clicking a model name (not the switch) goes to the set-default confirm flow', () => {
    const { onSelect } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'glm-4-flash' }))
    expect(onSelect).toHaveBeenCalledWith('glm-4-flash', 'official')
    // 点击模型名不得改变可见性（仍为未自定义 null）
    expect(readStoredVisibilityKeys(localStorage)).toBeNull()
  })

  test('codex rows dispatch the codex confirm flow', () => {
    const { onSelect } = renderPanel({
      codex: {
        connected: true,
        models: [{ id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' }],
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'openai-codex/gpt-5.6-luna' }))
    expect(onSelect).toHaveBeenCalledWith('openai-codex/gpt-5.6-luna', 'codex')
  })

  test('current model row shows the current marker and active highlight', () => {
    renderPanel({ currentId: 'glm-4-flash' })
    const nameButton = screen.getByRole('button', { name: 'glm-4-flash' })
    expect(nameButton.className).toContain('active')
    expect(screen.getByText('当前')).toBeTruthy()
  })

  test('search filters rows by label or provider', () => {
    renderPanel({ query: 'glm' })
    expect(screen.getByRole('button', { name: 'glm-4-flash' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'deepseek-chat' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'ymg/deepseek-v3' })).toBeNull()
  })

  test('no match shows the empty message', () => {
    renderPanel({ query: 'no-such-model' })
    expect(screen.getByText('无匹配模型')).toBeTruthy()
  })

  test('provider group is collapsible', () => {
    renderPanel()
    const header = screen.getByRole('button', { name: /▾ deepseek/ })
    fireEvent.click(header)
    expect(screen.queryByRole('button', { name: 'deepseek-chat' })).toBeNull()
    fireEvent.click(header)
    expect(screen.getByRole('button', { name: 'deepseek-chat' })).toBeTruthy()
  })

  test('hiding the last model of a provider writes the hide-all sentinel; re-enable returns only that one', () => {
    renderPanel()
    const toggle = screen.getByRole('switch', { name: /deepseek-chat/ })
    fireEvent.click(toggle)
    expect(readStoredVisibilityKeys(localStorage)).toContain('deepseek::')
    fireEvent.click(screen.getByRole('switch', { name: /deepseek-chat/ }))
    const keys = readStoredVisibilityKeys(localStorage) ?? []
    expect(keys).not.toContain('deepseek::')
    expect(keys).toContain('deepseek::deepseek-chat')
  })
})
