import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { describe, expect, test, vi } from 'vitest'
import { clampSettingsDrawerWidth, SettingsDrawer } from './SettingsDrawer'
import type { LlmSettings } from '../../api/types'

function renderSettingsDrawer(
  llmSettings: LlmSettings,
  _unused?: unknown,
  overrides: Partial<ComponentProps<typeof SettingsDrawer>> = {},
) {
  return render(
    <SettingsDrawer
      open
      compilerSettings={{ mode: 'mock', converter_path: '', output_dir: '' }}
      llmSettings={llmSettings}
      recentProjects={[]}
      memoryStatus={null}
      memoryLessons={[]}
      memorySkillPreview=""
      memoryBusy={false}
      gitStatus={null}
      gitBusy={false}
      knowledgeStatus={null}
      knowledgeBusy={false}
      onClose={vi.fn()}
      onCompilerSettingsChange={vi.fn(async (settings) => settings)}
      onOpenConfig={vi.fn()}
      onTestLlmConnection={vi.fn(async () => ({ ok: true }))}
      onReloadRuntimeSettings={vi.fn(async () => undefined)}
      onBrowseCompilerFile={vi.fn(async () => null)}
      onBrowseOutputDirectory={vi.fn(async () => null)}
      onOpenProjectPath={vi.fn()}
      onExportHsfProject={vi.fn()}
      onResetCurrentProject={vi.fn()}
      onLoadProjectGitStatus={vi.fn()}
      onInitializeProjectGit={vi.fn()}
      onSetProjectGitEnabled={vi.fn()}
      onCommitProjectGit={vi.fn()}
      onLoadKnowledgeStatus={vi.fn()}
      onReloadKnowledge={vi.fn()}
      onLoadMemoryLessons={vi.fn()}
      onSummarizeProjectMemory={vi.fn()}
      onUpdateMemoryLesson={vi.fn()}
      onDeleteMemoryLesson={vi.fn()}
      onIgnoreMemoryLesson={vi.fn()}
      onClearProjectMemory={vi.fn()}
      {...overrides}
    />,
  )
}

describe('SettingsDrawer AI model settings', () => {
  test('shows common settings by default and keeps low-frequency sections collapsed', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: {
        custom: [],
        official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
      },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    expect(screen.getByText('模型')).toBeTruthy()
    expect(screen.getByText('Mock')).toBeTruthy()
    expect(screen.getByText('最近 0 个')).toBeTruthy()
    expect(screen.queryByText('LP_XMLConverter')).toBeNull()
    expect(screen.queryByText('Recent HSF projects')).toBeNull()
    expect(screen.queryByText('Project Git')).toBeNull()
    expect(screen.queryByText('Learned error lessons')).toBeNull()
  })

  test('shows an interface section for language selection, collapsed by default', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: { custom: [], official: [] },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    expect(screen.getByRole('button', { name: /界面/ })).toBeTruthy()
    expect(screen.queryByText('语言')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /界面/ }))
    expect(screen.getByText('语言')).toBeTruthy()
    expect(screen.getByRole('radio', { name: '中文' })).toHaveProperty('checked', true)
  })

  test('saves compiler settings and reloads on Save', async () => {
    const saveOrder: string[] = []
    const onCompilerSettingsChange = vi.fn(async (settings) => { saveOrder.push('compiler'); return settings })
    const onReloadRuntimeSettings = vi.fn(async () => { saveOrder.push('reload') })
    renderSettingsDrawer(
      { model: 'deepseek-chat', models: ['deepseek-chat'], model_groups: { custom: [], official: [] }, api_key: '', api_base: '', max_retries: 5, assistant_settings: '' },
      undefined,
      { onCompilerSettingsChange, onReloadRuntimeSettings },
    )

    fireEvent.click(screen.getByRole('button', { name: /编译器/ }))
    fireEvent.change(screen.getByLabelText('Compiler mode'), { target: { value: 'lp' } })
    expect(screen.getByText('未保存')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(screen.getByText('已保存')).toBeTruthy())
    expect(onCompilerSettingsChange).toHaveBeenCalledWith({ mode: 'lp', converter_path: '', output_dir: '' })
    expect(saveOrder).toEqual(['compiler', 'reload'])
  })

  test('keeps settings dirty and reports save errors when compiler settings fail', async () => {
    const onCompilerSettingsChange = vi.fn(async () => { throw new Error('Compiler settings were not saved') })
    const onReloadRuntimeSettings = vi.fn(async () => undefined)
    renderSettingsDrawer(
      { model: 'deepseek-chat', models: ['deepseek-chat'], model_groups: { custom: [], official: [] }, api_key: '', api_base: '', max_retries: 5, assistant_settings: '' },
      undefined,
      { onCompilerSettingsChange, onReloadRuntimeSettings },
    )

    fireEvent.click(screen.getByRole('button', { name: /编译器/ }))
    fireEvent.change(screen.getByLabelText('Compiler mode'), { target: { value: 'lp' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(screen.getByTitle('Compiler settings were not saved')).toBeTruthy())
    expect(screen.queryByText('已保存')).toBeNull()
    expect(screen.getByText('未保存')).toBeTruthy()
    expect(onReloadRuntimeSettings).not.toHaveBeenCalled()
  })

  test('resizes the settings panel from the left edge', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: {
        custom: [],
        official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
      },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    const drawer = screen.getByLabelText('工作台设置')
    const handle = screen.getByRole('separator', { name: '调整设置面板宽度' })

    expect(drawer.style.width).toBe('430px')

    fireEvent.pointerDown(handle, { button: 0, clientX: 0, pointerId: 1 })
    fireEvent.pointerMove(window, { clientX: -120 })

    expect(drawer.style.width).toBe('550px')
  })

  test('keeps resized settings width inside the viewport', () => {
    expect(clampSettingsDrawerWidth(900, 1024)).toBe(760)
    expect(clampSettingsDrawerWidth(100, 1024)).toBe(360)
    expect(clampSettingsDrawerWidth(900, 380)).toBe(356)
  })

  test('shows current model name and Edit config.toml button', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: { custom: [], official: [] },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    expect(screen.getAllByText('deepseek-chat').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: /Edit config\.toml/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Test connection/ })).toBeTruthy()
  })

  test('calls onOpenConfig when Edit config.toml is clicked', () => {
    const onOpenConfig = vi.fn()
    renderSettingsDrawer(
      { model: 'deepseek-chat', models: [], model_groups: { custom: [], official: [] }, api_key: '', api_base: '', max_retries: 5, assistant_settings: '' },
      undefined,
      { onOpenConfig },
    )
    fireEvent.click(screen.getByRole('button', { name: /Edit config\.toml/ }))
    expect(onOpenConfig).toHaveBeenCalledTimes(1)
  })

  test('renders grouped model list and highlights the current model', () => {
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash'],
        model_groups: {
          custom: [{ id: 'ymg/deepseek-v3', label: 'ymg/deepseek-v3', kind: 'custom', provider: 'ymg' }],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange: vi.fn(async () => undefined) },
    )

    expect(screen.getByText('自定义')).toBeTruthy()
    expect(screen.getByText('官方')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'ymg/deepseek-v3' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'glm-4-flash' })).toBeTruthy()

    const activeBtn = screen.getByRole('button', { name: 'deepseek-chat' })
    expect(activeBtn.className).toContain('active')
  })

  test('shows the current model at the top with a valid highlight', () => {
    const { container } = renderSettingsDrawer({
      model: 'deepseek-chat',
      model_available: true,
      models: ['deepseek-chat'],
      model_groups: { custom: [], official: [] },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    const display = container.querySelector('.settings-model-display')
    expect(display?.textContent).toBe('deepseek-chat')
    expect(display?.className).toContain('valid')
    expect(screen.queryByText(/当前模型不可用/)).toBeNull()
  })

  test('warns and points to Edit config.toml when the current model is unavailable', () => {
    const { container } = renderSettingsDrawer({
      model: 'gpt-4o',
      model_available: false,
      models: ['gpt-4o'],
      model_groups: { custom: [], official: [] },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    const display = container.querySelector('.settings-model-display')
    expect(display?.textContent).toBe('gpt-4o')
    expect(display?.className).toContain('invalid')
    const warning = screen.getByText(/当前模型不可用/)
    expect(warning.textContent).toContain('Edit config.toml')
  })

  test('shows an API key editor for official models and saves the key', async () => {
    const onSaveLlmApiKey = vi.fn(async () => undefined)
    renderSettingsDrawer(
      {
        model: 'gpt-4o',
        model_available: false,
        models: ['gpt-4o'],
        model_groups: {
          custom: [],
          official: [{ id: 'gpt-4o', label: 'gpt-4o', kind: 'official', provider: 'openai' }],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onSaveLlmApiKey },
    )

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-test' } })
    fireEvent.click(screen.getByRole('button', { name: '保存 Key' }))
    await waitFor(() => expect(onSaveLlmApiKey).toHaveBeenCalledWith('gpt-4o', 'sk-test'))
    // 断点 3 后：保存成功会自动验证连接并显示验证结果，不再只提示"已保存"
    await waitFor(() => expect(screen.getByText(/连接正常/)).toBeTruthy())
  })

  test('shows the API key editor for custom provider models (unified registry)', async () => {
    const onSaveLlmApiKey = vi.fn(async () => undefined)
    renderSettingsDrawer(
      {
        model: 'ymg/deepseek-v3',
        model_available: true,
        models: ['ymg/deepseek-v3'],
        model_groups: {
          custom: [{ id: 'ymg/deepseek-v3', label: 'ymg/deepseek-v3', kind: 'custom', provider: 'ymg' }],
          official: [],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onSaveLlmApiKey },
    )

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-ymg' } })
    fireEvent.click(screen.getByRole('button', { name: '保存 Key' }))
    await waitFor(() => expect(onSaveLlmApiKey).toHaveBeenCalledWith('ymg/deepseek-v3', 'sk-ymg'))
  })

  test('asks for confirmation, then switches and auto-tests the connection', async () => {
    const onModelChange = vi.fn(async () => undefined)
    const onTestLlmConnection = vi.fn(async () => ({ ok: true, message: 'LLM connection OK', duration_ms: 12 }))
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash'],
        model_groups: {
          custom: [],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange, onTestLlmConnection },
    )

    fireEvent.click(screen.getByRole('button', { name: 'glm-4-flash' }))
    expect(onModelChange).not.toHaveBeenCalled()
    expect(screen.getByTestId('model-switch-confirm').textContent).toContain('glm-4-flash')

    fireEvent.click(screen.getByRole('button', { name: '确认切换' }))
    await waitFor(() => expect(onModelChange).toHaveBeenCalledWith('glm-4-flash'))
    await waitFor(() => expect(onTestLlmConnection).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText(/LLM connection OK/)).toBeTruthy())
    expect(screen.queryByTestId('model-switch-confirm')).toBeNull()
  })

  test('does not switch when the confirmation is cancelled', () => {
    const onModelChange = vi.fn(async () => undefined)
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash'],
        model_groups: {
          custom: [],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange },
    )

    fireEvent.click(screen.getByRole('button', { name: 'glm-4-flash' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onModelChange).not.toHaveBeenCalled()
    expect(screen.queryByTestId('model-switch-confirm')).toBeNull()
  })

  test('shows the switch error verbatim when the model change fails', async () => {
    const onModelChange = vi.fn(async () => { throw new Error('config.toml is read-only') })
    const onTestLlmConnection = vi.fn(async () => ({ ok: true }))
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash'],
        model_groups: {
          custom: [],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange, onTestLlmConnection },
    )

    fireEvent.click(screen.getByRole('button', { name: 'glm-4-flash' }))
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }))

    await waitFor(() => expect(screen.getByText('config.toml is read-only')).toBeTruthy())
    expect(onTestLlmConnection).not.toHaveBeenCalled()
    expect(screen.getByTestId('model-switch-confirm')).toBeTruthy()
  })

  test('shows the auto-test failure verbatim after a successful switch', async () => {
    const onModelChange = vi.fn(async () => undefined)
    const onTestLlmConnection = vi.fn(async () => ({ ok: false, error: 'HTTP 401: invalid api key' }))
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash'],
        model_groups: {
          custom: [],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange, onTestLlmConnection },
    )

    fireEvent.click(screen.getByRole('button', { name: 'glm-4-flash' }))
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }))

    await waitFor(() => expect(screen.getByText('HTTP 401: invalid api key')).toBeTruthy())
  })

  test('shows the full server response for a failed test and copies it', async () => {
    const detail =
      'RuntimeError: LLM 认证失败：API Key 可能无效\n\nHTTP 401 响应原文：\n{"error":{"message":"Incorrect API key provided","code":"invalid_api_key"}}'
    const onTestLlmConnection = vi.fn(async () => ({ ok: false, error: 'LLM 认证失败：API Key 可能无效', detail }))
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(window.navigator, 'clipboard', { value: { writeText }, configurable: true })

    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat'],
        model_groups: {
          custom: [],
          official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onTestLlmConnection },
    )

    fireEvent.click(screen.getByRole('button', { name: /Test connection/ }))

    const block = await screen.findByTestId('llm-test-error')
    expect(within(block).getByText(/Incorrect API key provided/)).toBeTruthy()
    expect(block.querySelector('pre')?.textContent).toBe(detail)

    fireEvent.click(within(block).getByRole('button', { name: '复制错误信息' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(detail))
    await waitFor(() => expect(within(block).getByRole('button', { name: '已复制' })).toBeTruthy())
  })

  test('highlights the custom model when config stores its target model name', () => {
    renderSettingsDrawer(
      {
        model: 'gpt-5.4',
        models: ['ymg/gpt-5.4'],
        model_groups: {
          custom: [
            { id: 'ymg/gpt-5.4', label: 'ymg/gpt-5.4', kind: 'custom', provider: 'ymg', target_model: 'gpt-5.4' },
          ],
          official: [],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange: vi.fn(async () => undefined) },
    )

    const btn = screen.getByRole('button', { name: 'ymg/gpt-5.4' })
    expect(btn.className).toContain('active')
  })

  test('does not call onModelChange when clicking the current model', () => {
    const onModelChange = vi.fn(async () => undefined)
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat'],
        model_groups: {
          custom: [],
          official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange },
    )

    fireEvent.click(screen.getByRole('button', { name: 'deepseek-chat' }))
    expect(onModelChange).not.toHaveBeenCalled()
  })

  test('filters models by search query', () => {
    renderSettingsDrawer(
      {
        model: 'deepseek-chat',
        models: ['deepseek-chat', 'glm-4-flash', 'qwen-plus'],
        model_groups: {
          custom: [],
          official: [
            { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' },
            { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official', provider: 'zhipu' },
            { id: 'qwen-plus', label: 'qwen-plus', kind: 'official', provider: 'alibaba' },
          ],
        },
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
      undefined,
      { onModelChange: vi.fn(async () => undefined) },
    )

    const searchInput = screen.getByPlaceholderText('搜索模型…')
    fireEvent.change(searchInput, { target: { value: 'glm' } })

    expect(screen.getByRole('button', { name: 'glm-4-flash' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'deepseek-chat' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'qwen-plus' })).toBeNull()
  })

  test('hides model list when onModelChange is not provided', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: {
        custom: [],
        official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
      },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    })

    expect(screen.queryByPlaceholderText('搜索模型…')).toBeNull()
    expect(screen.queryByText('官方')).toBeNull()
  })

  test('loads settings side data only when the drawer opens', () => {
    const loadMemory = vi.fn()
    const firstLoadGit = vi.fn()
    const secondLoadGit = vi.fn()
    const llmSettings: LlmSettings = {
      model: 'deepseek-chat',
      models: ['deepseek-chat'],
      model_groups: {
        custom: [],
        official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
      },
      api_key: '',
      api_base: '',
      max_retries: 5,
      assistant_settings: '',
    }

    const view = renderSettingsDrawer(llmSettings, undefined, {
      onLoadMemoryLessons: loadMemory,
      onLoadProjectGitStatus: firstLoadGit,
    })

    expect(loadMemory).toHaveBeenCalledTimes(1)
    expect(firstLoadGit).toHaveBeenCalledTimes(1)

    view.rerender(
      <SettingsDrawer
        open
        compilerSettings={{ mode: 'mock', converter_path: '', output_dir: '' }}
        llmSettings={llmSettings}
        recentProjects={[]}
        memoryStatus={null}
        memoryLessons={[]}
        memorySkillPreview=""
        memoryBusy={false}
        gitStatus={null}
        gitBusy={false}
        knowledgeStatus={null}
        knowledgeBusy={false}
        onClose={vi.fn()}
        onCompilerSettingsChange={vi.fn(async (settings) => settings)}
        onOpenConfig={vi.fn()}
        onTestLlmConnection={vi.fn(async () => ({ ok: true }))}
        onReloadRuntimeSettings={vi.fn(async () => undefined)}
        onBrowseCompilerFile={vi.fn(async () => null)}
        onBrowseOutputDirectory={vi.fn(async () => null)}
        onOpenProjectPath={vi.fn()}
        onExportHsfProject={vi.fn()}
        onResetCurrentProject={vi.fn()}
        onLoadProjectGitStatus={secondLoadGit}
        onInitializeProjectGit={vi.fn()}
        onSetProjectGitEnabled={vi.fn()}
        onCommitProjectGit={vi.fn()}
        onLoadKnowledgeStatus={vi.fn()}
        onReloadKnowledge={vi.fn()}
        onLoadMemoryLessons={loadMemory}
        onSummarizeProjectMemory={vi.fn()}
        onUpdateMemoryLesson={vi.fn()}
        onDeleteMemoryLesson={vi.fn()}
        onIgnoreMemoryLesson={vi.fn()}
        onClearProjectMemory={vi.fn()}
      />,
    )

    expect(loadMemory).toHaveBeenCalledTimes(1)
    expect(secondLoadGit).not.toHaveBeenCalled()
  })
})
