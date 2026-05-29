import { useEffect, useState } from 'react'
import { AssistantPanel } from '../components/AssistantPanel'
import { BottomDrawer } from '../components/BottomDrawer'
import { ParameterRail } from '../components/ParameterRail'
import { PreviewViewport } from '../components/PreviewViewport'
import { ScriptEditor } from '../components/ScriptEditor'
import { ScriptTree } from '../components/ScriptTree'
import { TopMenu } from '../components/TopMenu'
import { groupParameters } from '../state/parameterGroups'
import { useWorkbenchStore } from '../state/useWorkbenchStore'
import { RevisionPanel } from './diagnostics/RevisionPanel'
import { FloatingPreviewWindow } from './preview/FloatingPreviewWindow'
import { ProjectOpenControls } from './project/ProjectOpenControls'
import { SettingsDrawer } from './settings/SettingsDrawer'

export function WorkbenchApp() {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [floatingPreviewOpen, setFloatingPreviewOpen] = useState(false)
  const project = useWorkbenchStore((state) => state.project)
  const parameters = useWorkbenchStore((state) => state.parameters)
  const draftParameters = useWorkbenchStore((state) => state.draftParameters)
  const preview = useWorkbenchStore((state) => state.preview)
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
  const latestRevisionId = useWorkbenchStore((state) => state.latestRevisionId)
  const revisionLoading = useWorkbenchStore((state) => state.revisionLoading)
  const activeScriptName = useWorkbenchStore((state) => state.activeScriptName)
  const scriptContents = useWorkbenchStore((state) => state.scriptContents)
  const dirtyScripts = useWorkbenchStore((state) => state.dirtyScripts)
  const scriptSaving = useWorkbenchStore((state) => state.scriptSaving)
  const mockCompileResult = useWorkbenchStore((state) => state.mockCompileResult)
  const load = useWorkbenchStore((state) => state.load)
  const setDraftParameter = useWorkbenchStore((state) => state.setDraftParameter)
  const applyDraftParameters = useWorkbenchStore((state) => state.applyDraftParameters)
  const loadProjectPath = useWorkbenchStore((state) => state.loadProjectPath)
  const importGdlFile = useWorkbenchStore((state) => state.importGdlFile)
  const closeProject = useWorkbenchStore((state) => state.closeProject)
  const browseProjectDirectory = useWorkbenchStore((state) => state.browseProjectDirectory)
  const setCompilerSettings = useWorkbenchStore((state) => state.setCompilerSettings)
  const setLlmSettings = useWorkbenchStore((state) => state.setLlmSettings)
  const reloadRuntimeSettings = useWorkbenchStore((state) => state.reloadRuntimeSettings)
  const browseCompilerFile = useWorkbenchStore((state) => state.browseCompilerFile)
  const compileCurrentProject = useWorkbenchStore((state) => state.compileCurrentProject)
  const runMockCompile = useWorkbenchStore((state) => state.runMockCompile)
  const setActiveRailPanel = useWorkbenchStore((state) => state.setActiveRailPanel)
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

  useEffect(() => {
    void load()
  }, [load])

  return (
    <main className="app-shell">
      <TopMenu
        project={project}
        projectControls={
          <ProjectOpenControls
            project={project}
            loading={loading}
            onLoadProjectPath={(path) => void loadProjectPath(path)}
            onBrowseProjectDirectory={() => void browseProjectDirectory()}
            onImportGdlFile={() => void importGdlFile()}
          />
        }
        hasDraftChanges={hasDraftChanges()}
        onApply={() => void applyDraftParameters()}
        onCompile={() => void compileCurrentProject()}
        onMockCompile={() => void runMockCompile()}
        onSave={() => void saveActiveScript()}
        onOpenSettings={() => setSettingsOpen(true)}
        applying={applying}
        loading={loading}
        compiling={compiling}
        saving={scriptSaving}
        hasDirtyScript={hasDirtyScript}
        activeScriptName={activeScriptName}
        lastError={lastError}
        onClearError={clearLastError}
      />
      <section className="workspace-grid" aria-busy={loading}>
        <aside className="left-rail">
          <ScriptTree scripts={scripts} activeScript={activeScriptName} dirtyScripts={dirtyScripts} onSelect={(name) => void openScript(name)} />
          <ParameterRail
            title="参数"
            sections={[
              { title: '尺寸', parameters: grouped.dimensions },
              { title: '属性', parameters: grouped.properties },
            ]}
            draftParameters={draftParameters}
            onChange={(name, value) => void setDraftParameter(name, value)}
          />
        </aside>
        <section className="workbench-main-stage editor-stage">
          {activeScriptName ? (
            <ScriptEditor
              scriptName={activeScriptName}
              content={activeScriptContent}
              onChange={updateActiveScriptContent}
              isDirty={hasDirtyScript}
            />
          ) : (
            <div className="editor-empty">No script loaded</div>
          )}
        </section>
        <aside className="workbench-right-rail right-rail">
          <div className="rail-tabs" role="tablist" aria-label="Right rail panels">
            <button
              type="button"
              className={`rail-tab${activeRailPanel === '3d' ? ' active' : ''}`}
              aria-selected={activeRailPanel === '3d'}
              onClick={() => setActiveRailPanel('3d')}
            >
              3D
            </button>
            <button type="button" className="rail-tab" disabled aria-selected="false">
              2D
            </button>
            <button
              type="button"
              className={`rail-tab${activeRailPanel === 'ai' ? ' active' : ''}`}
              aria-selected={activeRailPanel === 'ai'}
              onClick={() => setActiveRailPanel('ai')}
            >
              AI
            </button>
          </div>
          <div className="rail-panel viewport-panel">
            {activeRailPanel === '3d' ? (
              <PreviewViewport
                preview={preview}
                warnings={warnings}
                actions={
                  <button type="button" className="viewport-action-button" onClick={() => setFloatingPreviewOpen(true)}>
                    浮窗
                  </button>
                }
              />
            ) : (
              <AssistantPanel
                messages={assistantMessages}
                busy={assistantBusy}
                onSend={(message) => void sendAssistantMessage(message)}
                onCreate={(message) => void createProjectFromPrompt(message)}
                onGenerate={(message) => void generateAssistantChanges(message)}
              />
            )}
          </div>
        </aside>
      </section>
      <BottomDrawer
        warnings={warnings}
        compileLog={compileLog}
        mockCompileResult={mockCompileResult}
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
        onClose={() => setFloatingPreviewOpen(false)}
      />
      <SettingsDrawer
        open={settingsOpen}
        compilerSettings={compilerSettings}
        llmSettings={llmSettings}
        recentProjects={recentProjects}
        onClose={() => setSettingsOpen(false)}
        onCompilerSettingsChange={(settings) => void setCompilerSettings(settings)}
        onLlmSettingsChange={(settings) => void setLlmSettings(settings)}
        onReloadRuntimeSettings={() => void reloadRuntimeSettings()}
        onBrowseCompilerFile={() => void browseCompilerFile()}
        onOpenProjectPath={(path) => void loadProjectPath(path)}
        onCloseProject={() => void closeProject()}
      />
    </main>
  )
}
