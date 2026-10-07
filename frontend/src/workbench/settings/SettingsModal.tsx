import { useEffect, useRef, useState } from 'react'
import type {
  CompilerSettings,
  DistilledLesson,
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
import { DistilledLessonsPanel } from './DistilledLessonsPanel'
import { GitSettingsPanel } from './GitSettingsPanel'
import { InterfaceSettingsPanel, interfaceSummary } from './InterfaceSettingsPanel'
import { KnowledgePanel } from './KnowledgePanel'
import { MemoryLessonsPanel } from './MemoryLessonsPanel'
import { SettingsPanel } from './SettingsPanel'
import { useSettingsDialog } from './useSettingsDialog'
import { WorkspaceSettingsPanel } from './WorkspaceSettingsPanel'

export type SettingsSectionId =
  | 'interface'
  | 'ai'
  | 'compiler'
  | 'workspace'
  | 'git'
  | 'memory'
  | 'lessons'
  | 'knowledge'

interface SettingsModalProps {
  open: boolean
  initialSection?: SettingsSectionId
  initialFocus?: 'visibility'
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  recentProjects: RecentProject[]
  memoryStatus: ProjectMemoryStatus | null
  memoryLessons: ErrorLesson[]
  memorySkillPreview: string
  memoryBusy: boolean
  distilledLessons: DistilledLesson[]
  distilledLessonsBusy: boolean
  distilledLessonsMessage: { kind: 'error' | 'info'; text: string } | null
  projectName: string | null
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
  /** 卡07：服务商管理接线（透传给 AiSettingsPanel） */
  providerManager?: import('./ProviderManagerPanel').ProviderManagerPanelProps
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
  onLoadDistilledLessons: () => void
  onDistillLessons: () => void
  onSetDistilledLessonStatus: (fingerprint: string, decision: 'promote' | 'reject' | 'demote') => void
}

export function SettingsModal({
  open,
  initialSection = 'ai',
  initialFocus,
  compilerSettings,
  llmSettings,
  recentProjects,
  memoryStatus,
  memoryLessons,
  memorySkillPreview,
  memoryBusy,
  distilledLessons,
  distilledLessonsBusy,
  distilledLessonsMessage,
  projectName,
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
  providerManager,
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
  onLoadDistilledLessons,
  onDistillLessons,
  onSetDistilledLessonStatus,
}: SettingsModalProps) {
  const t = useT()
  const locale = useUiPrefsStore((state) => state.locale)
  const setLocale = useUiPrefsStore((state) => state.setLocale)
  const [compilerDraft, setCompilerDraft] = useState(compilerSettings)
  const [settingsSaveState, setSettingsSaveState] = useState<'saved' | 'dirty' | 'saving' | null>(null)
  const [settingsSaveError, setSettingsSaveError] = useState('')
  const [gitMessage, setGitMessage] = useState('OpenBrep HSF checkpoint')
  const [activeSection, setActiveSection] = useState<SettingsSectionId>(initialSection)
  const [visitedSections, setVisitedSections] = useState<Set<SettingsSectionId>>(() => new Set([initialSection]))
  const dialogRef = useSettingsDialog(open, onClose)
  const navRef = useRef<HTMLDivElement>(null)
  const wasOpenRef = useRef(false)
  const isCompilerDirty = compilerDirty(compilerDraft, compilerSettings)

  useEffect(() => {
    setCompilerDraft(compilerSettings)
  }, [compilerSettings])

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setSettingsSaveState(null)
      setActiveSection(initialSection)
      setVisitedSections((previous) => new Set([...previous, initialSection]))
      onLoadMemoryLessons()
      onLoadDistilledLessons()
      onLoadProjectGitStatus()
      onLoadKnowledgeStatus()
    }
    wasOpenRef.current = open
  }, [open, initialSection, onLoadMemoryLessons, onLoadDistilledLessons, onLoadProjectGitStatus, onLoadKnowledgeStatus])

  function selectSection(id: SettingsSectionId) {
    setActiveSection(id)
    setVisitedSections((previous) => new Set([...previous, id]))
  }

  useEffect(() => {
    if (!open || activeSection !== 'ai' || initialFocus !== 'visibility') return
    const frame = requestAnimationFrame(() => {
      const target = dialogRef.current?.querySelector<HTMLElement>('[data-settings-focus="visibility"]')
      target?.scrollIntoView?.({ block: 'nearest' })
      target?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [open, activeSection, initialFocus, dialogRef])

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

  const sections: { id: SettingsSectionId; summary: string }[] = [
    { id: 'interface', summary: interfaceSummary(locale) },
    { id: 'ai', summary: aiSummary(t, llmSettings) },
    { id: 'compiler', summary: compilerSummary(t, compilerDraft) },
    { id: 'workspace', summary: workspaceSummary(t, recentProjects) },
    { id: 'git', summary: gitSummary(t, gitStatus) },
    { id: 'memory', summary: memorySummary(t, memoryStatus, memoryLessons.length) },
    { id: 'lessons', summary: lessonsSummary(t, distilledLessons) },
    { id: 'knowledge', summary: knowledgeSummary(t, knowledgeStatus) },
  ]

  if (!open) return null

  return (
    <div className="settings-modal-overlay" onClick={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <div className="settings-modal" role="dialog" aria-modal="true"
        aria-label={t('settings.header.drawerAriaLabel')} tabIndex={-1} ref={dialogRef}>
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
            <button type="button" className="settings-icon-btn" title={t('settings.header.closeTitle')}
              aria-label={t('settings.header.closeAriaLabel')} onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        <div className="settings-modal-body">
          <div className="settings-nav" role="tablist" aria-orientation="vertical"
            aria-label={t('settings.header.title')} ref={navRef}>
            {sections.map(({ id, summary }, index) => (
              <button key={id} type="button" role="tab" id={`settings-tab-${id}`}
                className={`settings-nav-item${activeSection === id ? ' active' : ''}`}
                aria-selected={activeSection === id} aria-controls={`settings-panel-${id}`}
                tabIndex={activeSection === id ? 0 : -1}
                onClick={() => selectSection(id)}
                onKeyDown={(event) => {
                  let next: number
                  if (event.key === 'ArrowDown') next = (index + 1) % sections.length
                  else if (event.key === 'ArrowUp') next = (index + sections.length - 1) % sections.length
                  else if (event.key === 'Home') next = 0
                  else if (event.key === 'End') next = sections.length - 1
                  else return
                  event.preventDefault()
                  selectSection(sections[next].id)
                  navRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
                }}>
                <strong>{t(`settings.section.${id}`)}</strong>
                <small>{summary}</small>
                {id === 'compiler' && isCompilerDirty ? <em>{t('settings.header.unsaved')}</em> : null}
              </button>
            ))}
          </div>
          <div className="settings-content">
            <SettingsPanel
              id="interface"
              title={t('settings.section.interface')}
              summary={interfaceSummary(locale)}
              active={activeSection === 'interface'}
              visited={visitedSections.has('interface')}
            >
              <InterfaceSettingsPanel locale={locale} onLocaleChange={setLocale} />
            </SettingsPanel>

            <SettingsPanel
              id="ai"
              title={t('settings.section.ai')}
              summary={aiSummary(t, llmSettings)}
              active={activeSection === 'ai'}
              visited={visitedSections.has('ai')}
            >
              <AiSettingsPanel
                llmSettings={llmSettings}
                onOpenConfig={onOpenConfig}
                onTestConnection={onTestLlmConnection}
                onModelChange={onModelChange}
                onSaveApiKey={onSaveLlmApiKey}
                providerManager={providerManager}
                onReloadRuntimeSettings={onReloadRuntimeSettings}
              />
            </SettingsPanel>

            <SettingsPanel
              id="compiler"
              title={t('settings.section.compiler')}
              summary={compilerSummary(t, compilerDraft)}
              modified={isCompilerDirty}
              active={activeSection === 'compiler'}
              visited={visitedSections.has('compiler')}
            >
              <CompilerSettingsPanel
                draft={compilerDraft}
                onChange={updateCompilerDraft}
                onBrowseCompilerFile={() => void browseCompilerDraft()}
                onBrowseOutputDirectory={() => void browseOutputDraft()}
              />
            </SettingsPanel>

            <SettingsPanel
              id="workspace"
              title={t('settings.section.workspace')}
              summary={workspaceSummary(t, recentProjects)}
              active={activeSection === 'workspace'}
              visited={visitedSections.has('workspace')}
            >
              <WorkspaceSettingsPanel
                recentProjects={recentProjects}
                onOpenProjectPath={onOpenProjectPath}
                onExportHsfProject={onExportHsfProject}
                onResetCurrentProject={onResetCurrentProject}
              />
            </SettingsPanel>

            <SettingsPanel
              id="git"
              title={t('settings.section.git')}
              summary={gitSummary(t, gitStatus)}
              active={activeSection === 'git'}
              visited={visitedSections.has('git')}
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
            </SettingsPanel>

            <SettingsPanel
              id="memory"
              title={t('settings.section.memory')}
              summary={memorySummary(t, memoryStatus, memoryLessons.length)}
              active={activeSection === 'memory'}
              visited={visitedSections.has('memory')}
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
            </SettingsPanel>

            <SettingsPanel
              id="lessons"
              title={t('settings.section.lessons')}
              summary={lessonsSummary(t, distilledLessons)}
              active={activeSection === 'lessons'}
              visited={visitedSections.has('lessons')}
            >
              <DistilledLessonsPanel
                lessons={distilledLessons}
                busy={distilledLessonsBusy}
                projectName={projectName}
                message={distilledLessonsMessage}
                onRefresh={onLoadDistilledLessons}
                onDistill={onDistillLessons}
                onSetStatus={onSetDistilledLessonStatus}
              />
            </SettingsPanel>

            <SettingsPanel
              id="knowledge"
              title={t('settings.section.knowledge')}
              summary={knowledgeSummary(t, knowledgeStatus)}
              active={activeSection === 'knowledge'}
              visited={visitedSections.has('knowledge')}
            >
              <KnowledgePanel
                status={knowledgeStatus}
                busy={knowledgeBusy}
                onRefresh={onLoadKnowledgeStatus}
                onReload={onReloadKnowledge}
              />
            </SettingsPanel>
          </div>
        </div>
      </div>
    </div>
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

function lessonsSummary(t: ReturnType<typeof useT>, lessons: DistilledLesson[]) {
  const pending = lessons.filter((lesson) => (lesson.status ?? 'proposed') === 'proposed').length
  return pending > 0
    ? t('settings.summary.lessonsPending', { count: pending })
    : t('settings.summary.lessonsNone')
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
