import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  fetchModelingPlugins: vi.fn(),
  fetchModelingTools: vi.fn(),
  setModelingPluginEnabled: vi.fn(),
  adoptModelingPlugin: vi.fn(),
  installModelingPlugin: vi.fn(),
  personalizeModelingPlugin: vi.fn(),
  saveModelingPluginMethod: vi.fn(),
  restoreModelingPlugin: vi.fn(),
}))

vi.mock('../../api/client', () => api)

import { ModelingPluginsPanel } from './ModelingPluginsPanel'

const plugin = {
  skill_id: 'chinese-timber-zuodou', name: '中国古建 / 坐斗', domain: '中国古建',
  version: '1.0.0', status: 'active', content_hash: 'abc123456789', source: 'personal',
  intents: ['create', 'modify'], aliases: ['坐斗'], capabilities: ['methodology'],
  methodology: '# 坐斗方法', versions: ['1.0.0'], recent_usage: [], installed: true,
  selected: true, enabled: true, pinned_version: '1.0.0', pinned_hash: 'abc123456789',
  update_available: false, shadowed: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchModelingPlugins.mockResolvedValue({ ok: true, has_project: true, plugins: [plugin], issues: [], selection_issues: [] })
  api.fetchModelingTools.mockResolvedValue({ ok: true, tools: [{ id: 'compiler', name: '编译 GSM', provider: 'HSFCompiler', status: 'mock' }] })
  api.setModelingPluginEnabled.mockResolvedValue({ ok: true })
})

describe('ModelingPluginsPanel', () => {
  test('shows installed plugin and runtime tools', async () => {
    render(<ModelingPluginsPanel active projectName="Zuodou" />)
    expect(await screen.findByText('中国古建 / 坐斗')).toBeTruthy()
    expect(screen.getByText('编译 GSM')).toBeTruthy()
    expect(screen.getByText(/本项目已启用/)).toBeTruthy()
  })

  test('keeps enablement as a draft until explicit save', async () => {
    render(<ModelingPluginsPanel active projectName="Zuodou" />)
    const toggle = await screen.findByRole('checkbox', { name: '启用' })
    fireEvent.click(toggle)
    expect(api.setModelingPluginEnabled).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '保存启用状态' }))
    await waitFor(() => expect(api.setModelingPluginEnabled).toHaveBeenCalledWith('chinese-timber-zuodou', false))
  })
})
