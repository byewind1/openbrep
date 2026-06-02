import { useEffect, useRef, useState } from 'react'
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
import { AiSettingsPanel } from './AiSettingsPanel'
import { CompilerSettingsPanel } from './CompilerSettingsPanel'
import { GeneralSettingsPanel } from './GeneralSettingsPanel'
import { GitSettingsPanel } from './GitSettingsPanel'
import { MemoryLessonsPanel } from './MemoryLessonsPanel'
import { SettingsSection } from './SettingsSection'
import { WorkspaceSettingsPanel } from './WorkspaceSettingsPanel'

const SETTINGS_DRAWER_DEFAULT_WIDTH = 430
const SETTINGS_DRAWER_MIN_WIDTH = 360
const SETTINGS_DRAWER_MAX_WIDTH = 760
const SETTINGS_DRAWER_VIEWPORT_MARGIN = 24
const SETTINGS_DRAWER_KEY_STEP = 24

type SettingsSectionId = 'general' | 'ai' | 'compiler' | 'workspace' | 'git' | 'memory' | 'advanced'

const DEFAULT_EXPANDED_SECTIONS: Record<SettingsSectionId, boolean> = {
  general: true,
  ai: true,
  compiler: false,
  workspace: false,
  git: false,
  memory: false,
  advanced: false,
}

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
  const [drawerWidth, setDrawerWidth] = useState(SETTINGS_DRAWER_DEFAULT_WIDTH)
  const [expandedSections, setExpandedSections] = useState(DEFAULT_EXPANDED_SECTIONS)
  const wasOpenRef = useRef(false)
  const resizeStartRef = useRef<{ pointerX: number; width: number } | null>(null)
  const isCompilerDirty = compilerDirty(compilerDraft, compilerSettings)
  const isLlmDirty = llmDirty(llmDraft, llmSettings)

  useEffect(() => {
    setLlmDraft(llmSettings)
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

  function toggleSection(id: string) {
    setExpandedSections((sections) => ({
      ...sections,
      [id]: !sections[id as SettingsSectionId],
    }))
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

        <SettingsSection
          id="general"
          title="General"
          summary="config.toml"
          expanded={expandedSections.general}
          onToggle={toggleSection}
        >
          <GeneralSettingsPanel
            configPath="config.toml"
            saveState={settingsSaveState}
            saveError={settingsSaveError}
            onReload={() => void reloadRuntimeSettings()}
            onSave={() => void saveSettings()}
          />
        </SettingsSection>

        <SettingsSection
          id="ai"
          title="AI"
          summary={aiSummary(llmDraft)}
          modified={isLlmDirty}
          expanded={expandedSections.ai}
          onToggle={toggleSection}
        >
          <AiSettingsPanel
            draft={llmDraft}
            testResult={llmTestResult}
            testing={llmTesting}
            onChange={updateLlmDraft}
            onTestConnection={() => void testLlmConnection()}
          />
        </SettingsSection>

        <SettingsSection
          id="compiler"
          title="Compiler"
          summary={compilerSummary(compilerDraft)}
          modified={isCompilerDirty}
          expanded={expandedSections.compiler}
          onToggle={toggleSection}
        >
          <CompilerSettingsPanel
            draft={compilerDraft}
            onChange={updateCompilerDraft}
            onBrowseCompilerFile={() => void browseCompilerDraft()}
            onBrowseOutputDirectory={() => void browseOutputDraft()}
          />
        </SettingsSection>

        <SettingsSection
          id="workspace"
          title="Workspace"
          summary={workspaceSummary(recentProjects)}
          expanded={expandedSections.workspace}
          onToggle={toggleSection}
        >
          <WorkspaceSettingsPanel
            recentProjects={recentProjects}
            onOpenProjectPath={onOpenProjectPath}
            onExportHsfProject={onExportHsfProject}
            onResetCurrentProject={onResetCurrentProject}
          />
        </SettingsSection>

        <SettingsSection
          id="git"
          title="Git"
          summary={gitSummary(gitStatus)}
          expanded={expandedSections.git}
          onToggle={toggleSection}
        >
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
        </SettingsSection>

        <SettingsSection
          id="memory"
          title="Memory"
          summary={memorySummary(memoryStatus, memoryLessons.length)}
          expanded={expandedSections.memory}
          onToggle={toggleSection}
        >
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
        </SettingsSection>

        <SettingsSection
          id="advanced"
          title="Advanced"
          summary="reserved"
          expanded={expandedSections.advanced}
          onToggle={toggleSection}
        >
          <span className="settings-empty">Reserved for later local runtime controls</span>
        </SettingsSection>
      </aside>
    </>
  )
}

function compilerSummary(settings: CompilerSettings) {
  return settings.mode === 'lp' ? 'LP' : 'Mock'
}

function aiSummary(settings: LlmSettings) {
  return settings.model || 'No model'
}

function workspaceSummary(recentProjects: RecentProject[]) {
  return `${recentProjects.length} recent`
}

function gitSummary(gitStatus: ProjectGitStatus | null) {
  if (!gitStatus?.initialized) return 'Not initialized'
  return gitStatus.enabled ? 'Enabled' : 'Disabled'
}

function memorySummary(memoryStatus: ProjectMemoryStatus | null, fallbackLessonCount: number) {
  const lessonCount = memoryStatus?.lesson_count ?? fallbackLessonCount
  return `${lessonCount} lessons`
}

function compilerDirty(a: CompilerSettings, b: CompilerSettings) {
  return a.mode !== b.mode || a.converter_path !== b.converter_path || a.output_dir !== b.output_dir
}

function llmDirty(a: LlmSettings, b: LlmSettings) {
  return (
    a.model !== b.model ||
    a.api_key !== b.api_key ||
    a.api_base !== b.api_base ||
    a.max_retries !== b.max_retries ||
    a.assistant_settings !== b.assistant_settings
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
