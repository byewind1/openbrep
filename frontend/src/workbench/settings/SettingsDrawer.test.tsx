import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { describe, expect, test, vi } from 'vitest'
import { clampSettingsDrawerWidth, SettingsDrawer } from './SettingsDrawer'
import type { LlmSettings } from '../../api/types'

function renderSettingsDrawer(
  llmSettings: LlmSettings,
  onLlmSettingsChange: ComponentProps<typeof SettingsDrawer>['onLlmSettingsChange'] = vi.fn(async () => {}),
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
      onCompilerSettingsChange={vi.fn(async () => undefined)}
      onLlmSettingsChange={onLlmSettingsChange}
      onTestLlmConnection={async () => ({ ok: true })}
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
  test('keeps settings as draft until Save Settings is pressed and saves compiler before AI', async () => {
    const saveOrder: string[] = []
    const onCompilerSettingsChange = vi.fn(async () => {
      saveOrder.push('compiler')
    })
    const onLlmSettingsChange = vi.fn(async () => {
      saveOrder.push('llm')
    })
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
      onLlmSettingsChange,
      { onCompilerSettingsChange },
    )

    fireEvent.change(screen.getByLabelText('Compiler mode'), { target: { value: 'lp' } })

    expect(onCompilerSettingsChange).not.toHaveBeenCalled()
    expect(screen.getByText('Unsaved changes')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }))

    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy())
    expect(onCompilerSettingsChange).toHaveBeenCalledWith({ mode: 'lp', converter_path: '', output_dir: '' })
    expect(onLlmSettingsChange).toHaveBeenCalled()
    expect(saveOrder).toEqual(['compiler', 'llm'])
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

  test('switches between official and custom model lists without using a blank sentinel option', () => {
    renderSettingsDrawer({
      model: 'deepseek-chat',
      models: ['ymg-gpt-5.3-codex', 'deepseek-chat'],
      model_groups: {
        custom: [
          {
            id: 'ymg-gpt-5.3-codex',
            label: 'ymg-gpt-5.3-codex',
            kind: 'custom',
            provider: 'ymg',
            target_model: 'gpt-5.3-codex',
            protocol: 'openai',
            api_base: 'https://api.ymg.example/v1',
            has_api_key: true,
          },
        ],
        official: [{ id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official', provider: 'deepseek' }],
      },
      api_key: 'deepseek-key',
      api_base: 'https://api.deepseek.com/v1',
      max_retries: 5,
      assistant_settings: '',
    })

    expect(screen.getByRole('button', { name: 'Official' }).className).toContain('active')
    expect(screen.getByRole('option', { name: 'deepseek-chat' }).getAttribute('aria-selected')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Custom' }))

    expect(screen.getByRole('button', { name: 'Custom' }).className).toContain('active')
    expect(screen.getByRole('option', { name: 'ymg-gpt-5.3-codex (ymg)' }).getAttribute('aria-selected')).toBe('true')
    expect(within(screen.getByRole('listbox', { name: 'Model' })).queryByText('Custom model')).toBeNull()
    expect(screen.getByText('Custom provider: ymg / openai')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Exact ID' }))

    expect(screen.getByRole('button', { name: 'Exact ID' }).className).toContain('active')
    expect(within(screen.getByRole('listbox', { name: 'Model' })).getByText('Manual model ID')).toBeTruthy()
    expect(screen.getAllByText('Manual model ID').length).toBeGreaterThanOrEqual(2)
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

    const view = renderSettingsDrawer(llmSettings, vi.fn(async () => undefined), {
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
        onCompilerSettingsChange={vi.fn(async () => undefined)}
        onLlmSettingsChange={vi.fn(async () => undefined)}
        onTestLlmConnection={async () => ({ ok: true })}
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
