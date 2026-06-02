import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type {
  CompilerSettings,
  ErrorLesson,
  LlmConnectionTestResult,
  LlmSettings,
  ProjectGitStatus,
  ProjectMemoryStatus,
  RecentProject,
  UpdateMemoryLessonRequest,
} from '../../api/types'
import { GitSettingsPanel } from './GitSettingsPanel'
import { MemoryLessonsPanel } from './MemoryLessonsPanel'

const SETTINGS_DRAWER_DEFAULT_WIDTH = 430
const SETTINGS_DRAWER_MIN_WIDTH = 360
const SETTINGS_DRAWER_MAX_WIDTH = 760
const SETTINGS_DRAWER_VIEWPORT_MARGIN = 24
const SETTINGS_DRAWER_KEY_STEP = 24

interface SettingsDrawerProps {
  open: boolean
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  recentProjects: RecentProject[]
  memoryStatus: ProjectMemoryStatus | null
  memoryLessons: ErrorLesson[]
  memorySkillPreview: string
  memoryBusy: boolean
  gitStatus: ProjectGitStatus | null
  gitBusy: boolean
  onClose: () => void
  onCompilerSettingsChange: (settings: CompilerSettings) => Promise<CompilerSettings>
  onLlmSettingsChange: (settings: LlmSettings) => Promise<LlmSettings>
  onTestLlmConnection: (settings: LlmSettings) => Promise<LlmConnectionTestResult>
  onReloadRuntimeSettings: () => Promise<void>
  onBrowseCompilerFile: () => Promise<CompilerSettings | null>
  onBrowseOutputDirectory: () => Promise<CompilerSettings | null>
  onOpenProjectPath: (path: string) => void
  onExportHsfProject: () => void
  onResetCurrentProject: () => void
  onLoadProjectGitStatus: () => void
  onInitializeProjectGit: () => void
  onSetProjectGitEnabled: (enabled: boolean) => void
  onCommitProjectGit: (message: string) => void
  onLoadMemoryLessons: () => void
  onSummarizeProjectMemory: () => void
  onUpdateMemoryLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => void
  onDeleteMemoryLesson: (fingerprint: string) => void
  onIgnoreMemoryLesson: (fingerprint: string) => void
  onClearProjectMemory: () => void
}

export function SettingsDrawer({
  open,
  compilerSettings,
  llmSettings,
  recentProjects,
  memoryStatus,
  memoryLessons,
  memorySkillPreview,
  memoryBusy,
  gitStatus,
  gitBusy,
  onClose,
  onCompilerSettingsChange,
  onLlmSettingsChange,
  onTestLlmConnection,
  onReloadRuntimeSettings,
  onBrowseCompilerFile,
  onBrowseOutputDirectory,
  onOpenProjectPath,
  onExportHsfProject,
  onResetCurrentProject,
  onLoadProjectGitStatus,
  onInitializeProjectGit,
  onSetProjectGitEnabled,
  onCommitProjectGit,
  onLoadMemoryLessons,
  onSummarizeProjectMemory,
  onUpdateMemoryLesson,
  onDeleteMemoryLesson,
  onIgnoreMemoryLesson,
  onClearProjectMemory,
}: SettingsDrawerProps) {
  const [compilerDraft, setCompilerDraft] = useState(compilerSettings)
  const [llmDraft, setLlmDraft] = useState(llmSettings)
  const [llmTestResult, setLlmTestResult] = useState<LlmConnectionTestResult | null>(null)
  const [llmTesting, setLlmTesting] = useState(false)
  const [settingsSaveState, setSettingsSaveState] = useState<'saved' | 'dirty' | 'saving' | null>(null)
  const [settingsSaveError, setSettingsSaveError] = useState('')
  const [gitMessage, setGitMessage] = useState('OpenBrep HSF checkpoint')
  const [manualModelMode, setManualModelMode] = useState(false)
  const [drawerWidth, setDrawerWidth] = useState(SETTINGS_DRAWER_DEFAULT_WIDTH)
  const wasOpenRef = useRef(false)
  const resizeStartRef = useRef<{ pointerX: number; width: number } | null>(null)
  const customModelOptions = llmDraft.model_groups?.custom ?? []
  const officialModelOptions = llmDraft.model_groups?.official ?? []
  const groupedModelIds = new Set([...customModelOptions, ...officialModelOptions].map((option) => option.id))
  const fallbackModelOptions = (llmDraft.models ?? [])
    .filter((model) => model && !groupedModelIds.has(model))
    .map((model) => ({ id: model, label: model, kind: 'official' as const, provider: '', has_api_key: false }))
  const knownModelIds = new Set([...groupedModelIds, ...fallbackModelOptions.map((option) => option.id)])
  const allModelOptions = [...customModelOptions, ...officialModelOptions, ...fallbackModelOptions]
  const selectedModelMeta = allModelOptions.find((option) => option.id === llmDraft.model)
  const activeModelCategory = manualModelMode ? 'exact' : selectedModelMeta?.kind ?? (knownModelIds.has(llmDraft.model) ? 'official' : 'exact')
  const apiKeyHint =
    activeModelCategory === 'custom'
      ? 'Custom key saves to [[llm.custom_providers]] with its base URL.'
      : activeModelCategory === 'official'
        ? selectedModelMeta?.has_api_key
          ? 'Official key saves to [llm.provider_keys]. Leave blank to keep the stored provider key.'
          : 'Official key saves to [llm.provider_keys] for this provider.'
        : 'Exact model IDs use the entered key unless they match a configured custom provider.'
  const apiBaseHint =
    activeModelCategory === 'custom'
      ? 'Custom base URL is read from the selected custom provider.'
      : activeModelCategory === 'official'
        ? 'Leave empty for the native official endpoint; fill only for an endpoint override.'
        : 'Optional endpoint override for OpenAI-compatible routes.'
  const visibleModelOptions =
    activeModelCategory === 'custom'
      ? customModelOptions
      : activeModelCategory === 'official'
        ? [...officialModelOptions, ...fallbackModelOptions]
        : []

  useEffect(() => {
    setLlmDraft(llmSettings)
    setManualModelMode(false)
  }, [llmSettings])

  useEffect(() => {
    setCompilerDraft(compilerSettings)
  }, [compilerSettings])

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setSettingsSaveState(null)
      onLoadMemoryLessons()
      onLoadProjectGitStatus()
    }
    wasOpenRef.current = open
  }, [open, onLoadMemoryLessons, onLoadProjectGitStatus])

  useEffect(() => {
    if (!open) {
      resizeStartRef.current = null
      return
    }

    setDrawerWidth((width) => clampSettingsDrawerWidth(width))

    function handlePointerMove(event: PointerEvent) {
      const resizeStart = resizeStartRef.current
      if (!resizeStart) {
        return
      }

      setDrawerWidth(clampSettingsDrawerWidth(resizeStart.width + resizeStart.pointerX - event.clientX))
    }

    function handlePointerUp() {
      resizeStartRef.current = null
    }

    function handleWindowResize() {
      setDrawerWidth((width) => clampSettingsDrawerWidth(width))
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('resize', handleWindowResize)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('resize', handleWindowResize)
    }
  }, [open])

  function submitLlmSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void saveSettings()
  }

  function updateCompilerDraft(settings: CompilerSettings) {
    setCompilerDraft(settings)
    setSettingsSaveError('')
    setSettingsSaveState('dirty')
  }

  function updateLlmDraft(settings: LlmSettings) {
    setLlmDraft(settings)
    setSettingsSaveError('')
    setSettingsSaveState('dirty')
  }

  async function saveSettings() {
    try {
      setSettingsSaveError('')
      setSettingsSaveState('saving')
      await onCompilerSettingsChange(compilerDraft)
      await onLlmSettingsChange(llmDraft)
      await onReloadRuntimeSettings()
      setSettingsSaveState('saved')
    } catch (error) {
      setSettingsSaveError(error instanceof Error ? error.message : 'Settings were not saved.')
      setSettingsSaveState('dirty')
    }
  }

  async function reloadRuntimeSettings() {
    setSettingsSaveError('')
    setSettingsSaveState(null)
    await onReloadRuntimeSettings()
  }

  async function browseCompilerDraft() {
    const selected = await onBrowseCompilerFile()
    if (selected) {
      updateCompilerDraft({ ...compilerDraft, converter_path: selected.converter_path })
    }
  }

  async function browseOutputDraft() {
    const selected = await onBrowseOutputDirectory()
    if (selected) {
      updateCompilerDraft({ ...compilerDraft, output_dir: selected.output_dir })
    }
  }

  async function testLlmConnection() {
    setLlmTesting(true)
    setLlmTestResult(null)
    const result = await onTestLlmConnection(llmDraft)
    setLlmTestResult(result)
    setLlmTesting(false)
  }

  function selectModelCategory(category: 'official' | 'custom' | 'exact') {
    if (category === 'custom') {
      const next = customModelOptions[0]
      if (next) {
        setManualModelMode(false)
        updateLlmDraft({ ...llmDraft, model: next.id, api_base: next.api_base ?? llmDraft.api_base })
      }
      return
    }
    if (category === 'official') {
      const next = officialModelOptions[0] ?? fallbackModelOptions[0]
      if (next) {
        setManualModelMode(false)
        updateLlmDraft({ ...llmDraft, model: next.id, api_key: '', api_base: '' })
      }
      return
    }
    setManualModelMode(true)
    updateLlmDraft({ ...llmDraft, model: selectedModelMeta?.id ?? llmDraft.model })
  }

  return (
    <>
      {open ? <button className="settings-scrim" type="button" aria-label="Close settings" onClick={onClose} /> : null}
      <aside
        className={`settings-drawer${open ? ' open' : ''}`}
        style={{ width: drawerWidth }}
        aria-hidden={!open}
        aria-label="Workbench settings"
      >
        <div
          className="settings-resize-handle"
          role="separator"
          aria-label="Resize settings panel"
          aria-orientation="vertical"
          aria-valuemin={SETTINGS_DRAWER_MIN_WIDTH}
          aria-valuemax={getSettingsDrawerMaxWidth()}
          aria-valuenow={drawerWidth}
          tabIndex={0}
          onPointerDown={(event) => {
            if (event.button !== 0) {
              return
            }
            resizeStartRef.current = { pointerX: event.clientX, width: drawerWidth }
            event.currentTarget.setPointerCapture?.(event.pointerId)
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') {
              event.preventDefault()
              setDrawerWidth((width) => clampSettingsDrawerWidth(width + SETTINGS_DRAWER_KEY_STEP))
            }
            if (event.key === 'ArrowRight') {
              event.preventDefault()
              setDrawerWidth((width) => clampSettingsDrawerWidth(width - SETTINGS_DRAWER_KEY_STEP))
            }
          }}
        />
        <div className="settings-header">
          <div>
            <strong>Settings</strong>
            <span>Workbench runtime</span>
          </div>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="settings-actions">
          <button type="button" onClick={() => void reloadRuntimeSettings()}>
            Reload config
          </button>
          <button type="button" className="primary-action" disabled={settingsSaveState === 'saving'} onClick={() => void saveSettings()}>
            {settingsSaveState === 'saving' ? 'Saving...' : 'Save Settings'}
          </button>
          {settingsSaveState ? (
            <span
              className={
                settingsSaveState === 'dirty'
                  ? 'settings-dirty-state'
                  : settingsSaveState === 'saving'
                    ? 'settings-saving-state'
                    : 'settings-saved-state'
              }
            >
              {settingsSaveState === 'dirty' ? 'Unsaved changes' : settingsSaveState === 'saving' ? 'Saving' : 'Saved'}
            </span>
          ) : null}
          {settingsSaveError ? <span className="settings-save-error">{settingsSaveError}</span> : null}
        </div>

        <section className="settings-section">
          <div className="settings-section-heading">
            <strong>Compiler</strong>
            <span>Mock or LP_XMLConverter</span>
          </div>
          <label className="settings-row">
            <span>Mode</span>
            <select
              aria-label="Compiler mode"
              value={compilerDraft.mode}
              onChange={(event) =>
                updateCompilerDraft({
                  ...compilerDraft,
                  mode: event.currentTarget.value === 'lp' ? 'lp' : 'mock',
                })
              }
            >
              <option value="mock">Mock</option>
              <option value="lp">LP</option>
            </select>
          </label>
          <label className="settings-field">
            <span>LP_XMLConverter</span>
            <div className="settings-path-row">
              <input
                type="text"
                placeholder="/Applications/.../LP_XMLConverter"
                value={compilerDraft.converter_path}
                disabled={compilerDraft.mode !== 'lp'}
                onChange={(event) =>
                  updateCompilerDraft({
                    ...compilerDraft,
                    converter_path: event.currentTarget.value,
                  })
                }
              />
              <button type="button" disabled={compilerDraft.mode !== 'lp'} onClick={() => void browseCompilerDraft()}>
                Browse
              </button>
            </div>
          </label>
          <label className="settings-field">
            <span>Output directory</span>
            <div className="settings-path-row">
              <input
                type="text"
                placeholder="Project sibling /output"
                value={compilerDraft.output_dir}
                onChange={(event) =>
                  updateCompilerDraft({
                    ...compilerDraft,
                    output_dir: event.currentTarget.value,
                  })
                }
              />
              <button type="button" onClick={() => void browseOutputDraft()}>
                Browse
              </button>
            </div>
          </label>
        </section>

        <form className="settings-section" onSubmit={submitLlmSettings}>
          <div className="settings-section-heading">
            <strong>AI</strong>
            <span>Model, endpoint and collaboration preference</span>
          </div>
          <div className="settings-field">
            <span>Model</span>
            <div className="settings-segmented" aria-label="AI source">
              <button
                type="button"
                className={activeModelCategory === 'official' ? 'active' : ''}
                disabled={!officialModelOptions.length && !fallbackModelOptions.length}
                onClick={() => selectModelCategory('official')}
              >
                Official
              </button>
              <button
                type="button"
                className={activeModelCategory === 'custom' ? 'active' : ''}
                disabled={!customModelOptions.length}
                onClick={() => selectModelCategory('custom')}
              >
                Custom
              </button>
              <button
                type="button"
                className={activeModelCategory === 'exact' ? 'active' : ''}
                onClick={() => selectModelCategory('exact')}
              >
                Exact ID
              </button>
            </div>
            <div className="settings-model-row">
              <div className="settings-model-list" role="listbox" aria-label="Model">
                {activeModelCategory === 'exact' ? (
                  <span className="settings-empty">Manual model ID</span>
                ) : (
                  visibleModelOptions.map((model) => (
                    <button
                      type="button"
                      role="option"
                      aria-selected={llmDraft.model === model.id}
                      className={llmDraft.model === model.id ? 'active' : ''}
                      onClick={() => {
                        updateLlmDraft({
                          ...llmDraft,
                          model: model.id,
                          api_key: model.kind === 'custom' ? llmDraft.api_key : '',
                          api_base: model.kind === 'custom' ? model.api_base ?? llmDraft.api_base : '',
                        })
                        setManualModelMode(false)
                      }}
                      key={model.id}
                    >
                      {model.kind === 'custom' ? `${model.label} (${model.provider})` : model.label}
                    </button>
                  ))
                )}
              </div>
              <input
                type="text"
                value={llmDraft.model}
                placeholder="Exact model id"
                onChange={(event) => {
                  setManualModelMode(true)
                  updateLlmDraft({ ...llmDraft, model: event.currentTarget.value })
                }}
              />
            </div>
            {selectedModelMeta ? (
              <small className="settings-field-hint">
                {activeModelCategory === 'exact'
                  ? 'Manual model ID'
                  : selectedModelMeta.kind === 'custom'
                  ? `Custom provider: ${selectedModelMeta.provider}${selectedModelMeta.protocol ? ` / ${selectedModelMeta.protocol}` : ''}`
                  : `Official provider: ${selectedModelMeta.provider || 'auto'}`}
              </small>
            ) : null}
          </div>
          <label className="settings-field">
            <span>API Key</span>
            <input
              type="password"
              value={llmDraft.api_key}
              placeholder="Provider API key"
              onChange={(event) => updateLlmDraft({ ...llmDraft, api_key: event.currentTarget.value })}
            />
            <small className="settings-field-hint">{apiKeyHint}</small>
          </label>
          <label className="settings-field">
            <span>API Base URL</span>
            <input
              type="text"
              value={llmDraft.api_base}
              placeholder="Optional endpoint override"
              onChange={(event) => updateLlmDraft({ ...llmDraft, api_base: event.currentTarget.value })}
            />
            <small className="settings-field-hint">{apiBaseHint}</small>
          </label>
          <label className="settings-row">
            <span>Max retries</span>
            <input
              type="number"
              min={1}
              max={10}
              value={llmDraft.max_retries}
              onChange={(event) =>
                updateLlmDraft({
                  ...llmDraft,
                  max_retries: Number(event.currentTarget.value),
                })
              }
            />
          </label>
          <label className="settings-field">
            <span>Assistant preference</span>
            <textarea
              value={llmDraft.assistant_settings}
              placeholder="例如：先解释再给最小修改；优先保证可编译；不要大改结构。"
              onChange={(event) => updateLlmDraft({ ...llmDraft, assistant_settings: event.currentTarget.value })}
            />
          </label>
          <div className="settings-submit-row">
            <button type="button" disabled={llmTesting} onClick={() => void testLlmConnection()}>
              {llmTesting ? 'Testing...' : 'Test connection'}
            </button>
          </div>
          {llmTestResult ? (
            <p className={`settings-test-result ${llmTestResult.ok ? 'success' : 'error'}`}>
              {llmTestResult.ok
                ? `${llmTestResult.message ?? 'LLM connection OK'}${llmTestResult.duration_ms !== undefined ? ` (${llmTestResult.duration_ms} ms)` : ''}`
                : `LLM settings error: ${llmTestResult.error ?? 'Connection test failed.'}`}
            </p>
          ) : null}
        </form>

        <section className="settings-section">
          <div className="settings-section-heading">
            <strong>Workspace</strong>
            <span>Recent HSF projects and current session</span>
          </div>
          <div className="recent-project-list">
            {recentProjects.length ? (
              recentProjects.map((project) => (
                <button
                  type="button"
                  className="recent-project-item"
                  disabled={!project.exists}
                  key={project.path}
                  onClick={() => onOpenProjectPath(project.path)}
                  title={project.path}
                >
                  <span>{project.name || project.path}</span>
                  {project.parent_dir ? <small>{project.parent_dir}</small> : null}
                  {!project.exists ? <em>missing</em> : null}
                </button>
              ))
            ) : (
              <span className="settings-empty">No recent HSF projects</span>
            )}
          </div>
          <div className="settings-submit-row">
            <button type="button" onClick={onExportHsfProject}>
              Export HSF
            </button>
            <button
              type="button"
              onClick={onResetCurrentProject}
              title="Reset the current workbench session without deleting files on disk"
            >
              Reset Current Project
            </button>
          </div>
        </section>

        <GitSettingsPanel
          gitStatus={gitStatus}
          gitBusy={gitBusy}
          message={gitMessage}
          onMessageChange={setGitMessage}
          onRefresh={onLoadProjectGitStatus}
          onInitialize={onInitializeProjectGit}
          onSetEnabled={onSetProjectGitEnabled}
          onCommit={onCommitProjectGit}
        />

        <MemoryLessonsPanel
          memoryStatus={memoryStatus}
          lessons={memoryLessons}
          skillPreview={memorySkillPreview}
          busy={memoryBusy}
          formatBytes={formatBytes}
          onRefresh={onLoadMemoryLessons}
          onSummarize={onSummarizeProjectMemory}
          onUpdateLesson={onUpdateMemoryLesson}
          onDeleteLesson={onDeleteMemoryLesson}
          onIgnoreLesson={onIgnoreMemoryLesson}
          onClear={onClearProjectMemory}
        />

        <section className="settings-section muted">
          <div className="settings-section-heading">
            <strong>Advanced</strong>
            <span>Reserved for later local runtime controls</span>
          </div>
        </section>
      </aside>
    </>
  )
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function clampSettingsDrawerWidth(width: number, viewportWidth = getViewportWidth()) {
  const viewportMax = Math.max(280, viewportWidth - SETTINGS_DRAWER_VIEWPORT_MARGIN)
  const minWidth = Math.min(SETTINGS_DRAWER_MIN_WIDTH, viewportMax)
  const maxWidth = Math.max(minWidth, Math.min(SETTINGS_DRAWER_MAX_WIDTH, viewportMax))
  return Math.min(Math.max(width, minWidth), maxWidth)
}

function getSettingsDrawerMaxWidth() {
  return Math.max(SETTINGS_DRAWER_MIN_WIDTH, Math.min(SETTINGS_DRAWER_MAX_WIDTH, getViewportWidth() - SETTINGS_DRAWER_VIEWPORT_MARGIN))
}

function getViewportWidth() {
  return typeof window === 'undefined' ? 1024 : window.innerWidth
}
