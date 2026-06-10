import { useEffect, useState } from 'react'
import { BottomDrawer } from '../components/BottomDrawer'
import { TopMenu } from '../components/TopMenu'
import type { CompileIssue } from '../api/types'
import { groupParameters } from '../state/parameterGroups'
import { useWorkbenchStore } from '../state/useWorkbenchStore'
import { RevisionPanel } from './diagnostics/RevisionPanel'
import { ResizableWorkspaceGrid } from './layout/ResizableWorkspaceGrid'
import { WorkbenchLeftRail } from './layout/WorkbenchLeftRail'
import { WorkbenchRightRail } from './layout/WorkbenchRightRail'
import { FloatingPreviewWindow } from './preview/FloatingPreviewWindow'
import { PreviewWorkspaceStage } from './preview/PreviewWorkspaceStage'
import { ProjectOpenControls } from './project/ProjectOpenControls'
import { SettingsDrawer } from './settings/SettingsDrawer'

export function WorkbenchApp() {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [floatingPreviewOpen, setFloatingPreviewOpen] = useState(false)
  const [previewWorkspaceOpen, setPreviewWorkspaceOpen] = useState(false)
  const [editorFocus, setEditorFocus] = useState<{ scriptName: string; line: number | null; token: number } | null>(null)
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
  const compileLog = useWorkbenchStore((state) => state.compileLog)
  const compilerSettings = useWorkbenchStore((state) => state.compilerSettings)
  const llmSettings = useWorkbenchStore((state) => state.llmSettings)
  const activeRailPanel = useWorkbenchStore((state) => state.activeRailPanel)
  const assistantBusy = useWorkbenchStore((state) => state.assistantBusy)
  const assistantMessages = useWorkbenchStore((state) => state.assistantMessages)
  const scripts = useWorkbenchStore((state) => state.scripts)
  const recentProjects = useWorkbenchStore((state) => state.recentProjects)
  const revisions = useWorkbenchStore((state) => state.revisions)
  const memoryStatus = useWorkbenchStore((state) => state.memoryStatus)
  const memoryLessons = useWorkbenchStore((state) => state.memoryLessons)
  const memorySkillPreview = useWorkbenchStore((state) => state.memorySkillPreview)
  const memoryBusy = useWorkbenchStore((state) => state.memoryBusy)
  const gitStatus = useWorkbenchStore((state) => state.gitStatus)
  const gitBusy = useWorkbenchStore((state) => state.gitBusy)
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
  const exportHsfProject = useWorkbenchStore((state) => state.exportHsfProject)
  const saveProject = useWorkbenchStore((state) => state.saveProject)
  const closeProject = useWorkbenchStore((state) => state.closeProject)
  const browseProjectDirectory = useWorkbenchStore((state) => state.browseProjectDirectory)
  const setCompilerSettings = useWorkbenchStore((state) => state.setCompilerSettings)
  const openConfig = useWorkbenchStore((state) => state.openConfig)
  const testLlmConnection = useWorkbenchStore((state) => state.testLlmConnection)
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
  const initializeProjectGit = useWorkbenchStore((state) => state.initializeProjectGit)
  const setProjectGitEnabled = useWorkbenchStore((state) => state.setProjectGitEnabled)
  const commitProjectGit = useWorkbenchStore((state) => state.commitProjectGit)
  const adoptAssistantMessageCode = useWorkbenchStore((state) => state.adoptAssistantMessageCode)
  const sendAssistantMessage = useWorkbenchStore((state) => state.sendAssistantMessage)
  const createProjectFromPrompt = useWorkbenchStore((state) => state.createProjectFromPrompt)
  const generateAssistantChanges = useWorkbenchStore((state) => state.generateAssistantChanges)
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
  const activeFocusKey = editorFocus?.scriptName === activeScriptName ? editorFocus.token : null

  useEffect(() => {
    void load()
  }, [load])

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
    if (
      hasUnsavedDraft &&
      !window.confirm('Reset current project? Unsaved script edits or parameter drafts will be discarded unless saved first.')
    ) {
      return
    }
    void closeProject()
  }

  function hasMeaningfulProjectContent() {
    return Object.values(scriptContents).some((content) => content.trim().length > 0)
  }

  function confirmDiscardUnsavedChanges(action: string) {
    const hasUnsavedDraft = hasAnyDirtyScript || hasDraftChanges()
    return !hasUnsavedDraft || window.confirm(`${action}? Unsaved script edits or parameter drafts will be discarded unless saved first.`)
  }

  function createNewProject() {
    if (loading || !confirmDiscardUnsavedChanges('Create a new project')) return
    void newProject()
  }

  async function saveProjectAsWithPrompt() {
    if (!project) return
    if (!project.path && !hasMeaningfulProjectContent()) {
      window.alert('Nothing to save yet. Add GDL code or generate an object first.')
      return
    }
    const name = window.prompt('Project name', project.name || 'Untitled GDL Object')
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

  function focusDiagnosticIssue(issue: CompileIssue) {
    const scriptName = issue.script.split('/').pop() ?? issue.script
    if (!scriptName) return
    openScriptInEditor(scriptName)
    setEditorFocus({
      scriptName,
      line: issue.line && issue.line > 0 ? issue.line : null,
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
            onSaveProjectAs={() => void saveProjectAsWithPrompt()}
          />
        }
        hasDraftChanges={hasDraftChanges()}
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
        onClearError={clearLastError}
      />
      <ResizableWorkspaceGrid
        previewWorkspaceOpen={previewWorkspaceOpen}
        loading={loading}
        left={(
          <WorkbenchLeftRail
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
            activeFocusKey={activeFocusKey}
            onCollapsePreview={() => setPreviewWorkspaceOpen(false)}
            onFloatPreview={() => setFloatingPreviewOpen(true)}
            onChangeScript={updateActiveScriptContent}
            onRefreshPreview={() => void loadPreview3D()}
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
            onSendAssistantMessage={(message) => void sendAssistantMessage(message)}
            onCreateProjectFromPrompt={(message, image) => void createProjectFromPrompt(message, image)}
            onGenerateAssistantChanges={(message, image) => void generateAssistantChanges(message, image)}
            onClearAssistantHistory={() => void clearAssistantHistory()}
            onAdoptAssistantCode={(index) => void adoptAssistantMessageCode(index)}
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
          <RevisionPanel
            revisions={revisions}
            latestRevisionId={latestRevisionId}
            loading={revisionLoading}
            onSave={(message) => void saveRevision(message)}
            onRestore={(revisionId) => void restoreRevision(revisionId)}
          />
        }
      />
      <FloatingPreviewWindow
        open={floatingPreviewOpen}
        preview={preview}
        warnings={warnings}
        hasDirtyScripts={hasAnyDirtyScript}
        onClose={() => setFloatingPreviewOpen(false)}
      />
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
        onClose={() => setSettingsOpen(false)}
        onCompilerSettingsChange={setCompilerSettings}
        onOpenConfig={() => void openConfig()}
        onTestLlmConnection={testLlmConnection}
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
        onLoadMemoryLessons={loadMemoryLessons}
        onSummarizeProjectMemory={summarizeProjectMemory}
        onUpdateMemoryLesson={updateMemoryLesson}
        onDeleteMemoryLesson={deleteMemoryLesson}
        onIgnoreMemoryLesson={ignoreMemoryLesson}
        onClearProjectMemory={clearProjectMemory}
      />
    </main>
  )
}
