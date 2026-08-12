import { lazy, Suspense, useEffect, useState } from 'react'
import { BottomDrawer } from '../components/BottomDrawer'
import { TopMenu } from '../components/TopMenu'
import { useThemedDialog } from '../components/ThemedDialog'
import { detectChatIntent } from '../state/chatIntent'
import { useT } from '../i18n'
import type { AssistantImageAttachment, CompileIssue } from '../api/types'
import { groupParameters } from '../state/parameterGroups'
import { useUiPrefsStore } from '../state/uiPrefsStore'
import { useWorkbenchStore } from '../state/useWorkbenchStore'
import { ResizableWorkspaceGrid } from './layout/ResizableWorkspaceGrid'
import { WorkbenchLeftRail } from './layout/WorkbenchLeftRail'
import { WorkbenchRightRail } from './layout/WorkbenchRightRail'
import { FloatingPreviewWindow } from './preview/FloatingPreviewWindow'
import { PreviewWorkspaceStage } from './preview/PreviewWorkspaceStage'
import { ProjectOpenControls } from './project/ProjectOpenControls'
import { useConfigAutoRefresh } from './useConfigAutoRefresh'

const RevisionPanel = lazy(() => import('./diagnostics/RevisionPanel').then((m) => ({ default: m.RevisionPanel })))
const SettingsDrawer = lazy(() => import('./settings/SettingsDrawer').then((m) => ({ default: m.SettingsDrawer })))

export function WorkbenchApp() {
  const locale = useUiPrefsStore((state) => state.locale)
  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  const { confirm, prompt, dialogNode } = useThemedDialog()

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [floatingPreviewOpen, setFloatingPreviewOpen] = useState(false)
  const [previewWorkspaceOpen, setPreviewWorkspaceOpen] = useState(false)
  const [editorFocus, setEditorFocus] = useState<{
    scriptName: string
    line: number | null
    /** 相关代码段末行（P1e）：整段亮显；单行定位时为 null */
    endLine: number | null
    token: number
  } | null>(null)
  const project = useWorkbenchStore((state) => state.project)
  const parameters = useWorkbenchStore((state) => state.parameters)
  const parameterIssues = useWorkbenchStore((state) => state.parameterIssues)
  const draftParameters = useWorkbenchStore((state) => state.draftParameters)
  const preview = useWorkbenchStore((state) => state.preview)
  const preview2d = useWorkbenchStore((state) => state.preview2d)
  const warnings = useWorkbenchStore((state) => state.warnings)
  const loading = useWorkbenchStore((state) => state.loading)
  const applying = useWorkbenchStore((state) => state.applying)
  const compiling = useWorkbenchStore((state) => state.compiling)
  const lastError = useWorkbenchStore((state) => state.lastError)
  const backendNotice = useWorkbenchStore((state) => state.backendNotice)
  const compileLog = useWorkbenchStore((state) => state.compileLog)
  const compilerSettings = useWorkbenchStore((state) => state.compilerSettings)
  const llmSettings = useWorkbenchStore((state) => state.llmSettings)
  const activeRailPanel = useWorkbenchStore((state) => state.activeRailPanel)
  const assistantBusy = useWorkbenchStore((state) => state.assistantBusy)
  const assistantMessages = useWorkbenchStore((state) => state.assistantMessages)
  const assistantDraftSeed = useWorkbenchStore((state) => state.assistantDraftSeed)
  const importAssistantHistory = useWorkbenchStore((state) => state.importAssistantHistory)
  const distillAssistantHistory = useWorkbenchStore((state) => state.distillAssistantHistory)
  const consumeAssistantDraftSeed = useWorkbenchStore((state) => state.consumeAssistantDraftSeed)
  const pendingPlan = useWorkbenchStore((state) => state.pendingPlan)
  const pendingSkillProposal = useWorkbenchStore((state) => state.pendingSkillProposal)
  const scripts = useWorkbenchStore((state) => state.scripts)
  const recentProjects = useWorkbenchStore((state) => state.recentProjects)
  const revisions = useWorkbenchStore((state) => state.revisions)
  const memoryStatus = useWorkbenchStore((state) => state.memoryStatus)
  const memoryLessons = useWorkbenchStore((state) => state.memoryLessons)
  const memorySkillPreview = useWorkbenchStore((state) => state.memorySkillPreview)
  const memoryBusy = useWorkbenchStore((state) => state.memoryBusy)
  const gitStatus = useWorkbenchStore((state) => state.gitStatus)
  const gitBusy = useWorkbenchStore((state) => state.gitBusy)
  const knowledgeStatus = useWorkbenchStore((state) => state.knowledgeStatus)
  const knowledgeBusy = useWorkbenchStore((state) => state.knowledgeBusy)
  const tapirStatus = useWorkbenchStore((state) => state.tapirStatus)
  const tapirBusy = useWorkbenchStore((state) => state.tapirBusy)
  const latestRevisionId = useWorkbenchStore((state) => state.latestRevisionId)
  const revisionLoading = useWorkbenchStore((state) => state.revisionLoading)
  const activeScriptName = useWorkbenchStore((state) => state.activeScriptName)
  const scriptContents = useWorkbenchStore((state) => state.scriptContents)
  const dirtyScripts = useWorkbenchStore((state) => state.dirtyScripts)
  const lastSavedAt = useWorkbenchStore((state) => state.lastSavedAt)
  const scriptSaving = useWorkbenchStore((state) => state.scriptSaving)
  const mockCompileResult = useWorkbenchStore((state) => state.mockCompileResult)
  const workspace = useWorkbenchStore((state) => state.workspace)
  const workspaceBusy = useWorkbenchStore((state) => state.workspaceBusy)
  const workspaceSearching = useWorkbenchStore((state) => state.workspaceSearching)
  const workspaceSearchQuery = useWorkbenchStore((state) => state.workspaceSearchQuery)
  const workspaceSearchHits = useWorkbenchStore((state) => state.workspaceSearchHits)
  const workspaceInitHint = useWorkbenchStore((state) => state.workspaceInitHint)
  const openWorkspace = useWorkbenchStore((state) => state.openWorkspace)
  const initWorkspace = useWorkbenchStore((state) => state.initWorkspace)
  const browseWorkspaceDirectory = useWorkbenchStore((state) => state.browseWorkspaceDirectory)
  const clearWorkspaceInitHint = useWorkbenchStore((state) => state.clearWorkspaceInitHint)
  const trashWorkspaceProject = useWorkbenchStore((state) => state.trashWorkspaceProject)
  const closeWorkspace = useWorkbenchStore((state) => state.closeWorkspace)
  const refreshWorkspace = useWorkbenchStore((state) => state.refreshWorkspace)
  const searchWorkspace = useWorkbenchStore((state) => state.searchWorkspace)
  const load = useWorkbenchStore((state) => state.load)
  const setDraftParameter = useWorkbenchStore((state) => state.setDraftParameter)
  const applyDraftParameters = useWorkbenchStore((state) => state.applyDraftParameters)
  const addProjectParameter = useWorkbenchStore((state) => state.addProjectParameter)
  const updateProjectParameter = useWorkbenchStore((state) => state.updateProjectParameter)
  const deleteProjectParameter = useWorkbenchStore((state) => state.deleteProjectParameter)
  const validateProjectParameters = useWorkbenchStore((state) => state.validateProjectParameters)
  const resetDraftParameters = useWorkbenchStore((state) => state.resetDraftParameters)
  const loadProjectPath = useWorkbenchStore((state) => state.loadProjectPath)
  const newProject = useWorkbenchStore((state) => state.newProject)
  const importGdlFile = useWorkbenchStore((state) => state.importGdlFile)
  const importGsmFile = useWorkbenchStore((state) => state.importGsmFile)
  const importBlenderScript = useWorkbenchStore((state) => state.importBlenderScript)
  const exportHsfProject = useWorkbenchStore((state) => state.exportHsfProject)
  const saveProject = useWorkbenchStore((state) => state.saveProject)
  const closeProject = useWorkbenchStore((state) => state.closeProject)
  const browseProjectDirectory = useWorkbenchStore((state) => state.browseProjectDirectory)
  const setCompilerSettings = useWorkbenchStore((state) => state.setCompilerSettings)
  const openConfig = useWorkbenchStore((state) => state.openConfig)
  const testLlmConnection = useWorkbenchStore((state) => state.testLlmConnection)
  const switchLlmModel = useWorkbenchStore((state) => state.switchLlmModel)
  const saveLlmApiKey = useWorkbenchStore((state) => state.saveLlmApiKey)
  const reloadRuntimeSettings = useWorkbenchStore((state) => state.reloadRuntimeSettings)
  const refreshTapirStatus = useWorkbenchStore((state) => state.refreshTapirStatus)
  const reloadTapirLibraries = useWorkbenchStore((state) => state.reloadTapirLibraries)
  const syncTapirSelection = useWorkbenchStore((state) => state.syncTapirSelection)
  const highlightTapirSelection = useWorkbenchStore((state) => state.highlightTapirSelection)
  const loadTapirParameters = useWorkbenchStore((state) => state.loadTapirParameters)
  const applyTapirParameters = useWorkbenchStore((state) => state.applyTapirParameters)
  const browseCompilerFile = useWorkbenchStore((state) => state.browseCompilerFile)
  const browseOutputDirectory = useWorkbenchStore((state) => state.browseOutputDirectory)
  const compileCurrentProject = useWorkbenchStore((state) => state.compileCurrentProject)
  const runMockCompile = useWorkbenchStore((state) => state.runMockCompile)
  const revealCompileOutput = useWorkbenchStore((state) => state.revealCompileOutput)
  const loadPreview3D = useWorkbenchStore((state) => state.loadPreview3D)
  const loadPreview2D = useWorkbenchStore((state) => state.loadPreview2D)
  const setActiveRailPanel = useWorkbenchStore((state) => state.setActiveRailPanel)
  const clearAssistantHistory = useWorkbenchStore((state) => state.clearAssistantHistory)
  const loadMemoryLessons = useWorkbenchStore((state) => state.loadMemoryLessons)
  const summarizeProjectMemory = useWorkbenchStore((state) => state.summarizeProjectMemory)
  const updateMemoryLesson = useWorkbenchStore((state) => state.updateMemoryLesson)
  const deleteMemoryLesson = useWorkbenchStore((state) => state.deleteMemoryLesson)
  const ignoreMemoryLesson = useWorkbenchStore((state) => state.ignoreMemoryLesson)
  const clearProjectMemory = useWorkbenchStore((state) => state.clearProjectMemory)
  const loadProjectGitStatus = useWorkbenchStore((state) => state.loadProjectGitStatus)
  const loadKnowledgeStatus = useWorkbenchStore((state) => state.loadKnowledgeStatus)
  const reloadKnowledge = useWorkbenchStore((state) => state.reloadKnowledge)
  const initializeProjectGit = useWorkbenchStore((state) => state.initializeProjectGit)
  const setProjectGitEnabled = useWorkbenchStore((state) => state.setProjectGitEnabled)
  const commitProjectGit = useWorkbenchStore((state) => state.commitProjectGit)
  const adoptAssistantMessageCode = useWorkbenchStore((state) => state.adoptAssistantMessageCode)
  const sendChat = useWorkbenchStore((state) => state.sendChat)
  const t = useT()

  // P0-C：有项目打开时的"生成"意图先确认（建筑基础_v1 事故：
  // "参考图1生成坐斗"被当成改当前项目，全文重写覆盖了打开的项目）。
  // 新建项目不会改动当前项目文件；想改当前项目可取消后改用修改类表述。
  async function handleChat(message: string, images?: AssistantImageAttachment[]) {
    if (project && detectChatIntent(message, true) === 'create') {
      const ok = await confirm({
        title: t('chat.confirmCreateTitle'),
        message: t('chat.confirmCreateMessage', { name: project.name || '' }),
        confirmLabel: t('chat.confirmCreateOk'),
      })
      if (!ok) return
    }
    await sendChat(message, images)
  }
  const confirmPendingPlan = useWorkbenchStore((state) => state.confirmPendingPlan)
  const confirmPendingSkillProposal = useWorkbenchStore((state) => state.confirmPendingSkillProposal)
  const stopChat = useWorkbenchStore((state) => state.stopChat)
  const interruptedContext = useWorkbenchStore((state) => state.interruptedContext)
  const openScript = useWorkbenchStore((state) => state.openScript)
  const updateActiveScriptContent = useWorkbenchStore((state) => state.updateActiveScriptContent)
  const saveActiveScript = useWorkbenchStore((state) => state.saveActiveScript)
  const saveRevision = useWorkbenchStore((state) => state.saveRevision)
  const restoreRevision = useWorkbenchStore((state) => state.restoreRevision)
  const clearLastError = useWorkbenchStore((state) => state.clearLastError)
  const hasDraftChanges = useWorkbenchStore((state) => state.hasDraftChanges)
  const grouped = groupParameters(parameters)
  const activeScriptContent = activeScriptName ? scriptContents[activeScriptName] ?? '' : ''
  const hasDirtyScript = activeScriptName ? Boolean(dirtyScripts[activeScriptName]) : false
  const hasAnyDirtyScript = Object.values(dirtyScripts).some(Boolean)
  const activeFocusLine = editorFocus?.scriptName === activeScriptName ? editorFocus.line : null
  const activeFocusEndLine = editorFocus?.scriptName === activeScriptName ? editorFocus.endLine : null
  const activeFocusKey = editorFocus?.scriptName === activeScriptName ? editorFocus.token : null

  useEffect(() => {
    void load()
  }, [load])

  // Keep the active model in sync when config.toml is edited outside the app.
  useConfigAutoRefresh()

  useEffect(() => {
    if (activeRailPanel === 'inspect') {
      void refreshTapirStatus()
    }
  }, [activeRailPanel, refreshTapirStatus])

  // 打开/切换到有路径的项目时默认进入预览舞台（建筑师视角先看几何）；
  // 点开脚本时再切回编辑器舞台（见 openScriptInEditor）。
  const projectPath = project?.path ?? null
  useEffect(() => {
    if (projectPath) {
      setPreviewWorkspaceOpen(true)
    }
  }, [projectPath])

  function openScriptInEditor(scriptName: string) {
    setPreviewWorkspaceOpen(false)
    void openScript(scriptName)
  }

  function resetCurrentProject() {
    if (!project || loading) return
    const hasUnsavedDraft = hasAnyDirtyScript || hasDraftChanges()
    if (!hasUnsavedDraft) {
      void closeProject()
      return
    }
    void confirm({
      title: 'Reset current project',
      message: 'Reset current project? Unsaved script edits or parameter drafts will be discarded unless saved first.',
      danger: true,
    }).then((ok) => {
      if (ok) void closeProject()
    })
  }

  function hasMeaningfulProjectContent() {
    return Object.values(scriptContents).some((content) => content.trim().length > 0)
  }

  async function confirmDiscardUnsavedChanges(action: string) {
    const hasUnsavedDraft = hasAnyDirtyScript || hasDraftChanges()
    if (!hasUnsavedDraft) return true
    return confirm({
      title: action,
      message: `${action}? Unsaved script edits or parameter drafts will be discarded unless saved first.`,
    })
  }

  async function createNewProject() {
    if (loading || !(await confirmDiscardUnsavedChanges('Create a new project'))) return
    void newProject()
  }

  async function saveProjectAsWithPrompt() {
    if (!project) return
    if (!project.path && !hasMeaningfulProjectContent()) {
      window.alert('Nothing to save yet. Add GDL code or generate an object first.')
      return
    }
    const name = await prompt({ title: 'Project name', defaultValue: project.name || 'Untitled GDL Object' })
    if (name === null) return
    const cleanedName = name.trim()
    if (!cleanedName) {
      window.alert('Project name is required.')
      return
    }
    await exportHsfProject('', cleanedName)
  }

  async function saveCurrentProject() {
    if (!project || loading) return
    if (hasDirtyScript) {
      await saveActiveScript()
    }
    if (!project.path) {
      await saveProjectAsWithPrompt()
      return
    }
    await saveProject()
  }

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const isResetShortcut = (event.metaKey || event.ctrlKey) && event.shiftKey && event.code === 'KeyR'
      if (!isResetShortcut) return
      event.preventDefault()
      event.stopPropagation()
      resetCurrentProject()
    }

    window.addEventListener('keydown', handleShortcut, true)
    return () => window.removeEventListener('keydown', handleShortcut, true)
  }, [project, loading, hasAnyDirtyScript, dirtyScripts, draftParameters, closeProject])

  function focusDiagnosticIssue(issue: CompileIssue, endLine?: number | null) {
    const scriptName = issue.script.split('/').pop() ?? issue.script
    if (!scriptName) return
    openScriptInEditor(scriptName)
    setEditorFocus({
      scriptName,
      line: issue.line && issue.line > 0 ? issue.line : null,
      // 诊断跳转不带 endLine → 单行定位，行为不变（P1e）
      endLine: endLine ?? null,
      token: Date.now(),
    })
  }

  return (
    <main className="app-shell">
      <TopMenu
        project={project}
        projectControls={
          <ProjectOpenControls
            project={project}
            loading={loading}
            recentProjects={recentProjects}
            onNewProject={createNewProject}
            onLoadProjectPath={(path) => void loadProjectPath(path)}
            onBrowseProjectDirectory={() => void browseProjectDirectory()}
            onImportGdlFile={() => void importGdlFile()}
            onImportGsmFile={() => void importGsmFile()}
            onImportBlenderScript={() => void importBlenderScript()}
            onSaveProjectAs={() => void saveProjectAsWithPrompt()}
          />
        }
        hasDraftChanges={hasDraftChanges()}
        currentModel={llmSettings.model}
        onApply={() => void applyDraftParameters()}
        onCompile={() => void compileCurrentProject()}
        onMockCompile={() => void runMockCompile()}
        onSave={() => void saveCurrentProject()}
        onOpenSettings={() => setSettingsOpen(true)}
        applying={applying}
        loading={loading}
        compiling={compiling}
        saving={scriptSaving}
        hasDirtyScript={hasDirtyScript}
        lastSavedAt={lastSavedAt}
        lastError={lastError}
        backendNotice={backendNotice}
        onClearError={clearLastError}
      />
      <ResizableWorkspaceGrid
        previewWorkspaceOpen={previewWorkspaceOpen}
        loading={loading}
        left={(
          <WorkbenchLeftRail
            workspace={workspace}
            workspaceBusy={workspaceBusy}
            workspaceInitHint={workspaceInitHint}
            workspaceSearching={workspaceSearching}
            workspaceSearchQuery={workspaceSearchQuery}
            workspaceSearchHits={workspaceSearchHits}
            onOpenWorkspace={(path) => void openWorkspace(path)}
            onInitWorkspace={(path) => void initWorkspace(path)}
            onCloseWorkspace={() => void closeWorkspace()}
            onRefreshWorkspace={() => void refreshWorkspace()}
            onSearchWorkspace={(query) => void searchWorkspace(query)}
            onBrowseDirectory={() => browseWorkspaceDirectory()}
            onDismissInitHint={clearWorkspaceInitHint}
            onTrashWorkspaceProject={(path) => void trashWorkspaceProject(path)}
            onSelectProjectPath={(path) => void loadProjectPath(path)}
            scripts={scripts}
            activeScriptName={activeScriptName}
            dirtyScripts={dirtyScripts}
            groupedParameters={grouped}
            parameterIssues={parameterIssues}
            draftParameters={draftParameters}
            applying={applying}
            onSelectScript={openScriptInEditor}
            onChangeParameter={(name, value) => void setDraftParameter(name, value)}
            onApplyParameters={() => void applyDraftParameters()}
            onResetParameters={resetDraftParameters}
            onAddParameter={addProjectParameter}
            onUpdateParameter={updateProjectParameter}
            onDeleteParameter={deleteProjectParameter}
            onValidateParameters={() => void validateProjectParameters()}
          />
        )}
        main={(
          <PreviewWorkspaceStage
            previewWorkspaceOpen={previewWorkspaceOpen}
            preview={preview}
            warnings={warnings}
            activeScriptName={activeScriptName}
            activeScriptContent={activeScriptContent}
            hasDirtyScript={hasDirtyScript}
            hasDirtyScripts={hasAnyDirtyScript}
            activeFocusLine={activeFocusLine}
            activeFocusEndLine={activeFocusEndLine}
            activeFocusKey={activeFocusKey}
            onCollapsePreview={() => setPreviewWorkspaceOpen(false)}
            onFloatPreview={() => setFloatingPreviewOpen(true)}
            onChangeScript={updateActiveScriptContent}
            onRefreshPreview={() => void loadPreview3D()}
            onRevealSource={(scriptName, lineNumber, endLine) => focusDiagnosticIssue({ script: scriptName, line: lineNumber, severity: 'error', message: '' }, endLine)}
          />
        )}
        right={(
          <WorkbenchRightRail
            activeRailPanel={activeRailPanel}
            preview={preview}
            preview2d={preview2d}
            warnings={warnings}
            hasDirtyScripts={hasAnyDirtyScript}
            tapirStatus={tapirStatus}
            tapirBusy={tapirBusy}
            assistantMessages={assistantMessages}
            assistantBusy={assistantBusy}
            pendingPlan={pendingPlan}
            onConfirmPlan={(approve) => void confirmPendingPlan(approve)}
            pendingSkillProposal={pendingSkillProposal}
            onConfirmSkillProposal={(approve) => void confirmPendingSkillProposal(approve)}
            onSetActiveRailPanel={setActiveRailPanel}
            onLoadPreview3D={() => void loadPreview3D()}
            onLoadPreview2D={() => void loadPreview2D()}
            onExpandPreview={() => setPreviewWorkspaceOpen(true)}
            onFloatPreview={() => setFloatingPreviewOpen(true)}
            onRefreshTapirStatus={() => void refreshTapirStatus()}
            onReloadTapirLibraries={() => void reloadTapirLibraries()}
            onSyncTapirSelection={() => void syncTapirSelection()}
            onHighlightTapirSelection={() => void highlightTapirSelection()}
            onLoadTapirParameters={() => void loadTapirParameters()}
            onApplyTapirParameters={() => void applyTapirParameters()}
            hasProject={!!project}
            interruptedContext={interruptedContext}
            onChat={(message, images) => void handleChat(message, images)}
            onStop={stopChat}
            onClearAssistantHistory={() => void clearAssistantHistory()}
            onAdoptAssistantCode={(index) => void adoptAssistantMessageCode(index)}
            onOpenScript={openScriptInEditor}
            onSaveRevision={(message) => void saveRevision(message)}
            onRevealLine={(scriptName, lineNumber, endLine) => focusDiagnosticIssue({ script: scriptName, line: lineNumber, severity: 'error', message: '' }, endLine ?? null)}
            modelOptions={llmSettings.model_options ?? []}
            currentModel={llmSettings.model}
            onModelChange={switchLlmModel}
            workspace={workspace}
            currentProjectPath={project?.path ?? null}
            onImportAssistantHistory={(sourcePath) => void importAssistantHistory(sourcePath)}
            draftSeed={assistantDraftSeed}
            onConsumeDraftSeed={consumeAssistantDraftSeed}
            onDistillAssistantHistory={() => void distillAssistantHistory()}
          />
        )}
      />
      <BottomDrawer
        warnings={warnings}
        compileLog={compileLog}
        mockCompileResult={mockCompileResult}
        compiling={compiling}
        onIssueSelect={focusDiagnosticIssue}
        onRevealOutput={(path) => void revealCompileOutput(path)}
        revisionPanel={
          <Suspense fallback={null}>
            <RevisionPanel
              revisions={revisions}
              latestRevisionId={latestRevisionId}
              loading={revisionLoading}
              onSave={(message) => void saveRevision(message)}
              onRestore={(revisionId) => void restoreRevision(revisionId)}
            />
          </Suspense>
        }
      />
      <FloatingPreviewWindow
        open={floatingPreviewOpen}
        preview={preview}
        warnings={warnings}
        hasDirtyScripts={hasAnyDirtyScript}
        onClose={() => setFloatingPreviewOpen(false)}
        onRevealSource={(scriptName, lineNumber, endLine) => focusDiagnosticIssue({ script: scriptName, line: lineNumber, severity: 'error', message: '' }, endLine)}
      />
      <Suspense fallback={null}>
      <SettingsDrawer
        open={settingsOpen}
        compilerSettings={compilerSettings}
        llmSettings={llmSettings}
        recentProjects={recentProjects}
        memoryStatus={memoryStatus}
        memoryLessons={memoryLessons}
        memorySkillPreview={memorySkillPreview}
        memoryBusy={memoryBusy}
        gitStatus={gitStatus}
        gitBusy={gitBusy}
        knowledgeStatus={knowledgeStatus}
        knowledgeBusy={knowledgeBusy}
        onClose={() => setSettingsOpen(false)}
        onCompilerSettingsChange={setCompilerSettings}
        onOpenConfig={() => void openConfig()}
        onTestLlmConnection={testLlmConnection}
        onModelChange={switchLlmModel}
        onSaveLlmApiKey={saveLlmApiKey}
        onReloadRuntimeSettings={reloadRuntimeSettings}
        onBrowseCompilerFile={browseCompilerFile}
        onBrowseOutputDirectory={browseOutputDirectory}
        onOpenProjectPath={(path) => void loadProjectPath(path)}
        onExportHsfProject={() => void exportHsfProject()}
        onResetCurrentProject={resetCurrentProject}
        onLoadProjectGitStatus={() => void loadProjectGitStatus()}
        onInitializeProjectGit={() => void initializeProjectGit()}
        onSetProjectGitEnabled={(enabled) => void setProjectGitEnabled(enabled)}
        onCommitProjectGit={(message) => void commitProjectGit(message)}
        onLoadKnowledgeStatus={() => void loadKnowledgeStatus()}
        onReloadKnowledge={() => void reloadKnowledge()}
        onLoadMemoryLessons={loadMemoryLessons}
        onSummarizeProjectMemory={summarizeProjectMemory}
        onUpdateMemoryLesson={updateMemoryLesson}
        onDeleteMemoryLesson={deleteMemoryLesson}
        onIgnoreMemoryLesson={ignoreMemoryLesson}
        onClearProjectMemory={clearProjectMemory}
      />
      </Suspense>
      {dialogNode}
    </main>
  )
}
