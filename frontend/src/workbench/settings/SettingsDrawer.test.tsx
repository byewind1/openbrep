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

    expect(screen.getByText('Model')).toBeTruthy()
    expect(screen.getByText('Mock')).toBeTruthy()
    expect(screen.getByText('0 recent')).toBeTruthy()
    expect(screen.queryByText('LP_XMLConverter')).toBeNull()
    expect(screen.queryByText('Recent HSF projects')).toBeNull()
    expect(screen.queryByText('Project Git')).toBeNull()
    expect(screen.queryByText('Learned error lessons')).toBeNull()
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

    fireEvent.click(screen.getByRole('button', { name: /Compiler/ }))
    fireEvent.change(screen.getByLabelText('Compiler mode'), { target: { value: 'lp' } })
    expect(screen.getByText('Unsaved')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy())
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

    fireEvent.click(screen.getByRole('button', { name: /Compiler/ }))
    fireEvent.change(screen.getByLabelText('Compiler mode'), { target: { value: 'lp' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByTitle('Compiler settings were not saved')).toBeTruthy())
    expect(screen.queryByText('Saved')).toBeNull()
    expect(screen.getByText('Unsaved')).toBeTruthy()
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

    const drawer = screen.getByLabelText('Workbench settings')
    const handle = screen.getByRole('separator', { name: 'Resize settings panel' })

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
