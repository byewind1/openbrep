import { useEffect } from 'react'
import { AssistantPanel } from './components/AssistantPanel'
import { BottomDrawer } from './components/BottomDrawer'
import { ParameterRail } from './components/ParameterRail'
import { PreviewViewport } from './components/PreviewViewport'
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
  const load = useWorkbenchStore((state) => state.load)
  const setDraftParameter = useWorkbenchStore((state) => state.setDraftParameter)
  const applyDraftParameters = useWorkbenchStore((state) => state.applyDraftParameters)
  const hasDraftChanges = useWorkbenchStore((state) => state.hasDraftChanges)
  const grouped = groupParameters(parameters)

  useEffect(() => {
    void load()
  }, [load])

  return (
    <main className="app-shell">
      <TopMenu project={project} hasDraftChanges={hasDraftChanges()} onApply={() => void applyDraftParameters()} applying={applying} />
      <section className="workspace-grid" aria-busy={loading}>
        <ParameterRail
          title="尺寸参数"
          parameters={grouped.dimensions}
          draftParameters={draftParameters}
          onChange={(name, value) => void setDraftParameter(name, value)}
        />
        <PreviewViewport preview={preview} warnings={warnings} />
        <ParameterRail
          title="数量 / 属性"
          parameters={grouped.properties}
          draftParameters={draftParameters}
          onChange={(name, value) => void setDraftParameter(name, value)}
        />
        <AssistantPanel />
      </section>
      <BottomDrawer warnings={warnings} />
    </main>
  )
}
