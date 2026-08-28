import { useEffect, useRef, useState } from 'react'
import type {
  CompilerSettings,
  ErrorLesson,
  KnowledgeStatus,
  LlmConnectionTestResult,
  LlmSettings,
  ProjectGitStatus,
  ProjectMemoryStatus,
  RecentProject,
  UpdateMemoryLessonRequest,
} from '../../api/types'
import { useT } from '../../i18n'
import { useUiPrefsStore } from '../../state/uiPrefsStore'
import { AiSettingsPanel } from './AiSettingsPanel'
import { CompilerSettingsPanel } from './CompilerSettingsPanel'
import { GitSettingsPanel } from './GitSettingsPanel'
import { InterfaceSettingsPanel, interfaceSummary } from './InterfaceSettingsPanel'
import { KnowledgePanel } from './KnowledgePanel'
import { MemoryLessonsPanel } from './MemoryLessonsPanel'
import { SettingsSection } from './SettingsSection'
import { WorkspaceSettingsPanel } from './WorkspaceSettingsPanel'

const SETTINGS_DRAWER_DEFAULT_WIDTH = 430
const SETTINGS_DRAWER_MIN_WIDTH = 360
const SETTINGS_DRAWER_MAX_WIDTH = 760
const SETTINGS_DRAWER_VIEWPORT_MARGIN = 24
const SETTINGS_DRAWER_KEY_STEP = 24

type SettingsSectionId = 'interface' | 'ai' | 'compiler' | 'workspace' | 'git' | 'memory' | 'knowledge'

const DEFAULT_EXPANDED_SECTIONS: Record<SettingsSectionId, boolean> = {
  interface: false,
  ai: true,
  compiler: false,
  workspace: false,
  git: false,
  memory: false,
  knowledge: false,
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
  knowledgeStatus: KnowledgeStatus | null
  knowledgeBusy: boolean
  onClose: () => void
  onCompilerSettingsChange: (settings: CompilerSettings) => Promise<CompilerSettings>
  onOpenConfig: () => void
  onTestLlmConnection: () => Promise<LlmConnectionTestResult>
  onModelChange?: (
    model: string,
    reasoningEffort?: string,
    codexRoutingMode?: 'fixed' | 'auto',
  ) => Promise<void>
  onSaveLlmApiKey?: (model: string, apiKey: string) => Promise<unknown>
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
  onLoadKnowledgeStatus: () => void
  onReloadKnowledge: () => void
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
  knowledgeStatus,
  knowledgeBusy,
  onClose,
  onCompilerSettingsChange,
  onOpenConfig,
  onTestLlmConnection,
  onModelChange,
  onSaveLlmApiKey,
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
  onLoadKnowledgeStatus,
  onReloadKnowledge,
  onLoadMemoryLessons,
  onSummarizeProjectMemory,
  onUpdateMemoryLesson,
  onDeleteMemoryLesson,
  onIgnoreMemoryLesson,
  onClearProjectMemory,
}: SettingsDrawerProps) {
  const t = useT()
  const locale = useUiPrefsStore((state) => state.locale)
  const setLocale = useUiPrefsStore((state) => state.setLocale)
  const [compilerDraft, setCompilerDraft] = useState(compilerSettings)
  const [settingsSaveState, setSettingsSaveState] = useState<'saved' | 'dirty' | 'saving' | null>(null)
  const [settingsSaveError, setSettingsSaveError] = useState('')
  const [gitMessage, setGitMessage] = useState('OpenBrep HSF checkpoint')
  const [drawerWidth, setDrawerWidth] = useState(SETTINGS_DRAWER_DEFAULT_WIDTH)
  const [expandedSections, setExpandedSections] = useState(DEFAULT_EXPANDED_SECTIONS)
  const wasOpenRef = useRef(false)
  const resizeStartRef = useRef<{ pointerX: number; width: number } | null>(null)
  const isCompilerDirty = compilerDirty(compilerDraft, compilerSettings)

  useEffect(() => {
    setCompilerDraft(compilerSettings)
  }, [compilerSettings])

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setSettingsSaveState(null)
      onLoadMemoryLessons()
      onLoadProjectGitStatus()
      onLoadKnowledgeStatus()
    }
    wasOpenRef.current = open
  }, [open, onLoadMemoryLessons, onLoadProjectGitStatus, onLoadKnowledgeStatus])

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

  async function saveSettings() {
    try {
      setSettingsSaveError('')
      setSettingsSaveState('saving')
      await onCompilerSettingsChange(compilerDraft)
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

  return (
    <>
      {open ? (
        <button
          className="settings-scrim"
          type="button"
          aria-label={t('settings.header.closeAriaLabel')}
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`settings-drawer${open ? ' open' : ''}`}
        style={{ width: drawerWidth }}
        aria-hidden={!open}
        aria-label={t('settings.header.drawerAriaLabel')}
      >
        <div
          className="settings-resize-handle"
          role="separator"
          aria-label={t('settings.header.resizeAriaLabel')}
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
            <strong>{t('settings.header.title')}</strong>
            <span>config.toml</span>
          </div>
          <div className="settings-header-actions">
            {settingsSaveState === 'saving' && (
              <span className="settings-saving-state">{t('settings.header.saving')}</span>
            )}
            {settingsSaveState === 'dirty' && (
              <span className="settings-dirty-state">{t('settings.header.unsaved')}</span>
            )}
            {settingsSaveState === 'saved' && (
              <span className="settings-saved-state">{t('settings.header.saved')}</span>
            )}
            {settingsSaveError && (
              <span className="settings-save-error" title={settingsSaveError}>
                {t('settings.header.error')}
              </span>
            )}
            <button
              type="button"
              className="settings-icon-btn"
              title={t('settings.header.reloadTitle')}
              onClick={() => void reloadRuntimeSettings()}
            >
              ↺
            </button>
            <button
              type="button"
              className="settings-save-btn"
              disabled={settingsSaveState === 'saving'}
              onClick={() => void saveSettings()}
            >
              {settingsSaveState === 'saving' ? '…' : t('settings.header.saveButton')}
            </button>
            <button type="button" className="settings-icon-btn" title={t('settings.header.closeTitle')} onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        <SettingsSection
          id="interface"
          title={t('settings.section.interface')}
          summary={interfaceSummary(locale)}
          expanded={expandedSections.interface}
          onToggle={toggleSection}
        >
          <InterfaceSettingsPanel locale={locale} onLocaleChange={setLocale} />
        </SettingsSection>

        <SettingsSection
          id="ai"
          title={t('settings.section.ai')}
          summary={aiSummary(t, llmSettings)}
          expanded={expandedSections.ai}
          onToggle={toggleSection}
        >
          <AiSettingsPanel
            llmSettings={llmSettings}
            onOpenConfig={onOpenConfig}
            onTestConnection={onTestLlmConnection}
            onModelChange={onModelChange}
            onSaveApiKey={onSaveLlmApiKey}
          />
        </SettingsSection>

        <SettingsSection
          id="compiler"
          title={t('settings.section.compiler')}
          summary={compilerSummary(t, compilerDraft)}
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
          title={t('settings.section.workspace')}
          summary={workspaceSummary(t, recentProjects)}
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
          title={t('settings.section.git')}
          summary={gitSummary(t, gitStatus)}
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
          title={t('settings.section.memory')}
          summary={memorySummary(t, memoryStatus, memoryLessons.length)}
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
          id="knowledge"
          title={t('settings.section.knowledge')}
          summary={knowledgeSummary(t, knowledgeStatus)}
          expanded={expandedSections.knowledge}
          onToggle={toggleSection}
        >
          <KnowledgePanel
            status={knowledgeStatus}
            busy={knowledgeBusy}
            onRefresh={onLoadKnowledgeStatus}
            onReload={onReloadKnowledge}
          />
        </SettingsSection>

      </aside>
    </>
  )
}

function compilerSummary(t: ReturnType<typeof useT>, settings: CompilerSettings) {
  return settings.mode === 'lp' ? t('settings.summary.compilerLp') : t('settings.summary.compilerMock')
}

function aiSummary(t: ReturnType<typeof useT>, settings: LlmSettings) {
  return settings.model || t('settings.summary.aiNoModel')
}

function workspaceSummary(t: ReturnType<typeof useT>, recentProjects: RecentProject[]) {
  return t('settings.summary.workspaceRecentCount', { count: recentProjects.length })
}

function gitSummary(t: ReturnType<typeof useT>, gitStatus: ProjectGitStatus | null) {
  if (!gitStatus?.initialized) return t('settings.summary.gitNotInitialized')
  return gitStatus.enabled ? t('settings.summary.gitEnabled') : t('settings.summary.gitDisabled')
}

function memorySummary(
  t: ReturnType<typeof useT>,
  memoryStatus: ProjectMemoryStatus | null,
  fallbackLessonCount: number,
) {
  const lessonCount = memoryStatus?.lesson_count ?? fallbackLessonCount
  return t('settings.summary.memoryLessonCount', { count: lessonCount })
}

function knowledgeSummary(t: ReturnType<typeof useT>, status: KnowledgeStatus | null) {
  if (!status?.ok) return t('settings.summary.knowledgeDash')
  return status.has_pro
    ? t('settings.summary.knowledgeFreePro', { free: status.free_doc_count, pro: status.pro_doc_count })
    : t('settings.summary.knowledgeFreeNoPro', { free: status.free_doc_count })
}

function compilerDirty(a: CompilerSettings, b: CompilerSettings) {
  return a.mode !== b.mode || a.converter_path !== b.converter_path || a.output_dir !== b.output_dir
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
