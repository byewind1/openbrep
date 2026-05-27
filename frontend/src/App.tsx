import { useEffect } from 'react'
import { AssistantPanel } from './components/AssistantPanel'
import { BottomDrawer } from './components/BottomDrawer'
import { ParameterRail } from './components/ParameterRail'
import { PreviewViewport } from './components/PreviewViewport'
import { ScriptEditor } from './components/ScriptEditor'
import { ScriptTree } from './components/ScriptTree'
import { TopMenu } from './components/TopMenu'
import { groupParameters } from './state/parameterGroups'
import { useWorkbenchStore } from './state/useWorkbenchStore'

export default function App() {
  const project = useWorkbenchStore((state) => state.project)
  const parameters = useWorkbenchStore((state) => state.parameters)
  const draftParameters = useWorkbenchStore((state) => state.draftParameters)
  const preview = useWorkbenchStore((state) => state.preview)
  const warnings = useWorkbenchStore((state) => state.warnings)
  const loading = useWorkbenchStore((state) => state.loading)
  const applying = useWorkbenchStore((state) => state.applying)
  const compiling = useWorkbenchStore((state) => state.compiling)
  const compileLog = useWorkbenchStore((state) => state.compileLog)
  const compilerSettings = useWorkbenchStore((state) => state.compilerSettings)
  const assistantBusy = useWorkbenchStore((state) => state.assistantBusy)
  const assistantMessages = useWorkbenchStore((state) => state.assistantMessages)
  const scripts = useWorkbenchStore((state) => state.scripts)
  const activeScriptName = useWorkbenchStore((state) => state.activeScriptName)
  const scriptContents = useWorkbenchStore((state) => state.scriptContents)
  const dirtyScripts = useWorkbenchStore((state) => state.dirtyScripts)
  const scriptSaving = useWorkbenchStore((state) => state.scriptSaving)
  const mockCompileResult = useWorkbenchStore((state) => state.mockCompileResult)
  const load = useWorkbenchStore((state) => state.load)
  const setDraftParameter = useWorkbenchStore((state) => state.setDraftParameter)
  const applyDraftParameters = useWorkbenchStore((state) => state.applyDraftParameters)
  const loadProjectPath = useWorkbenchStore((state) => state.loadProjectPath)
  const browseProjectDirectory = useWorkbenchStore((state) => state.browseProjectDirectory)
  const setCompilerSettings = useWorkbenchStore((state) => state.setCompilerSettings)
  const browseCompilerFile = useWorkbenchStore((state) => state.browseCompilerFile)
  const runMockCompile = useWorkbenchStore((state) => state.runMockCompile)
  const sendAssistantMessage = useWorkbenchStore((state) => state.sendAssistantMessage)
  const generateAssistantChanges = useWorkbenchStore((state) => state.generateAssistantChanges)
  const openScript = useWorkbenchStore((state) => state.openScript)
  const updateActiveScriptContent = useWorkbenchStore((state) => state.updateActiveScriptContent)
  const saveActiveScript = useWorkbenchStore((state) => state.saveActiveScript)
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
        hasDraftChanges={hasDraftChanges()}
        onApply={() => void applyDraftParameters()}
        onLoadProjectPath={(path) => void loadProjectPath(path)}
        onBrowseProjectDirectory={() => void browseProjectDirectory()}
        onCompile={() => void runMockCompile()}
        onSave={() => void saveActiveScript()}
        compilerSettings={compilerSettings}
        onCompilerSettingsChange={(settings) => void setCompilerSettings(settings)}
        onBrowseCompilerFile={() => void browseCompilerFile()}
        applying={applying}
        loading={loading}
        compiling={compiling}
        saving={scriptSaving}
        hasDirtyScript={hasDirtyScript}
        activeScriptName={activeScriptName}
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
        <section className="editor-stage">
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
        <aside className="right-rail">
          <div className="right-rail-preview">
            <PreviewViewport preview={preview} warnings={warnings} />
          </div>
          <div className="right-rail-tabs">
            <button className="active">AI</button>
            <button>2D</button>
          </div>
          <div className="right-rail-panel">
            <AssistantPanel
              messages={assistantMessages}
              busy={assistantBusy}
              onSend={(message) => void sendAssistantMessage(message)}
              onGenerate={(message) => void generateAssistantChanges(message)}
            />
          </div>
        </aside>
      </section>
      <BottomDrawer warnings={warnings} compileLog={compileLog} mockCompileResult={mockCompileResult} />
    </main>
  )
}
