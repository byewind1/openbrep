import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { SettingsDrawer } from './SettingsDrawer'
import type { LlmSettings } from '../../api/types'

function renderSettingsDrawer(llmSettings: LlmSettings, onLlmSettingsChange = vi.fn()) {
  render(
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
      onCompilerSettingsChange={vi.fn()}
      onLlmSettingsChange={onLlmSettingsChange}
      onTestLlmConnection={async () => ({ ok: true })}
      onReloadRuntimeSettings={vi.fn()}
      onBrowseCompilerFile={vi.fn()}
      onBrowseOutputDirectory={vi.fn()}
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
    />,
  )
}

describe('SettingsDrawer AI model settings', () => {
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
})
